"""
run.py  --  command-line interface for the paper-trading +EV scanner.

Usage
-----
  python run.py sports                 list in-season sports + their API keys
  python run.py scan                   scan configured sports, show +EV bets
  python run.py scan --place           ...and log them to the paper ledger
  python run.py scan --sport soccer_epl
  python run.py report                 show ledger stats (ROI, CLV, bankroll)
  python run.py open                   list open (unsettled) paper bets
  python run.py close <id> <odds>      record sharp closing price -> CLV
  python run.py settle <id> won|lost|void

All bets are PAPER. No real money. Prove the edge first.
"""
from __future__ import annotations
import argparse
import sys

import config
import ledger
import scanner
from odds_api import (fetch_odds, fetch_scores, list_sports,
                      extract_market, OddsAPIError)


def _check_key() -> bool:
    if config.API_KEY in ("", "PASTE_YOUR_KEY_HERE"):
        print("!! No API key. Get a free one at https://the-odds-api.com")
        print("   then:  $env:THE_ODDS_API_KEY = \"your_key\"   (PowerShell)")
        return False
    return True


def cmd_sports(_):
    if not _check_key():
        return
    for s in list_sports():
        if s.get("active"):
            print(f'  {s["key"]:34s}  {s["title"]}')


def cmd_scan(args):
    if not _check_key():
        return
    sports = [args.sport] if args.sport else config.SPORTS
    bankroll = ledger.stats()["bankroll"]
    all_opps, last_quota = [], None

    for sport in sports:
        try:
            events, quota = fetch_odds(sport)
            last_quota = quota
        except OddsAPIError as e:
            print(f"  [{sport}] {e}")
            continue
        opps = scanner.scan_all(events, bankroll)
        all_opps.extend(opps)
        print(f"  [{sport}] {len(events)} events -> {len(opps)} value bets")

    if last_quota:
        print(f"\n  API quota: {last_quota['remaining']} requests remaining "
              f"({last_quota['used']} used)\n")

    if not all_opps:
        print("No +EV opportunities right now. (Normal -- sharp markets are tight.)")
        return

    all_opps.sort(key=lambda r: r["ev_pct"], reverse=True)
    print(f"{'EV%':>6} {'sel':<20} {'book':<13} {'odds':>6} {'fair':>6} "
          f"{'#bk':>3} {'stake':>7}  event")
    print("  " + "-" * 92)
    for o in all_opps[:40]:
        fair = 1.0 / o["true_prob"] if o["true_prob"] else 0
        print(f'{o["ev_pct"]:>6.2f} {o["selection"][:20]:<20} '
              f'{o["soft_book"][:13]:<13} {o["soft_odds"]:>6.2f} {fair:>6.2f} '
              f'{o["books_agree"]:>3} {o["stake"]:>7.2f}  {o["event"]}')

    if args.place:
        n = ledger.place_bets(all_opps)
        print(f"\n  logged {n} new paper bet(s) to {config.LEDGER_PATH}")


def cmd_report(_):
    s = ledger.stats()
    print("\n  ===== PAPER LEDGER =====")
    print(f"  Bankroll        : {s['bankroll']:.2f}  (started {config.STARTING_BANKROLL:.0f})")
    print(f"  Bets settled    : {s['settled']}")
    print(f"  Bets open       : {s['open']}")
    if s["settled"]:
        print(f"  Total staked    : {s['staked']:.2f}")
        print(f"  Profit          : {s['profit']:+.2f}")
        print(f"  ROI             : {s['roi_pct']:+.2f}%")
        print(f"  Win rate        : {s['win_rate']:.1f}%")
        print(f"  Avg EV at bet   : {s['avg_ev']:+.2f}%")
        clv = s["avg_clv"]
        clv_str = f"{clv:+.2f}% (n={s['clv_n']})" if clv is not None else "no data yet"
        print(f"  Avg CLV         : {clv_str}   <-- the metric that matters")
    print()
    if s["settled"] >= 100 and s.get("avg_clv") is not None:
        verdict = "REAL EDGE" if s["avg_clv"] > 0 else "NO EDGE -- do not bet real money"
        print(f"  Verdict after {s['settled']} bets: {verdict}\n")

    # ── settled history (newest first) ──
    df = ledger.load()
    hist = df[df["status"].isin(["won", "lost", "void"])].copy()
    if hist.empty:
        return
    hist = hist.sort_values("commence_time", ascending=False)
    print("  ----- SETTLED HISTORY -----")
    print(f"  {'#':<5}{'result':<7}{'P/L':>8}  {'pick':<20}{'odds':>6}"
          f"{'EV%':>7}{'CLV%':>7}  event")
    for _, r in hist.iterrows():
        clv = f"{float(r['clv_pct']):+.1f}" if str(r["clv_pct"]) not in ("", "nan") else "--"
        profit = float(r["profit"]) if str(r["profit"]) not in ("", "nan") else 0.0
        print(f'  #{int(r["bet_id"]):<4}{r["status"]:<7}{profit:>+8.2f}  '
              f'{str(r["selection"])[:20]:<20}{r["soft_odds"]:>6}'
              f'{r["ev_pct"]:>7}{clv:>7}  {r["event"]}')
    print()


def _winner_from_scores(game: dict):
    """Return winning team name, 'Draw', or None if not finished/parseable."""
    raw = game.get("scores") or []
    scores = {s["name"]: float(s["score"]) for s in raw
              if s.get("score") not in (None, "")}
    h, a = game.get("home_team"), game.get("away_team")
    if h not in scores or a not in scores:
        return None
    if scores[h] > scores[a]:
        return h
    if scores[a] > scores[h]:
        return a
    return "Draw"


def cmd_autosettle(_):
    """Pull final scores from the API and settle matching open bets."""
    if not _check_key():
        return
    df = ledger.load()
    open_bets = df[df["status"] == "open"]
    if open_bets.empty:
        print("No open bets to settle.")
        return

    settled = 0
    for sport in sorted(open_bets["sport"].unique()):
        try:
            games, _ = fetch_scores(sport)
        except OddsAPIError as e:
            print(f"  [{sport}] {e}")
            continue
        # winner lookup keyed by (home, away)
        winners = {}
        for g in games:
            if g.get("completed"):
                w = _winner_from_scores(g)
                if w:
                    winners[(g.get("home_team"), g.get("away_team"))] = w

        for _, bet in open_bets[open_bets["sport"] == sport].iterrows():
            w = winners.get((bet["home_team"], bet["away_team"]))
            if w is None:
                continue                      # not finished yet
            result = "won" if str(bet["selection"]) == w else "lost"
            if ledger.settle(int(bet["bet_id"]), result):
                settled += 1
                stake = float(bet["stake"])
                pl = stake * (float(bet["soft_odds"]) - 1) if result == "won" else -stake
                print(f'  #{int(bet["bet_id"])} {bet["selection"]} -> {result} '
                      f'(staked {stake:.2f} | P/L {pl:+.2f})  | {bet["event"]}')
    print(f"\n  auto-settled {settled} bet(s).")


def cmd_closeall(_):
    """Capture the sharp book's CURRENT price for every open bet -> CLV.
    Run this shortly before kickoff: 'current' then == the closing line."""
    if not _check_key():
        return
    from datetime import datetime, timezone, timedelta
    import pandas as pd

    df = ledger.load()
    # empty CSV cells read back as NaN, not "" — accept both as "not captured yet"
    closing = df["closing_odds"].astype(str).str.strip().str.lower()
    open_bets = df[(df["status"] == "open") & closing.isin(["", "nan", "none"])].copy()

    # only matches near kickoff: capturing days early would record a price that
    # is NOT the closing line and poison the CLV stat.
    now = datetime.now(timezone.utc)
    kick = pd.to_datetime(open_bets["commence_time"], utc=True, errors="coerce")
    near = (kick > now - timedelta(minutes=30)) & (kick < now + timedelta(minutes=45))
    open_bets = open_bets[near]
    if open_bets.empty:
        print("No open bets kicking off within the next 45 minutes.")
        return

    captured = 0
    for sport in sorted(open_bets["sport"].unique()):
        try:
            events, _ = fetch_odds(sport)
        except OddsAPIError as e:
            print(f"  [{sport}] {e}")
            continue
        # map (home, away) -> sharp current odds dict
        sharp_now = {}
        for ev in events:
            books = {b["key"]: b for b in ev.get("bookmakers", [])}
            sharp = books.get(config.SHARP_BOOK)
            if sharp:
                sharp_now[(ev.get("home_team"), ev.get("away_team"))] = \
                    extract_market(sharp, config.MARKET)

        for _, bet in open_bets[open_bets["sport"] == sport].iterrows():
            odds = sharp_now.get((bet["home_team"], bet["away_team"]), {})
            price = odds.get(bet["selection"])
            if price and ledger.capture_closing(int(bet["bet_id"]), float(price)):
                captured += 1
                clv = float(bet["soft_odds"]) / float(price) - 1.0
                tag = "BEAT the close" if clv > 0 else "behind the close"
                print(f'  #{int(bet["bet_id"]):<4} {bet["selection"][:20]:<20} '
                      f'took {bet["soft_odds"]} | closed {price:.2f} | '
                      f'CLV {clv*100:+.2f}%  {tag}')
    print(f"\n  captured closing odds for {captured} bet(s).")


def cmd_open(_):
    df = ledger.load()
    op = df[df["status"] == "open"]
    if op.empty:
        print("No open bets.")
        return
    for _, r in op.iterrows():
        print(f'  #{int(r["bet_id"]):<4} {r["ev_pct"]:>5}%  {r["selection"][:22]:<22} '
              f'@ {r["soft_odds"]} ({r["soft_book"]})  stake {r["stake"]}  | {r["event"]}')


def cmd_upcoming(_):
    """Show open bets sorted by kickoff time with action instructions."""
    from datetime import datetime, timezone, timedelta
    import pandas as pd

    df = ledger.load()
    op = df[df["status"] == "open"].copy()
    if op.empty:
        print("No open bets.")
        return

    now = datetime.now(timezone.utc)
    op["_dt"] = pd.to_datetime(op["commence_time"], utc=True)
    op = op.sort_values("_dt")

    past, soon, future = [], [], []
    for _, r in op.iterrows():
        dt = r["_dt"]
        mins = (dt - now).total_seconds() / 60
        if mins < -120:        # finished >2h ago
            past.append((r, mins))
        elif mins < 30:        # kickoff in <30 min (or just started)
            soon.append((r, mins))
        else:
            future.append((r, mins))

    def _row(r, mins):
        kicked = r["_dt"].strftime("%b %d  %H:%M UTC")
        return (f'  #{int(r["bet_id"]):<4} {kicked}  {r["selection"][:20]:<20} '
                f'@ {r["soft_odds"]}  | {r["event"]}')

    if past:
        print("\n  !! PAST MATCHES -- run: python run.py autosettle")
        for r, m in past:
            print(_row(r, m))

    if soon:
        print("\n  !! KICKING OFF SOON -- run closeall NOW, then autosettle after the match")
        for r, m in soon:
            label = f"({abs(int(m))}min ago)" if m < 0 else f"(in {int(m)}min)"
            print(f"  {label:<14}" + _row(r, m).lstrip())

    if future:
        print("\n  UPCOMING -- run closeall 30min before each kickoff:")
        prev_day = None
        for r, m in future:
            day = r["_dt"].strftime("%A %b %d")
            if day != prev_day:
                print(f"\n    -- {day} --")
                prev_day = day
            time_str = r["_dt"].strftime("%H:%M UTC")
            print(f'    {time_str}  #{int(r["bet_id"]):<4} {r["selection"][:20]:<20} '
                  f'@ {r["soft_odds"]}  | {r["event"]}')
    print()


def cmd_close(args):
    ok = ledger.capture_closing(args.bet_id, args.closing_odds)
    print("recorded closing odds + CLV" if ok else f"bet #{args.bet_id} not found")


def cmd_settle(args):
    ok = ledger.settle(args.bet_id, args.result)
    print(f"bet #{args.bet_id} settled: {args.result}" if ok
          else f"bet #{args.bet_id} not found")


def main():
    p = argparse.ArgumentParser(description="Paper-trading +EV sports betting scanner")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sports").set_defaults(func=cmd_sports)

    sc = sub.add_parser("scan")
    sc.add_argument("--sport", help="single sport key (default: config.SPORTS)")
    sc.add_argument("--place", action="store_true", help="log results to ledger")
    sc.set_defaults(func=cmd_scan)

    sub.add_parser("report").set_defaults(func=cmd_report)
    sub.add_parser("open").set_defaults(func=cmd_open)
    sub.add_parser("upcoming").set_defaults(func=cmd_upcoming)
    sub.add_parser("autosettle").set_defaults(func=cmd_autosettle)
    sub.add_parser("closeall").set_defaults(func=cmd_closeall)

    cl = sub.add_parser("close")
    cl.add_argument("bet_id", type=int)
    cl.add_argument("closing_odds", type=float)
    cl.set_defaults(func=cmd_close)

    st = sub.add_parser("settle")
    st.add_argument("bet_id", type=int)
    st.add_argument("result", choices=["won", "lost", "void"])
    st.set_defaults(func=cmd_settle)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())

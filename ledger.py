"""
ledger.py  --  the paper-trading bankroll.

Every bet we "place" is logged to a CSV. No real money moves. This is how we
PROVE an edge before risking anything: log hundreds of bets, settle them with
real results, and watch the two numbers that matter:

   ROI   -- profit / total staked (the headline, but noisy)
   CLV   -- did we get better odds than the sharp closing line?  (the truth)

CLV is the leading indicator. Positive CLV over 100+ bets = real edge, even if
ROI is temporarily negative from variance.
"""
from __future__ import annotations
from pathlib import Path

import pandas as pd

import config

COLUMNS = [
    "bet_id", "scanned_at", "commence_time", "sport", "event",
    "home_team", "away_team", "selection",
    "soft_book", "soft_odds", "sharp_odds", "true_prob", "ev_pct",
    "books_agree", "stake",
    "status",            # open | won | lost | void
    "closing_odds",      # sharp book's price near kickoff (for CLV)
    "clv_pct",           # soft_odds / closing_odds - 1
    "profit",            # settled P/L in units
]


def _path() -> Path:
    return Path(__file__).parent / config.LEDGER_PATH


def load() -> pd.DataFrame:
    p = _path()
    if p.exists():
        return pd.read_csv(p)
    return pd.DataFrame(columns=COLUMNS)


def save(df: pd.DataFrame) -> None:
    df.to_csv(_path(), index=False)


def _next_id(df: pd.DataFrame) -> int:
    return 1 if df.empty else int(df["bet_id"].max()) + 1


def place_bets(opportunities: list[dict]) -> int:
    """
    Log a list of scanner opportunities as OPEN paper bets.
    De-dupes: one bet per match maximum (highest EV), and skips matches that
    already have an open bet. Returns the number of new bets added.
    """
    # collapse to ONE bet per (event, selection): the best available price.
    # In real life you place a single bet at the book with the highest odds.
    best: dict[tuple, dict] = {}
    for o in opportunities:
        k = (o["event"], o["selection"])
        if k not in best or o["soft_odds"] > best[k]["soft_odds"]:
            best[k] = o

    # then ONE bet per match: when several outcomes of the same match show
    # value (e.g. home win AND draw), keep only the highest-EV one. The
    # outcomes are mutually exclusive, so betting two of them guarantees at
    # least one loss -- and a match flagging multiple "value" outcomes usually
    # means the sharp line just moved (or the devig is noisy), not free money.
    per_event: dict[str, dict] = {}
    for o in best.values():
        k = o["event"]
        if k not in per_event or o["ev_pct"] > per_event[k]["ev_pct"]:
            per_event[k] = o
    opportunities = list(per_event.values())

    df = load()
    # de-dupe against matches that already have an open bet (any selection,
    # any book) -- one bet per match, full stop.
    open_keys = set(
        df.loc[df["status"] == "open", "event"]
    ) if not df.empty else set()

    rows, next_id = [], _next_id(df)
    for o in opportunities:
        if o["stake"] < config.MIN_STAKE:
            continue
        key = o["event"]
        if key in open_keys:
            continue
        rows.append({
            "bet_id": next_id, **o,
            "status": "open", "closing_odds": "", "clv_pct": "", "profit": "",
        })
        open_keys.add(key)
        next_id += 1

    if rows:
        new = pd.DataFrame(rows, columns=COLUMNS)
        df = new if df.empty else pd.concat([df, new], ignore_index=True)
        save(df)
    return len(rows)


def capture_closing(bet_id: int, closing_odds: float) -> bool:
    """
    Record the sharp book's price near kickoff for an open bet, and compute CLV.
    Run this shortly before a match starts (the closing line is the sharpest).
    """
    df = load()
    mask = df["bet_id"] == bet_id
    if not mask.any():
        return False
    soft = float(df.loc[mask, "soft_odds"].iloc[0])
    clv = soft / closing_odds - 1.0
    df.loc[mask, "closing_odds"] = round(closing_odds, 3)
    df.loc[mask, "clv_pct"] = round(clv * 100, 2)
    save(df)
    return True


def settle(bet_id: int, result: str) -> bool:
    """
    Settle an open bet. result in {"won", "lost", "void"}.
    Profit = stake*(odds-1) on a win, -stake on a loss, 0 on void.
    """
    df = load()
    mask = df["bet_id"] == bet_id
    if not mask.any():
        return False
    stake = float(df.loc[mask, "stake"].iloc[0])
    odds = float(df.loc[mask, "soft_odds"].iloc[0])
    if result == "won":
        profit = stake * (odds - 1.0)
    elif result == "lost":
        profit = -stake
    else:
        result, profit = "void", 0.0
    df.loc[mask, "status"] = result
    df.loc[mask, "profit"] = round(profit, 2)
    save(df)
    return True


def stats() -> dict:
    """Summary of the whole ledger -- the report you actually care about."""
    df = load()
    settled = df[df["status"].isin(["won", "lost", "void"])].copy()
    open_n = int((df["status"] == "open").sum())

    if settled.empty:
        return {"settled": 0, "open": open_n,
                "bankroll": config.STARTING_BANKROLL}

    settled["profit"] = pd.to_numeric(settled["profit"], errors="coerce")
    settled["stake"] = pd.to_numeric(settled["stake"], errors="coerce")
    staked = settled["stake"].sum()
    profit = settled["profit"].sum()
    wins = int((settled["status"] == "won").sum())
    decided = int(settled["status"].isin(["won", "lost"]).sum())

    clv = pd.to_numeric(settled["clv_pct"], errors="coerce").dropna()

    return {
        "settled":   len(settled),
        "open":      open_n,
        "staked":    round(staked, 2),
        "profit":    round(profit, 2),
        "roi_pct":   round(100 * profit / staked, 2) if staked else 0.0,
        "win_rate":  round(100 * wins / decided, 1) if decided else 0.0,
        "avg_ev":    round(pd.to_numeric(settled["ev_pct"], errors="coerce").mean(), 2),
        "avg_clv":   round(clv.mean(), 2) if not clv.empty else None,
        "clv_n":     int(clv.shape[0]),
        "bankroll":  round(config.STARTING_BANKROLL + profit, 2),
    }

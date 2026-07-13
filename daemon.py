"""
daemon.py — fully automated runner.

Start it once and leave the window open:
    python daemon.py

What it does, forever:
  - On startup and every morning at 08:00: report + scan --place + autosettle
    (autosettle catches anything that finished while the daemon was off)
  - 30 min before every kickoff in the ledger  → closeall   (captures CLV)
  - ~2h 10min after every kickoff              → autosettle (settles the bet)
  - The schedule is rebuilt after every action, so newly scanned bets are
    picked up automatically.

Safe to stop (Ctrl+C) and restart anytime: everything is recalculated from
paper_bets.csv on startup, and missed settlements are caught by the morning
autosettle (the scores API looks back 3 days).
"""
from __future__ import annotations
import sys, time, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

BASE   = Path(__file__).parent
PYTHON = sys.executable
RUN_PY = str(BASE / "run.py")

MORNING_HOUR  = 8    # local time for the daily scan
CLOSE_BEFORE  = 30   # minutes before kickoff → closeall
SETTLE_AFTER  = 130  # minutes after kickoff  → autosettle (90min + stoppage)


# ── helpers ──────────────────────────────────────────────────────────────────

def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run(*args):
    print(f"\n[{_ts()}]  >> python run.py {' '.join(args)}")
    print("  " + "-" * 56)
    subprocess.run([PYTHON, RUN_PY] + list(args), cwd=BASE)
    print("  " + "-" * 56)


def _sleep_until(target_utc: datetime, label: str):
    """Sleep until target_utc, printing a heartbeat every 30 min."""
    while True:
        left = (target_utc - datetime.now(timezone.utc)).total_seconds()
        if left <= 5:
            return
        time.sleep(min(left, 1800))
        left = (target_utc - datetime.now(timezone.utc)).total_seconds()
        if left > 120:
            h, m = divmod(int(left // 60), 60)
            hm = f"{h}h {m}min" if h else f"{m}min"
            print(f"  [{_ts()}]  waiting {hm} → {label}")


def _next_morning_utc() -> datetime:
    """Tomorrow at MORNING_HOUR local time, as UTC."""
    nxt = (datetime.now() + timedelta(days=1)).replace(
        hour=MORNING_HOUR, minute=0, second=0, microsecond=0)
    return nxt.astimezone(timezone.utc)


def _build_schedule() -> list[tuple[datetime, str, str]]:
    """(fire_time_utc, command, description) for every open bet's match."""
    sys.path.insert(0, str(BASE))
    import ledger
    df = ledger.load()
    open_bets = df[df["status"] == "open"].copy()
    if open_bets.empty:
        return []

    open_bets["_dt"] = pd.to_datetime(open_bets["commence_time"], utc=True, errors="coerce")

    seen, schedule = set(), []
    for _, row in open_bets.iterrows():
        dt = row["_dt"]
        if pd.isna(dt):
            continue
        dt = dt.to_pydatetime()   # plain datetime — pandas Timestamp breaks astimezone()
        key = f"{row['home_team']} vs {row['away_team']}"
        if key in seen:
            continue
        seen.add(key)
        schedule.append((dt - timedelta(minutes=CLOSE_BEFORE),  "closeall",   f"closeall   — {key}"))
        schedule.append((dt + timedelta(minutes=SETTLE_AFTER),  "autosettle", f"autosettle — {key}"))

    schedule.sort(key=lambda x: x[0])
    return schedule


def _morning_routine():
    """Report on yesterday, scan for today's value, settle anything missed."""
    print(f"\n{'='*60}")
    print(f"  MORNING ROUTINE  —  {datetime.now().strftime('%A %d %B %Y')}")
    print(f"{'='*60}")
    _run("report")
    _run("scan", "--place")
    _run("autosettle")


def _print_today(schedule):
    now = datetime.now(timezone.utc)
    today = [x for x in schedule
             if now < x[0] < now + timedelta(hours=24)]
    if today:
        print(f"\n  Next 24h ({len(today)} actions):")
        for t, _, desc in today:
            print(f"    {t.astimezone().strftime('%d %b %H:%M')}  {desc}")
    else:
        print("\n  Nothing scheduled in the next 24h.")


# ── main loop ────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  EV Scanner daemon — started")
    print(f"  Morning routine daily at {MORNING_HOUR:02d}:00 local")
    print("  Ctrl+C to stop (safe — everything rebuilds on restart)")
    print("=" * 60)

    next_scan = datetime.now(timezone.utc)          # scan immediately on start

    while True:
        now = datetime.now(timezone.utc)

        # 1. morning routine when due
        if now >= next_scan:
            _morning_routine()
            next_scan = _next_morning_utc()
            print(f"\n  Next morning routine: "
                  f"{next_scan.astimezone().strftime('%A %d %b %H:%M')}")

        # 2. rebuild schedule (picks up bets the scan just added)
        schedule = [x for x in _build_schedule()
                    if x[0] > datetime.now(timezone.utc)]
        _print_today(schedule)

        # 3. what comes first: next match action or next morning scan?
        if schedule and schedule[0][0] < next_scan:
            fire_t, _, label = schedule[0]
        else:
            fire_t, label = next_scan, "morning routine"

        _sleep_until(fire_t, label)

        # 4. run everything that is now due (deduped: one closeall covers
        #    several matches kicking off at the same time)
        now = datetime.now(timezone.utc)
        due_cmds = []
        for t, cmd, desc in schedule:
            if t <= now + timedelta(seconds=30):
                print(f"\n[{_ts()}]  ACTION: {desc}")
                if cmd not in due_cmds:
                    due_cmds.append(cmd)
        for cmd in due_cmds:
            _run(cmd)
        # loop: morning routine fires at the top when its time comes


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nDaemon stopped. Restart anytime with: python daemon.py")
        sys.exit(0)

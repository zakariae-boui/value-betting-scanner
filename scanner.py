"""
scanner.py  --  the core engine.

For every event:
  1. Take the SHARP book's odds (Pinnacle) and de-vig them -> true probability.
  2. For every SOFT book and every outcome, compute expected value:
         EV%  =  soft_odds * true_prob  -  1
     If a soft book pays MORE than the true odds, EV is positive: a value bet.
  3. Recommend a stake via fractional Kelly, capped at MAX_STAKE_PCT.

This is the whole strategy: we never predict the winner. We trust the sharp
market's probability and pounce when a lazy soft book misprices it.
"""
from __future__ import annotations
from datetime import datetime, timezone

import config
import devig
from odds_api import extract_market


def kelly_stake(true_prob: float, odds: float, bankroll: float) -> float:
    """
    Fractional Kelly stake.
        b = odds - 1  (net fractional win)
        f* = (b*p - q) / b      where q = 1 - p
    Scaled by KELLY_FRACTION, capped by MAX_STAKE_PCT, floored at 0.
    """
    b = odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - true_prob
    f_star = (b * true_prob - q) / b
    f = max(0.0, f_star) * config.KELLY_FRACTION
    stake = bankroll * min(f, config.MAX_STAKE_PCT)
    if odds < config.SHORT_ODDS_THRESHOLD:
        stake = min(stake, config.MAX_STAKE_SHORT)
    return round(stake, 2)


def _is_soft(book_key: str) -> bool:
    if book_key == config.SHARP_BOOK:
        return False
    if not config.SOFT_BOOKS:        # empty = treat all non-sharp books as soft
        return True
    return book_key in config.SOFT_BOOKS


def scan_event(event: dict, bankroll: float, method: str = "power") -> list[dict]:
    """Return all +EV opportunities found in a single event."""
    books = {b["key"]: b for b in event.get("bookmakers", [])}
    sharp = books.get(config.SHARP_BOOK)
    if not sharp:
        return []                    # no sharp benchmark -> can't judge value

    sharp_odds = extract_market(sharp, config.MARKET)
    if len(sharp_odds) < 2:
        return []

    true_p = devig.fair_probs(sharp_odds, method=method)
    margin = devig.overround(sharp_odds) - 1.0
    home, away = event.get("home_team"), event.get("away_team")

    # how many soft books price each outcome at/above its fair odds?
    # broad agreement => slow market (trustworthy); one outlier => stale line.
    agree: dict[str, int] = {}
    for bk_key, bk in books.items():
        if not _is_soft(bk_key):
            continue
        for outcome, price in extract_market(bk, config.MARKET).items():
            p = true_p.get(outcome)
            if p and price >= (1.0 / p):
                agree[outcome] = agree.get(outcome, 0) + 1

    found = []
    for bk_key, bk in books.items():
        if not _is_soft(bk_key):
            continue
        soft_odds = extract_market(bk, config.MARKET)
        for outcome, price in soft_odds.items():
            p = true_p.get(outcome)
            if not p:
                continue
            ev = price * p - 1.0
            # ── sanity filters ──
            if ev < config.MIN_EV or ev > config.MAX_EV:
                continue
            if price > config.MAX_ODDS:
                continue
            if agree.get(outcome, 0) < config.MIN_BOOKS_AGREE:
                continue
            found.append({
                "scanned_at":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "commence_time": event.get("commence_time"),
                "sport":        event.get("sport_key", ""),
                "event":        f"{home} vs {away}",
                "home_team":    home,
                "away_team":    away,
                "selection":    outcome,
                "soft_book":    bk_key,
                "soft_odds":    round(price, 3),
                "sharp_odds":   round(sharp_odds.get(outcome, float("nan")), 3),
                "true_prob":    round(p, 4),
                "ev_pct":       round(ev * 100, 2),
                "sharp_margin": round(margin * 100, 2),
                "books_agree":  agree.get(outcome, 0),
                "stake":        kelly_stake(p, price, bankroll),
            })
    return found


def scan_all(events: list[dict], bankroll: float, method: str = "power") -> list[dict]:
    """Scan a list of events, return opportunities sorted by EV% (best first)."""
    out = []
    for ev in events:
        out.extend(scan_event(ev, bankroll, method=method))
    out.sort(key=lambda r: r["ev_pct"], reverse=True)
    return out

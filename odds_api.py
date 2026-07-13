"""
odds_api.py  --  thin client for The Odds API (https://the-odds-api.com).

Only the two endpoints we need:
  list_sports()        -> what sports are in season right now
  fetch_odds(sport)    -> live odds for every event, from every book

The API returns a quota header on each call so we can watch our free 500/month.
"""
from __future__ import annotations
import requests

import config


class OddsAPIError(RuntimeError):
    pass


def _request_with_retry(url: str, params: dict, tries: int = 4, wait: int = 60):
    """
    GET with retries: Wi-Fi blips and DNS hiccups shouldn't kill the daemon.
    Waits `wait` seconds between attempts (network often recovers in a minute).
    """
    import time
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            return requests.get(url, params=params, timeout=30)
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt < tries:
                print(f"  [network] attempt {attempt}/{tries} failed "
                      f"({type(e).__name__}) -- retrying in {wait}s ...")
                time.sleep(wait)
    raise OddsAPIError(f"network down after {tries} attempts: {last_err}")


def _rotate_key(reason: str = "") -> bool:
    """Switch to the next available API key. Returns False if all keys exhausted."""
    idx = config.API_KEYS.index(config.API_KEY) if config.API_KEY in config.API_KEYS else -1
    next_idx = idx + 1
    if next_idx >= len(config.API_KEYS):
        return False
    config.API_KEY = config.API_KEYS[next_idx]
    print(f"  [key rotation] switched to key {next_idx + 1}/{len(config.API_KEYS)}"
          + (f" ({reason})" if reason else ""))
    return True


def _get(path: str, params: dict) -> tuple[object, dict]:
    for _ in range(len(config.API_KEYS)):
        params_with_key = {"apiKey": config.API_KEY, **params}
        url = f"{config.API_BASE}{path}"
        resp = _request_with_retry(url, params_with_key)

        if resp.status_code == 401:
            if _rotate_key("key rejected (401)"):
                continue
            raise OddsAPIError("All API keys exhausted or invalid.")
        if resp.status_code == 429:
            if _rotate_key("quota exceeded (429)"):
                continue
            raise OddsAPIError("All API keys quota exceeded.")
        if resp.status_code == 422:
            raise OddsAPIError(f"422 -- bad parameters: {resp.text[:200]}")
        if resp.status_code != 200:
            raise OddsAPIError(f"{resp.status_code}: {resp.text[:200]}")

        quota = {
            "remaining": resp.headers.get("x-requests-remaining"),
            "used": resp.headers.get("x-requests-used"),
        }
        remaining = int(quota["remaining"]) if quota["remaining"] else 999
        if remaining < 20:
            _rotate_key(f"only {remaining} requests left on this key")
        return resp.json(), quota

    raise OddsAPIError("All API keys failed.")


def list_sports() -> list[dict]:
    """All sports the API currently covers (in-season flagged)."""
    data, _ = _get("/sports", {})
    return data


def fetch_odds(sport: str) -> tuple[list[dict], dict]:
    """
    Live odds for one sport.

    Returns (events, quota). Each event:
      {
        "id", "commence_time", "home_team", "away_team",
        "bookmakers": [
          {"key": "pinnacle", "title": "Pinnacle",
           "markets": [{"key": "h2h", "outcomes": [
               {"name": "Team A", "price": 2.10}, ...]}]},
          ...
        ]
      }
    """
    params = {
        "regions": config.REGIONS,
        "markets": config.MARKET,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    data, quota = _get(f"/sports/{sport}/odds", params)
    return data, quota


def fetch_scores(sport: str, days_from: int = 3) -> tuple[list[dict], dict]:
    """
    Recent + live scores for one sport (completed games carry final scores).
    Used by autosettle. Each item:
      {"id", "completed": bool, "home_team", "away_team",
       "scores": [{"name": "<team>", "score": "2"}, ...]}
    """
    params = {"daysFrom": days_from, "dateFormat": "iso"}
    data, quota = _get(f"/sports/{sport}/scores", params)
    return data, quota


def extract_market(bookmaker: dict, market_key: str = "h2h") -> dict[str, float]:
    """Pull {outcome_name: decimal_odds} for one market from one bookmaker."""
    for m in bookmaker.get("markets", []):
        if m.get("key") == market_key:
            return {o["name"]: float(o["price"])
                    for o in m.get("outcomes", []) if o.get("price")}
    return {}

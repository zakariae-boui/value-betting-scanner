"""
config.py  --  all the knobs for the +EV scanner in one place.

Edit the values here, never hard-code them elsewhere.
"""
import os
from pathlib import Path

# ── API ──────────────────────────────────────────────────────────────────────
# Get a free key at https://the-odds-api.com (free tier = 500 requests/month).
# Keys are NEVER stored in this repository. Two ways to provide yours:
#
#   1. Environment variable (recommended):
#        $env:THE_ODDS_API_KEY = "your_key"          (PowerShell)
#        export THE_ODDS_API_KEY="your_key"          (bash)
#
#   2. A local `api_keys.txt` next to this file (gitignored), one key per
#      line. If several keys are listed, odds_api.py rotates to the next one
#      when the current key's quota runs low.
API_KEYS: list[str] = []

_keys_file = Path(__file__).parent / "api_keys.txt"
if _keys_file.exists():
    API_KEYS += [ln.strip() for ln in _keys_file.read_text().splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]

_env_key = os.environ.get("THE_ODDS_API_KEY")
if _env_key:
    API_KEYS = [_env_key] + [k for k in API_KEYS if k != _env_key]

API_KEY = API_KEYS[0] if API_KEYS else ""   # active key -- rotated by odds_api.py

API_BASE = "https://api.the-odds-api.com/v4"

# Regions to pull books from (more regions = more soft books = more edges, but
# each region costs request quota). eu+uk+us is a good spread.
REGIONS = "eu,uk,us"

# Which sports to scan. Full list: GET /v4/sports  (run: python run.py sports).
# NOTE: European top leagues run Aug-May. In summer, scan in-season leagues.
# Softer leagues (Scandinavia, South America) hide the most value -- less sharp
# money polices them, so soft books misprice more often.
SPORTS = [
    "soccer_fifa_world_cup",        # huge + liquid (sharp, but lots of books)
    "soccer_brazil_campeonato",     # Brazil Serie A
    "soccer_brazil_serie_b",        # softer
    "soccer_norway_eliteserien",    # softer -- good hunting ground
    "soccer_sweden_allsvenskan",    # softer
    "soccer_finland_veikkausliiga", # softer
    "soccer_league_of_ireland",     # softer
    "soccer_chile_campeonato",      # softer
]

# ── books: sharp vs soft ───────────────────────────────────────────────────────
# The SHARP book's devigged price = our "true probability" benchmark.
# Pinnacle is the gold standard. (Add "circa" / "betonlineag" as backups.)
SHARP_BOOK = "pinnacle"

# SOFT books are where the value lives -- recreational books slow to move.
# We only flag a bet if a soft book beats the sharp fair price.
# Empty set = "treat every non-sharp book as soft" (simplest; good to start).
SOFT_BOOKS: set[str] = set()   # e.g. {"draftkings", "fanduel", "williamhill", "betmgm"}

# ── strategy parameters ────────────────────────────────────────────────────────
MIN_EV = 0.02          # only flag bets with >= 2% expected value
MARKET = "h2h"         # h2h = match winner (3-way for soccer). Start here.

# ── sanity filters (avoid the classic traps) ──────────────────────────────────
# Devig is unreliable on longshots, and a single book wildly above the market is
# usually a STALE line that vanishes when you click (and gets you limited fast).
MAX_ODDS = 6.0         # ignore selections priced above this (longshot bias)
MAX_EV   = 0.15        # ignore "too good to be true" edges (almost always stale)
MIN_BOOKS_AGREE = 2    # require >=N soft books at/above fair odds for the pick
                       # (broad agreement = real slow market, not one outlier)

# ── short-odds stake cap ───────────────────────────────────────────────────────
# Kelly goes crazy on short prices (risk 20 to win 2). Cap the damage.
SHORT_ODDS_THRESHOLD = 1.40   # bets below this get a hard unit cap
MAX_STAKE_SHORT      = 7.0    # max units for those bets (change to 5 if preferred)

# ── bankroll / staking ─────────────────────────────────────────────────────────
STARTING_BANKROLL = 1000.0   # PAPER money -- pretend units, no real cash
KELLY_FRACTION = 0.25        # fractional Kelly (1/4) -- safer than full Kelly
MAX_STAKE_PCT = 0.02         # never stake more than 2% of bankroll on one bet
MIN_STAKE = 1.0              # don't bother logging sub-1-unit stakes

# ── files ──────────────────────────────────────────────────────────────────────
LEDGER_PATH = "paper_bets.csv"   # relative to the project folder

"""Central, tunable configuration for the market signal scanner.

Everything a human is likely to want to adjust (scoring weights, thresholds,
position sizing, data sources) lives here so the scanner logic stays clean.
Secrets are read from the environment / a gitignored .env file.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root if present (no-op if missing).
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
BACKTEST_RESULTS_DIR = PROJECT_ROOT / "backtest" / "results"

# --------------------------------------------------------------------------- #
# Secrets / integrations
# --------------------------------------------------------------------------- #
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# Be a polite scraper: identify ourselves and don't hammer endpoints.
HTTP_USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "market-signal-scanner/0.1 (personal research; contact via Discord)",
)
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
# Seconds to sleep between successive HTTP requests to the same source.
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.0"))

# --------------------------------------------------------------------------- #
# Data sources
# --------------------------------------------------------------------------- #
OPENINSIDER_BASE = os.getenv("OPENINSIDER_BASE", "http://openinsider.com")

# --------------------------------------------------------------------------- #
# Ticker renames (symbol changes of a *continuing* company)
# --------------------------------------------------------------------------- #
# yfinance returns nothing for retired symbols, so a signal filed under an old
# ticker looks "delisted" even though the company trades on under a new symbol.
# Map old -> current so the price lookup (and thus the backtest) recovers them.
#
# IMPORTANT: only pure renames / symbol changes belong here — NOT acquisitions.
# When a company is bought out, the position is realized at the deal price, not
# converted into the acquirer's stock, so mapping e.g. ATVI -> MSFT would be
# wrong. Those cash/premium exits can't be recovered from yfinance and are left
# missing on purpose.
TICKER_RENAMES: dict[str, str] = {
    "SQ": "XYZ",       # Block
    "NLOK": "GEN",     # NortonLifeLock -> Gen Digital
    "SGMS": "LNW",     # Scientific Games -> Light & Wonder
    "SEAS": "PRKS",    # SeaWorld -> United Parks
    "RCII": "UPBD",    # Rent-A-Center -> Upbound Group
    "CFX": "ENOV",     # Colfax -> Enovis
    "CPSI": "TBRG",    # Computer Programs & Systems -> TruBridge
    "DISH": "SATS",    # DISH Network -> EchoStar (surviving ticker)
    "BGNE": "ONC",     # BeiGene -> BeOne Medicines
    "BODY": "BODI",    # Beachbody -> BODi
    "HHC": "HHH",      # Howard Hughes -> Howard Hughes Holdings
    "MPLN": "CTEV",    # MultiPlan -> Claritev
    "NCR": "VYX",      # NCR -> NCR Voyix (continuing entity)
    "LSXMA": "SIRI",   # Liberty SiriusXM -> Sirius XM
    "LSXMK": "SIRI",
    "PARA": "PSKY",    # Paramount -> Paramount Skydance
    "PARAA": "PSKY",
    "SIX": "FUN",      # Six Flags / Cedar Fair merger (surviving ticker)
    "WLL": "CHRD",     # Whiting -> Chord Energy (Whiting+Oasis)
    "ZI": "GTM",       # ZoomInfo -> GTM
}


# --------------------------------------------------------------------------- #
# Universe / watchlist
# --------------------------------------------------------------------------- #
# Per project decision: report everything (no ticker filter) to start.
# If you later want to restrict, fill this set with upper-case tickers; an
# empty set means "no filter".
WATCHLIST: set[str] = set()

# --------------------------------------------------------------------------- #
# Position sizing
# --------------------------------------------------------------------------- #
BASE_POSITION_PCT = 2.0          # base size as % of account
MAX_POSITION_PCT = 5.0           # hard cap on any single position

# Multiplier applied to the base size based on how many distinct signal
# *sources* fired for the same ticker.
SIZE_MULTIPLIER_BY_SOURCE_COUNT = {
    1: 1.0,
    2: 1.5,
    3: 2.0,   # 3+ -> 2.0 (see scoring.size_multiplier)
}

# --------------------------------------------------------------------------- #
# Scoring rubric (tunable). See scoring.py for how these are applied.
# --------------------------------------------------------------------------- #
SCORING = {
    "options": {
        "voloi_3x": 1,           # vol/OI > 3x
        "voloi_10x": 2,          # vol/OI > 10x (replaces the +1, not additive)
        "ratio_low": 3.0,
        "ratio_high": 10.0,
    },
    "insider": {
        "c_suite": 1,            # buyer holds a C-suite title
        "cluster_or_big": 2,     # 3+ insiders clustering OR a single buy > $500K
        "cluster_count": 3,
        "big_value_usd": 500_000,
        # Whole-token abbreviations (matched against tokenized titles to avoid
        # substring false positives like "direCTOr" matching "CTO").
        "c_suite_tokens": ("CEO", "CFO", "COO", "CTO", "CMO", "CIO", "PRES",
                           "PRESIDENT", "CHAIRMAN", "COB"),
        # Phrases matched as substrings (these don't false-positive).
        "c_suite_phrases": ("CHIEF",),
    },
    "politician": {
        "pct_5": 1,              # > 5% of their disclosed portfolio
        "pct_15": 2,             # > 15%
        "pct_low": 5.0,
        "pct_high": 15.0,
    },
    "institutional": {
        "form_13f_new": 1,       # new position in a 13F
        "form_13g": 1,           # passive >5% stake
        "form_13d": 3,           # activist >5% stake
    },
}

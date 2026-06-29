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

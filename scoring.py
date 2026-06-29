"""Signal scoring and position sizing.

A `Signal` is a single observation from one source for one ticker on one date.
Scoring turns the raw fields into an integer strength per the rubric in
`config.SCORING`. Aggregation groups signals by ticker, sums distinct sources,
and derives a recommended position size.

These functions are pure and side-effect free so they can be reused by both the
live scan (`run_scan.py`) and the backtest harness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import config


def is_c_suite_title(title: str) -> bool:
    """True if a title denotes a C-suite officer.

    Uses whole-token matching for abbreviations (so "Director" doesn't match
    "CTO") plus substring matching for unambiguous phrases like "Chief".
    """
    if not title:
        return False
    upper = title.upper()
    tokens = set(re.split(r"[^A-Z]+", upper))
    cfg = config.SCORING["insider"]
    if tokens & set(cfg["c_suite_tokens"]):
        return True
    return any(phrase in upper for phrase in cfg["c_suite_phrases"])


@dataclass
class Signal:
    """One scored observation from one source."""

    source: str            # 'options' | 'insider' | 'politician' | 'institutional'
    ticker: str
    signal_date: date
    score: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Per-source scoring
# --------------------------------------------------------------------------- #
def score_options(vol_oi_ratio: float) -> int:
    """Unusual call volume: +1 if vol/OI > 3x, +2 if > 10x (not additive)."""
    cfg = config.SCORING["options"]
    if vol_oi_ratio > cfg["ratio_high"]:
        return cfg["voloi_10x"]
    if vol_oi_ratio > cfg["ratio_low"]:
        return cfg["voloi_3x"]
    return 0


def score_insider(*, title: str, cluster_count: int, value_usd: float) -> int:
    """Insider buys: +1 C-suite, +2 for a cluster (3+) or a single buy > $500K.

    The two components stack (max 3), matching the rubric where a large buy by
    a C-suite officer is the strongest insider signal.
    """
    cfg = config.SCORING["insider"]
    score = 0
    if is_c_suite_title(title):
        score += cfg["c_suite"]
    if cluster_count >= cfg["cluster_count"] or value_usd > cfg["big_value_usd"]:
        score += cfg["cluster_or_big"]
    return score


def score_politician(pct_of_portfolio: float) -> int:
    """Politician buys: +1 if > 5% of portfolio, +2 if > 15% (not additive)."""
    cfg = config.SCORING["politician"]
    if pct_of_portfolio > cfg["pct_high"]:
        return cfg["pct_15"]
    if pct_of_portfolio > cfg["pct_low"]:
        return cfg["pct_5"]
    return 0


def score_institutional(form_type: str) -> int:
    """Institutional: +1 13F-new, +1 13G, +3 13D."""
    cfg = config.SCORING["institutional"]
    ft = (form_type or "").upper().replace("/A", "").strip()
    if ft == "13D":
        return cfg["form_13d"]
    if ft == "13G":
        return cfg["form_13g"]
    if ft in ("13F", "13F-NEW", "13F NEW"):
        return cfg["form_13f_new"]
    return 0


# --------------------------------------------------------------------------- #
# Aggregation / position sizing
# --------------------------------------------------------------------------- #
def size_multiplier(distinct_source_count: int) -> float:
    """Map number of distinct signal sources to a base-size multiplier."""
    table = config.SIZE_MULTIPLIER_BY_SOURCE_COUNT
    if distinct_source_count <= 0:
        return 0.0
    # 3+ -> highest defined tier.
    key = min(distinct_source_count, max(table))
    return table[key]


def recommended_size_pct(distinct_source_count: int) -> float:
    """Position size as a % of account, capped at config.MAX_POSITION_PCT."""
    pct = config.BASE_POSITION_PCT * size_multiplier(distinct_source_count)
    return round(min(pct, config.MAX_POSITION_PCT), 2)


@dataclass
class Position:
    """A ticker's aggregated view across all its signals."""

    ticker: str
    signals: list[Signal]

    @property
    def sources(self) -> set[str]:
        return {s.source for s in self.signals}

    @property
    def total_score(self) -> int:
        return sum(s.score for s in self.signals)

    @property
    def size_pct(self) -> float:
        return recommended_size_pct(len(self.sources))


def aggregate_positions(signals: list[Signal]) -> list[Position]:
    """Group signals by ticker into Positions, sorted by conviction.

    Sort key: number of distinct sources (overlap), then total score.
    """
    by_ticker: dict[str, list[Signal]] = {}
    for s in signals:
        if not s.ticker:
            continue
        by_ticker.setdefault(s.ticker.upper(), []).append(s)

    positions = [Position(ticker=t, signals=sigs) for t, sigs in by_ticker.items()]
    positions.sort(key=lambda p: (len(p.sources), p.total_score), reverse=True)
    return positions

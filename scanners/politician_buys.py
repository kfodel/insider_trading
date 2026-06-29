"""Politician (STOCK Act) buy scanner (STUB).

Plan: scrape Capitol Trades (https://capitoltrades.com) for recent congressional
*purchase* disclosures. Score by the trade's size relative to the politician's
disclosed portfolio (>5% -> +1, >15% -> +2). Backtestable via filing date.

Interface to implement:

    scan(lookback_days: int = 30) -> list[Signal]
    fetch_trades(start, end) -> list[<record>]   # for the backtest harness

Each Signal.detail should carry {politician, value_usd, pct_of_portfolio}.
"""

from __future__ import annotations

from scoring import Signal  # noqa: F401  (used once implemented)


def scan(lookback_days: int = 30) -> list[Signal]:
    """TODO: implement with a Capitol Trades scraper."""
    raise NotImplementedError("politician_buys.scan() not implemented yet")

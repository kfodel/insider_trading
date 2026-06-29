"""Institutional filing scanner (STUB).

Plan: query SEC EDGAR full-text / filing feeds for 13D, 13G, and 13F filings.
Prioritize 13D/13G (timely beneficial-ownership stakes) over 13F (quarterly,
stale). Score: 13F new position -> +1, 13G -> +1, 13D -> +3.

Interface to implement:

    scan(lookback_days: int = 30) -> list[Signal]
    fetch_filings(start, end) -> list[<record>]   # for the backtest harness

Each Signal.detail should carry {fund, form_type, value_usd, pct_of_portfolio}.

Note: SEC EDGAR requires a descriptive User-Agent (see config.HTTP_USER_AGENT)
and rate-limits to ~10 req/s.
"""

from __future__ import annotations

from scoring import Signal  # noqa: F401  (used once implemented)


def scan(lookback_days: int = 30) -> list[Signal]:
    """TODO: implement with an SEC EDGAR client."""
    raise NotImplementedError("institutional.scan() not implemented yet")

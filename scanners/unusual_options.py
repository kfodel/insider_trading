"""Unusual call option volume scanner (STUB).

Plan: use yfinance option chains, compute volume / open-interest per call
contract, and flag tickers where vol/OI exceeds the rubric thresholds
(>3x -> +1, >10x -> +2). yfinance gives current chains only, so this source
is intentionally excluded from the backtest (no free historical chain data).

Interface to implement:

    scan(tickers: list[str]) -> list[Signal]

For each ticker, pull near-dated expiries, find the highest vol/OI call, and
emit a Signal with detail = {expiry, strike, volume, open_interest, ratio}.
"""

from __future__ import annotations

from scoring import Signal  # noqa: F401  (used once implemented)


def scan(tickers: list[str] | None = None) -> list[Signal]:
    """TODO: implement with yfinance option chains."""
    raise NotImplementedError("unusual_options.scan() not implemented yet")

"""Offline tests — no network required.

Covers the OpenInsider HTML parser (against a fixture), scoring/sizing,
aggregation/formatting, and the backtest harness (against synthetic prices).

Run: python -m pytest tests/ -q   (pytest optional; see tests/run_tests.py)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scanners import insider_buys  # noqa: E402
from scoring import (  # noqa: E402
    Signal,
    aggregate_positions,
    recommended_size_pct,
    score_insider,
    score_institutional,
    score_options,
    score_politician,
)
import discord_alert  # noqa: E402
from backtest import harness  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "openinsider_sample.html"


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def test_parse_fixture():
    html = FIXTURE.read_text()
    buys = insider_buys._parse_screener_table(html)
    assert len(buys) == 5
    acme = [b for b in buys if b.ticker == "ACME"]
    assert len(acme) == 3
    ceo = next(b for b in acme if b.title == "CEO")
    assert ceo.value_usd == 500_000.0
    assert ceo.qty == 40_000
    assert ceo.price == 12.5
    assert ceo.filing_date == date(2024, 3, 1)
    assert ceo.is_c_suite is True


def test_money_and_date_parsers():
    assert insider_buys._parse_money("+$1,234,567") == 1234567.0
    assert insider_buys._parse_money("$3.00") == 3.0
    assert insider_buys._parse_money("New") is None
    assert insider_buys._parse_money("") is None
    assert insider_buys._parse_int("40,000") == 40000
    assert insider_buys._parse_date("2024-03-01 16:05:12") == date(2024, 3, 1)
    assert insider_buys._parse_date("garbage") is None


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def test_score_insider_rubric():
    # C-suite + big value -> 1 + 2 = 3
    assert score_insider(title="CEO", cluster_count=1, value_usd=600_000) == 3
    # Director, small, not a cluster -> 0
    assert score_insider(title="Director", cluster_count=1, value_usd=10_000) == 0
    # 3-insider cluster, non-C-suite -> 2
    assert score_insider(title="Director", cluster_count=3, value_usd=10_000) == 2
    # C-suite only -> 1
    assert score_insider(title="CFO", cluster_count=1, value_usd=10_000) == 1


def test_other_scorers():
    assert score_options(3.5) == 1
    assert score_options(11) == 2
    assert score_options(2) == 0
    assert score_politician(6) == 1
    assert score_politician(20) == 2
    assert score_politician(1) == 0
    assert score_institutional("13D") == 3
    assert score_institutional("13G/A") == 1
    assert score_institutional("13F") == 1


def test_sizing():
    assert recommended_size_pct(1) == 2.0
    assert recommended_size_pct(2) == 3.0   # 2 * 1.5
    assert recommended_size_pct(3) == 4.0   # 2 * 2.0
    assert recommended_size_pct(4) == 4.0   # capped at tier 3 multiplier (then 5% cap)


# --------------------------------------------------------------------------- #
# buys_to_signals + cluster detection
# --------------------------------------------------------------------------- #
def test_buys_to_signals_clusters():
    buys = insider_buys._parse_screener_table(FIXTURE.read_text())
    signals = insider_buys.buys_to_signals(buys)
    by_ticker = {s.ticker: s for s in signals}

    # ACME: 3 insiders (cluster) incl CEO + $500k buy -> C-suite(1)+cluster(2)=3
    assert by_ticker["ACME"].score == 3
    assert by_ticker["ACME"].detail["cluster_count"] == 3

    # BIGV: President (C-suite) + $1M -> 1 + 2 = 3
    assert by_ticker["BIGV"].score == 3

    # SMOL: 10% owner, $3k, single -> score 0 -> dropped
    assert "SMOL" not in by_ticker


# --------------------------------------------------------------------------- #
# Aggregation + formatting
# --------------------------------------------------------------------------- #
def test_aggregate_and_format():
    signals = [
        Signal("insider", "XYZ", date(2024, 1, 2), 2, {"insider": "A", "title": "CEO", "value_usd": 600000, "cluster_count": 1}),
        Signal("politician", "XYZ", date(2024, 1, 3), 2, {"politician": "Sen. Foo", "value_usd": 50000, "pct_of_portfolio": 18}),
        Signal("insider", "LON", date(2024, 1, 4), 1, {"insider": "B", "title": "CFO", "value_usd": 10000, "cluster_count": 1}),
    ]
    positions = aggregate_positions(signals)
    # XYZ has 2 sources -> ranked first, size 3.0%
    assert positions[0].ticker == "XYZ"
    assert positions[0].size_pct == 3.0
    assert len(positions[0].sources) == 2

    msg = discord_alert.format_positions(positions)
    assert "Insider Buys" in msg
    assert "Politician Buys" in msg
    assert "XYZ" in msg
    assert "3.0%" in msg


# --------------------------------------------------------------------------- #
# Backtest harness with synthetic prices (monkeypatch the cache)
# --------------------------------------------------------------------------- #
def _synthetic_series(start_val: float, daily_growth: float, n: int = 800):
    idx = pd.bdate_range("2022-01-03", periods=n)
    vals = [start_val * (1 + daily_growth) ** i for i in range(n)]
    return pd.Series(vals, index=idx)


def test_harness_forward_returns(monkeypatch):
    # AAA grows ~0.1%/day, SPY ~0.02%/day -> AAA should beat SPY.
    series = {
        "AAA": _synthetic_series(100, 0.001),
        "SPY": _synthetic_series(400, 0.0002),
    }

    def fake_closes(self, ticker):
        return series.get(ticker)

    monkeypatch.setattr(harness.PriceCache, "closes", fake_closes)

    signals = [Signal("insider", "AAA", date(2022, 2, 1), 3, {})]
    df = harness.forward_returns(signals, today=date(2024, 12, 31))
    assert len(df) == 1
    row = df.iloc[0]
    assert row["ret_1mo"] > 0
    assert row["ret_12mo"] > row["ret_1mo"]      # compounding
    assert row["excess_1mo"] > 0                  # beats SPY

    summary = harness.summarize(df)
    assert summary["overall"]["n"] == 1
    assert "1mo" in summary["overall"]
    text = harness.format_summary(summary)
    assert "OVERALL" in text


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([str(Path(__file__)), "-q"]))

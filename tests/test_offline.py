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


def test_clean_ticker():
    assert insider_buys._clean_ticker("CFTR.") == "CFTR"       # trailing dot artifact
    assert insider_buys._clean_ticker("BRK.B") == "BRK-B"      # class share -> dash
    assert insider_buys._clean_ticker(" aapl ") == "AAPL"      # trim + upper
    assert insider_buys._clean_ticker("RDS/A") == "RDS-A"      # slash separator
    assert insider_buys._clean_ticker("") == ""


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


# --------------------------------------------------------------------------- #
# Pagination termination + value filter (offline, fake session)
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeSession:
    """Returns the same fixture on every page — simulates OpenInsider ignoring
    &page=N. fetch_buys must detect the repeat and stop, not loop to max_pages."""

    def __init__(self, html):
        self.html = html
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        return _FakeResp(self.html)


def test_fetch_buys_stops_on_repeated_pages():
    html = FIXTURE.read_text()
    sess = _FakeSession(html)
    buys = insider_buys.fetch_buys(
        filing_days=30, rows_per_page=5, max_pages=25, session=sess
    )
    # Page 1 yields 5 rows; page 2 repeats them -> zero new -> stop after 2 gets.
    assert sess.calls == 2
    assert len(buys) == 5  # not 5 * 25


def test_fetch_buys_value_floor():
    html = FIXTURE.read_text()
    sess = _FakeSession(html)
    buys = insider_buys.fetch_buys(
        filing_days=30, min_value_usd=500_000, rows_per_page=5, session=sess
    )
    # Only ACME CEO ($500k) and BIGV ($1M) clear a $500k floor.
    tickers = sorted(b.ticker for b in buys)
    assert tickers == ["ACME", "BIGV"]


def test_screener_url_fdr_format():
    url = insider_buys._screener_url(filing_range=(date(2025, 7, 1), date(2025, 7, 31)))
    # Confirmed against OpenInsider's UI: fd=-1 (Custom) activates the range,
    # and the range uses spaces around the dash (encoded as +-+).
    assert "fd=-1" in url
    assert "fdr=07%2F01%2F2025+-+07%2F31%2F2025" in url
    assert "xp=1" in url


def test_dedup_buys():
    buys = insider_buys._parse_screener_table(FIXTURE.read_text())
    assert len(insider_buys.dedup_buys(buys + buys)) == len(buys)


def test_ticker_rename_applied(monkeypatch):
    """PriceCache should look up the renamed symbol, not the retired one."""
    import config as cfg

    requested = {}

    class _FakeYf:
        def __init__(self, symbol):
            requested["symbol"] = symbol

        def history(self, **kwargs):
            return pd.DataFrame()  # empty is fine; we only check the symbol used

    monkeypatch.setattr(cfg, "TICKER_RENAMES", {"SQ": "XYZ"})
    monkeypatch.setattr(harness.yf, "Ticker", _FakeYf)

    cache = harness.PriceCache(start=date(2022, 1, 1), end=date(2023, 1, 1))
    cache.closes("SQ")
    assert requested["symbol"] == "XYZ"


def test_coverage_by_group(monkeypatch):
    # LIVE has data; DEAD is "delisted" (no series). Both score 2.
    series = {"LIVE": _synthetic_series(100, 0.001), "SPY": _synthetic_series(400, 0.0002)}

    def fake_closes(self, ticker):
        s = series.get(ticker)
        if s is None:
            self.missing.add(ticker)
        return s

    monkeypatch.setattr(harness.PriceCache, "closes", fake_closes)

    signals = [
        Signal("insider", "LIVE", date(2022, 2, 1), 2, {}),
        Signal("insider", "DEAD", date(2022, 2, 1), 2, {}),
        Signal("insider", "GONE", date(2022, 2, 1), 3, {}),
    ]
    df = harness.forward_returns(signals, today=date(2024, 12, 31))
    cov = harness.coverage_by_group(signals, df)

    assert cov["overall"]["signals"] == 3
    assert cov["overall"]["with_data"] == 1          # only LIVE
    assert cov["by_score"][2]["signals"] == 2
    assert cov["by_score"][2]["with_data"] == 1      # LIVE present, DEAD missing
    assert cov["by_score"][2]["missing"] == 1
    assert cov["by_score"][3]["missing"] == 1        # GONE missing
    assert "survivorship" in harness.format_coverage(cov).lower()


# --------------------------------------------------------------------------- #
# Institutional (EDGAR) parser + scoring
# --------------------------------------------------------------------------- #
def test_edgar_parse_and_score():
    from scanners import institutional

    payload = {
        "hits": {
            "hits": [
                {
                    "_id": "0001-24-000001",
                    "_source": {
                        "display_names": ["Acme Corp (ACME) (CIK 0000123456)"],
                        "ciks": ["0000123456"],
                        "file_date": "2024-05-01",
                        "root_form": "SC 13D",
                    },
                },
                {
                    "_id": "0002-24-000002",
                    "_source": {
                        "display_names": ["Beta Inc (BETA) (CIK 0000999999)"],
                        "ciks": ["0000999999"],
                        "file_date": "2024-05-02",
                        "root_form": "SC 13G",
                    },
                },
            ]
        }
    }
    filings = institutional._parse_hits(payload)
    assert [f.ticker for f in filings] == ["ACME", "BETA"]
    assert filings[0].filed == date(2024, 5, 1)

    signals = {s.ticker: s for s in institutional.filings_to_signals(filings)}
    assert signals["ACME"].score == 3 and signals["ACME"].detail["form_type"] == "13D"
    assert signals["BETA"].score == 1 and signals["BETA"].detail["form_type"] == "13G"


# --------------------------------------------------------------------------- #
# Politician (Capitol Trades) parser + scoring
# --------------------------------------------------------------------------- #
def test_capitoltrades_parse_and_score():
    from scanners import politician_buys as pb

    payload = {
        "data": [
            {
                "asset": {"assetTicker": "NVDA:US"},
                "politician": {"firstName": "Jane", "lastName": "Doe", "party": "R", "chamber": "house"},
                "txType": "buy",
                "value": 300000,
                "txDate": "2024-03-01",
                "pubDate": "2024-03-15",
            },
            {
                "asset": {"assetTicker": "AAPL"},
                "politician": {"firstName": "John", "lastName": "Roe", "party": "D", "chamber": "senate"},
                "txType": "buy",
                "value": 60000,
                "txDate": "2024-03-02",
                "pubDate": "2024-03-16",
            },
            {  # a sale — must be ignored
                "asset": {"assetTicker": "TSLA"},
                "politician": {"firstName": "X", "lastName": "Y"},
                "txType": "sell",
                "value": 500000,
                "txDate": "2024-03-03",
                "pubDate": "2024-03-17",
            },
        ]
    }
    trades = pb._parse_trades(payload)
    assert trades[0].ticker == "NVDA"          # ":US" suffix stripped
    signals = {s.ticker: s for s in pb.trades_to_signals(trades)}
    assert signals["NVDA"].score == 2          # >= $250k
    assert signals["AAPL"].score == 1          # >= $50k
    assert "TSLA" not in signals               # sale dropped
    assert signals["NVDA"].signal_date == date(2024, 3, 15)  # pub_date


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([str(Path(__file__)), "-q"]))

"""Forward-return backtest harness.

Given a list of historical signals (ticker + date + type + score), compute
forward returns at 1/3/6/12 months using yfinance close prices, benchmark
against SPY over the same window, and summarize per signal type and per
scoring tier.

This module is source-agnostic: feed it `Signal` objects from any scanner's
historical fetcher. Options are intentionally excluded (no free historical
chains).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from scoring import Signal

# yfinance logs "$TICKER: possibly delisted" etc. at ERROR for every dead
# symbol. Over 5 years of history that's hundreds of lines of noise for an
# expected condition — silence it and report coverage ourselves instead.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# Forward windows in approximate calendar days.
HORIZONS = {"1mo": 30, "3mo": 91, "6mo": 182, "12mo": 365}
BENCHMARK = "SPY"
# Annualization factor to turn per-period returns into a crude Sharpe.
_PERIODS_PER_YEAR = {"1mo": 12, "3mo": 4, "6mo": 2, "12mo": 1}


@dataclass
class PriceCache:
    """Caches yfinance history per ticker over the full backtest span."""

    start: date
    end: date
    _cache: dict[str, Optional[pd.Series]] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)

    def closes(self, ticker: str) -> Optional[pd.Series]:
        if ticker in self._cache:
            return self._cache[ticker]
        series: Optional[pd.Series] = None
        try:
            df = yf.Ticker(ticker).history(
                start=self.start.isoformat(),
                end=self.end.isoformat(),
                auto_adjust=True,
            )
            if not df.empty:
                s = df["Close"].copy()
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                series = s
        except Exception:  # noqa: BLE001 — bad ticker / network; treat as missing
            series = None
        if series is None:
            self.missing.add(ticker)
        self._cache[ticker] = series
        return series


def _price_on_or_after(closes: pd.Series, target: date) -> Optional[float]:
    """First available close on/after `target` (handles weekends/holidays)."""
    ts = pd.Timestamp(target)
    window = closes.loc[ts:]
    if window.empty:
        return None
    return float(window.iloc[0])


def forward_returns(
    signals: list[Signal],
    *,
    today: Optional[date] = None,
) -> pd.DataFrame:
    """Build a tidy DataFrame of forward returns (incl. SPY) per signal.

    One row per signal; columns include ret_1mo..ret_12mo and spy_1mo.. plus
    excess_* (signal minus SPY). Returns are decimals (0.05 == +5%).
    """
    today = today or date.today()
    if not signals:
        return pd.DataFrame()

    span_start = min(s.signal_date for s in signals) - timedelta(days=5)
    span_end = today + timedelta(days=5)
    cache = PriceCache(start=span_start, end=span_end)

    spy_closes = cache.closes(BENCHMARK)
    rows = []

    for sig in signals:
        closes = cache.closes(sig.ticker)
        if closes is None:
            continue
        entry = _price_on_or_after(closes, sig.signal_date)
        spy_entry = (
            _price_on_or_after(spy_closes, sig.signal_date)
            if spy_closes is not None
            else None
        )
        if entry is None or entry == 0:
            continue

        row = {
            "ticker": sig.ticker,
            "source": sig.source,
            "date": sig.signal_date,
            "score": sig.score,
        }
        for name, days in HORIZONS.items():
            target = sig.signal_date + timedelta(days=days)
            # Skip horizons that haven't matured yet.
            if target > today:
                row[f"ret_{name}"] = np.nan
                row[f"spy_{name}"] = np.nan
                row[f"excess_{name}"] = np.nan
                continue
            exit_px = _price_on_or_after(closes, target)
            row[f"ret_{name}"] = (exit_px / entry - 1.0) if exit_px else np.nan

            if spy_entry and spy_closes is not None:
                spy_exit = _price_on_or_after(spy_closes, target)
                spy_ret = (spy_exit / spy_entry - 1.0) if spy_exit else np.nan
            else:
                spy_ret = np.nan
            row[f"spy_{name}"] = spy_ret
            row[f"excess_{name}"] = (
                row[f"ret_{name}"] - spy_ret
                if not (pd.isna(row[f"ret_{name}"]) or pd.isna(spy_ret))
                else np.nan
            )
        rows.append(row)

    # Coverage report: dead/renamed tickers are expected over long spans and are
    # simply dropped. Surface the count so the user knows the effective sample.
    requested = {s.ticker for s in signals} | {BENCHMARK}
    n_missing = len(cache.missing)
    n_found = len(requested) - n_missing
    print(
        f"[prices] {n_found}/{len(requested)} tickers had data; "
        f"{n_missing} missing (delisted/renamed/no data)."
    )
    if cache.missing:
        sample = ", ".join(sorted(cache.missing)[:20])
        more = " ..." if n_missing > 20 else ""
        print(f"[prices] no data for: {sample}{more}")
    if BENCHMARK in cache.missing:
        print(f"[prices] WARNING: benchmark {BENCHMARK} had no data — excess vs SPY unavailable.")

    return pd.DataFrame(rows)


def _summarize_group(df: pd.DataFrame) -> dict:
    """Summary stats for one group across all horizons."""
    out: dict = {"n": len(df)}
    for name in HORIZONS:
        col = df[f"ret_{name}"].dropna()
        exc = df[f"excess_{name}"].dropna()
        if col.empty:
            continue
        ppy = _PERIODS_PER_YEAR[name]
        sharpe = (
            (col.mean() / col.std(ddof=1) * np.sqrt(ppy))
            if col.std(ddof=1) and len(col) > 1
            else np.nan
        )
        out[name] = {
            "n_matured": int(col.count()),
            "mean": round(float(col.mean()), 4),
            "median": round(float(col.median()), 4),
            "win_rate": round(float((col > 0).mean()), 4),
            "sharpe": round(float(sharpe), 3) if not pd.isna(sharpe) else None,
            "mean_excess_vs_spy": round(float(exc.mean()), 4) if not exc.empty else None,
        }
    return out


def summarize(df: pd.DataFrame) -> dict:
    """Summary stats overall, per source, and per scoring tier."""
    if df.empty:
        return {"overall": {"n": 0}, "by_source": {}, "by_score": {}}

    result = {
        "overall": _summarize_group(df),
        "by_source": {
            src: _summarize_group(g) for src, g in df.groupby("source")
        },
        "by_score": {
            int(score): _summarize_group(g) for score, g in df.groupby("score")
        },
    }
    return result


def format_summary(summary: dict) -> str:
    """Human-readable text rendering of summarize()'s output."""
    lines: list[str] = []

    def render_block(label: str, block: dict) -> None:
        lines.append(f"\n=== {label}  (n={block.get('n', 0)}) ===")
        for name in HORIZONS:
            h = block.get(name)
            if not h:
                continue
            excess = h.get("mean_excess_vs_spy")
            excess_s = f"  excess_vs_SPY={excess:+.2%}" if excess is not None else ""
            sharpe = h.get("sharpe")
            sharpe_s = f"  Sharpe={sharpe}" if sharpe is not None else ""
            lines.append(
                f"  {name:>4s} (n={h['n_matured']:4d}): "
                f"mean={h['mean']:+.2%}  median={h['median']:+.2%}  "
                f"win={h['win_rate']:.0%}{sharpe_s}{excess_s}"
            )

    render_block("OVERALL", summary["overall"])
    for src, block in summary.get("by_source", {}).items():
        render_block(f"SOURCE: {src}", block)
    for score, block in sorted(summary.get("by_score", {}).items()):
        render_block(f"SCORE TIER: {score}", block)

    return "\n".join(lines)

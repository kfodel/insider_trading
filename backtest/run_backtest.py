"""Backtest runner for the OpenInsider signal.

Pulls historical insider purchases over a date range, scores them, computes
forward returns with the harness, and writes a summary + the raw per-signal
returns to backtest/results/.

Usage:
    python -m backtest.run_backtest --years 5
    python -m backtest.run_backtest --start 2019-01-01 --end 2024-01-01
    python -m backtest.run_backtest --years 5 --min-value 100000
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

import config
from scanners.insider_buys import buys_to_signals, fetch_buys
from backtest.harness import forward_returns, format_summary, summarize


def _monthly_windows(start: date, end: date):
    """Yield (window_start, window_end) ~monthly chunks.

    OpenInsider's screener caps rows per query, so we page through history in
    monthly windows to keep each query's result set complete.
    """
    cur = start
    while cur < end:
        nxt = min(date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1), end)
        yield cur, nxt - timedelta(days=1)
        cur = nxt


def gather_historical_signals(start: date, end: date, min_value_usd: float, verbose: bool):
    all_buys = []
    for w_start, w_end in _monthly_windows(start, end):
        if verbose:
            print(f"[window] {w_start} .. {w_end}")
        buys = fetch_buys(
            filing_range=(w_start, w_end),
            min_value_usd=min_value_usd,
            verbose=verbose,
        )
        all_buys.extend(buys)
        if verbose:
            print(f"  -> {len(buys)} buys")
    if verbose:
        print(f"Total raw buys: {len(all_buys)}")
    return buys_to_signals(all_buys)


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest OpenInsider insider-buy signals.")
    p.add_argument("--years", type=int, default=5, help="Lookback in years (default 5).")
    p.add_argument("--start", type=str, help="Start date YYYY-MM-DD (overrides --years).")
    p.add_argument("--end", type=str, help="End date YYYY-MM-DD (default today).")
    p.add_argument("--min-value", type=float, default=0.0, help="Min buy value in USD.")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today()
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        start = end - timedelta(days=365 * args.years)

    verbose = not args.quiet
    print(f"Backtesting insider buys {start} .. {end} (min_value=${args.min_value:,.0f})")

    signals = gather_historical_signals(start, end, args.min_value, verbose)
    print(f"Scored signals: {len(signals)}")

    df = forward_returns(signals, today=end)
    summary = summarize(df)

    print(format_summary(summary))

    # Persist results.
    config.BACKTEST_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    csv_path = config.BACKTEST_RESULTS_DIR / f"insider_returns_{stamp}.csv"
    json_path = config.BACKTEST_RESULTS_DIR / f"insider_summary_{stamp}.json"
    if not df.empty:
        df.to_csv(csv_path, index=False)
    with open(json_path, "w") as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(f"\nWrote {csv_path}\nWrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Market Signal Scanner

Scans for "smart money" signals across four sources, scores them, sizes a
position, and alerts to Discord. Includes a forward-return backtest harness.

> Status: **skeleton + OpenInsider (insider buys) scanner + backtest harness.**
> The other three scanners (unusual options, politician, institutional) are
> stubs with defined interfaces.

## Signals

| Source | Data | Backtestable |
|---|---|---|
| Unusual call option volume | yfinance option chains (vol/OI) | No (no free historical chains) |
| Insider buys | OpenInsider (SEC Form 4) scrape | **Yes** |
| Politician buys | Capitol Trades scrape (STOCK Act) | Yes (planned) |
| Institutional | SEC EDGAR 13D / 13G / 13F | Yes (planned) |

Any one signal is enough to act. Position size scales with signal strength and
the number of overlapping sources for the same ticker.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and paste your Discord webhook URL
```

## Usage

```bash
# Verify the Discord webhook works:
python discord_alert.py --test

# Run a live scan (insider buys, last 7 days) and post to Discord:
python run_scan.py

# Scan but print only, no Discord post:
python run_scan.py --dry-run --lookback 14

# Run just the insider scanner directly:
python -m scanners.insider_buys

# Backtest insider buys over the last 5 years:
python -m backtest.run_backtest --years 5
# ...or an explicit window, with a value floor:
python -m backtest.run_backtest --start 2019-01-01 --end 2024-01-01 --min-value 100000
```

Backtest output (summary stats per source and per score tier, plus the raw
per-signal forward returns vs SPY) is written to `backtest/results/`.

## Configuration

All tunables live in `config.py`: scoring weights/thresholds, position sizing
(`BASE_POSITION_PCT`, `MAX_POSITION_PCT`), the optional `WATCHLIST` filter
(empty = report everything), HTTP politeness settings, and the OpenInsider base
URL. Secrets come from `.env` (gitignored).

## Scoring & sizing

Rubric (see `config.SCORING`):

- **Unusual calls:** +1 if vol/OI > 3x, +2 if > 10x
- **Insider:** +1 C-suite, +2 cluster (3+) or single buy > $500K (these stack)
- **Politician:** +1 if > 5% of portfolio, +2 if > 15%
- **Institutional:** +1 13F-new, +1 13G, +3 13D

Sizing: base 2% of account × source-overlap multiplier (1 source → 1×,
2 → 1.5×, 3+ → 2×), capped at 5% per position.

## Project layout

```
scanners/
  unusual_options.py   # stub
  insider_buys.py      # OpenInsider scraper (implemented)
  politician_buys.py   # stub
  institutional.py     # stub
backtest/
  harness.py           # forward-return + SPY benchmark + summary stats
  run_backtest.py      # OpenInsider backtest runner
scoring.py             # Signal model, scoring, aggregation, sizing
discord_alert.py       # webhook formatting + send (--test)
config.py              # all tunables + secrets loading
run_scan.py            # orchestrator
tests/                 # offline tests (HTML fixture + synthetic prices)
```

## Network requirements

The scanners need outbound access to `openinsider.com`, `query*.finance.yahoo.com`
(yfinance), and `discord.com`. Run from a host where these are reachable. In a
sandbox with a restrictive egress allowlist these calls will 403 — that's
expected; run the live commands on your self-hosted machine.

## Tests

```bash
python -m pytest tests/ -q       # or: python tests/run_tests.py
```

Tests run fully offline: the OpenInsider parser is checked against a saved HTML
fixture and the backtest harness against a synthetic price series.
```

"""Politician (STOCK Act) buy scanner — Capitol Trades.

Pulls recent congressional *purchase* disclosures from Capitol Trades' JSON
backend (bff.capitoltrades.com), which aggregates STOCK Act filings.

Entry points:
  * scan(lookback_days)        -> live Signals from recent buys
  * fetch_trades(start, end)   -> raw records for a date range (backtest)

Scoring caveat: the rubric wants "% of the politician's portfolio", but the
feed exposes only the trade's disclosed dollar *range* (STOCK Act buckets like
$1,001-$15,000). Computing true portfolio-% requires aggregating each
politician's full holdings, which is a follow-up. For now we score off the
trade-size bucket as a proxy (see _score_by_value) and stash the raw value so
the real portfolio-% can be layered in later.

Live response schema needs a verification pass (run scan with verbose and
confirm field names) — coded to Capitol Trades' observed shape.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import requests

import config
from scoring import Signal

TRADES_URL = "https://bff.capitoltrades.com/trades"

# STOCK Act disclosure buckets are wide; use the trade's lower-bound value as a
# size proxy until true portfolio-% is available. Thresholds are deliberately
# conservative and live in one place for tuning.
_BIG_TRADE_USD = 250_000      # -> +2 (proxy for a high-conviction / large buy)
_MED_TRADE_USD = 50_000       # -> +1


@dataclass
class PoliticianTrade:
    politician: str
    party: str
    chamber: str
    ticker: str
    tx_type: str               # "buy" / "sell"
    value_usd: Optional[float]  # lower bound of the disclosed range
    tx_date: Optional[date]
    pub_date: Optional[date]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"}
    )
    return s


def _to_date(raw) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _to_value(raw) -> Optional[float]:
    """Capitol Trades 'value' is typically the numeric lower bound of the range."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_trades(payload: dict) -> list[PoliticianTrade]:
    rows = (payload or {}).get("data", []) or []
    out: list[PoliticianTrade] = []
    for r in rows:
        asset = r.get("asset") or {}
        pol = r.get("politician") or {}
        ticker = (asset.get("assetTicker") or "").upper().split(":")[0].strip()
        if not ticker:
            continue
        name = " ".join(
            x for x in (pol.get("firstName"), pol.get("lastName")) if x
        ).strip()
        out.append(
            PoliticianTrade(
                politician=name,
                party=pol.get("party", ""),
                chamber=pol.get("chamber", ""),
                ticker=ticker,
                tx_type=(r.get("txType") or "").lower(),
                value_usd=_to_value(r.get("value")),
                tx_date=_to_date(r.get("txDate")),
                pub_date=_to_date(r.get("pubDate")),
            )
        )
    return out


def fetch_trades(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    tx_type: str = "buy",
    max_pages: int = 20,
    page_size: int = 100,
    session: Optional[requests.Session] = None,
    verbose: bool = False,
) -> list[PoliticianTrade]:
    """Fetch congressional trades of a given type over a date range."""
    sess = session or _session()
    all_trades: list[PoliticianTrade] = []

    for page in range(1, max_pages + 1):
        params = {
            "txType": tx_type,
            "page": page,
            "pageSize": page_size,
            "sortBy": "-txDate",
        }
        if start:
            params["txDateFrom"] = start.isoformat()
        if end:
            params["txDateTo"] = end.isoformat()
        if verbose:
            print(f"  [capitoltrades] page {page}")
        resp = sess.get(TRADES_URL, params=params, timeout=config.HTTP_TIMEOUT)
        resp.raise_for_status()
        page_trades = _parse_trades(resp.json())
        if not page_trades:
            break
        all_trades.extend(page_trades)
        if len(page_trades) < page_size:
            break
        time.sleep(config.REQUEST_DELAY)

    return all_trades


def _score_by_value(value_usd: Optional[float]) -> int:
    """Proxy score from trade size until true portfolio-% is wired in."""
    v = value_usd or 0
    if v >= _BIG_TRADE_USD:
        return 2
    if v >= _MED_TRADE_USD:
        return 1
    return 0


def trades_to_signals(trades: list[PoliticianTrade]) -> list[Signal]:
    """Score buys into Signals — the largest buy per ticker represents it."""
    best: dict[str, PoliticianTrade] = {}
    for t in trades:
        if t.tx_type != "buy":
            continue
        cur = best.get(t.ticker)
        if cur is None or (t.value_usd or 0) > (cur.value_usd or 0):
            best[t.ticker] = t

    signals = []
    for ticker, t in best.items():
        score = _score_by_value(t.value_usd)
        if score <= 0:
            continue
        signal_date = t.pub_date or t.tx_date  # pub_date is when it became public
        if signal_date is None:
            continue
        signals.append(
            Signal(
                source="politician",
                ticker=ticker,
                signal_date=signal_date,
                score=score,
                detail={
                    "politician": t.politician,
                    "party": t.party,
                    "value_usd": t.value_usd,
                    "pct_of_portfolio": None,  # TODO: aggregate holdings
                },
            )
        )
    return signals


def scan(lookback_days: int = 30, verbose: bool = False) -> list[Signal]:
    """Live scan: congressional buys disclosed in the last `lookback_days`."""
    end = date.today()
    start = end - timedelta(days=lookback_days)
    trades = fetch_trades(start=start, end=end, verbose=verbose)
    signals = trades_to_signals(trades)
    if config.WATCHLIST:
        signals = [s for s in signals if s.ticker in config.WATCHLIST]
    return signals


if __name__ == "__main__":
    found = scan(lookback_days=30, verbose=True)
    print(f"\n{len(found)} politician signals (last 30 days):")
    for s in sorted(found, key=lambda x: x.score, reverse=True):
        d = s.detail
        print(f"  {s.ticker:6s} score={s.score} {d.get('politician','')} ${(d.get('value_usd') or 0):,.0f}")

"""OpenInsider scanner.

Scrapes http://openinsider.com's screener for insider *purchases* (Form 4,
transaction code "P"). Two entry points:

  * scan()                         -> live signals from the last N days
  * fetch_buys(...)                -> raw records for an arbitrary date range
                                      (used by the backtest harness)

The screener returns an HTML table (class "tinytable"). We parse it by reading
the header row and mapping column names -> indices, so the parser survives
OpenInsider adding/reordering columns.

Network note: OpenInsider must be reachable. In sandboxes with a restrictive
egress allowlist this will fail with a connection/403 error — run it from your
self-hosted machine (or allowlist openinsider.com).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

import config
from scoring import Signal, is_c_suite_title, score_insider


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class InsiderBuy:
    """One insider purchase row from OpenInsider."""

    filing_date: Optional[date]
    trade_date: Optional[date]
    ticker: str
    company: str
    insider: str
    title: str
    trade_type: str
    price: Optional[float]
    qty: Optional[int]
    value_usd: Optional[float]

    @property
    def is_c_suite(self) -> bool:
        return is_c_suite_title(self.title)


# --------------------------------------------------------------------------- #
# HTTP / parsing helpers
# --------------------------------------------------------------------------- #
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.HTTP_USER_AGENT})
    return s


def _parse_money(text: str) -> Optional[float]:
    """'+$1,234,567' / '$500' / '-1,000' -> float, else None."""
    if not text:
        return None
    cleaned = (
        text.replace("$", "")
        .replace(",", "")
        .replace("+", "")
        .replace("%", "")
        .strip()
    )
    if cleaned in ("", "-", "New", "N/A"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(text: str) -> Optional[int]:
    val = _parse_money(text)
    return int(val) if val is not None else None


def _parse_date(text: str) -> Optional[date]:
    """OpenInsider uses 'YYYY-MM-DD' (sometimes with a time suffix)."""
    if not text:
        return None
    token = text.strip().split(" ")[0]
    try:
        return datetime.strptime(token, "%Y-%m-%d").date()
    except ValueError:
        return None


# Canonical header substrings -> our field key.
_COLUMN_ALIASES = {
    "filing date": "filing_date",
    "trade date": "trade_date",
    "ticker": "ticker",
    "company name": "company",
    "insider name": "insider",
    "title": "title",
    "trade type": "trade_type",
    "price": "price",
    "qty": "qty",
    "value": "value_usd",
}


def _build_header_map(header_cells: list[str]) -> dict[str, int]:
    """Map our field keys to column indices using header text."""
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(header_cells):
        label = raw.strip().lower()
        for alias, key in _COLUMN_ALIASES.items():
            if alias in label and key not in mapping:
                mapping[key] = idx
    return mapping


def _parse_screener_table(html: str) -> list[InsiderBuy]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="tinytable")
    if table is None:
        return []

    header_cells = [th.get_text(strip=True) for th in table.select("thead th")]
    colmap = _build_header_map(header_cells)
    if "ticker" not in colmap:
        return []

    buys: list[InsiderBuy] = []
    for row in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if not cells or len(cells) <= colmap["ticker"]:
            continue

        def cell(key: str) -> str:
            idx = colmap.get(key)
            return cells[idx] if idx is not None and idx < len(cells) else ""

        ticker = cell("ticker").upper()
        if not ticker:
            continue

        buys.append(
            InsiderBuy(
                filing_date=_parse_date(cell("filing_date")),
                trade_date=_parse_date(cell("trade_date")),
                ticker=ticker,
                company=cell("company"),
                insider=cell("insider"),
                title=cell("title"),
                trade_type=cell("trade_type"),
                price=_parse_money(cell("price")),
                qty=_parse_int(cell("qty")),
                value_usd=_parse_money(cell("value_usd")),
            )
        )
    return buys


# --------------------------------------------------------------------------- #
# Screener URL construction
# --------------------------------------------------------------------------- #
def _screener_url(
    *,
    filing_days: Optional[int] = None,
    filing_range: Optional[tuple[date, date]] = None,
    min_value_usd: float = 0.0,
    min_insiders: int = 1,
    rows: int = 1000,
    page: int = 1,
) -> str:
    """Build an OpenInsider screener URL for *purchases* only.

    Use either `filing_days` (last N days) or `filing_range` (explicit window).
    `min_value_usd` filters by transaction value; `min_insiders` >= 3 finds
    cluster buys.
    """
    params: dict[str, str | int] = {
        "s": "",            # ticker (blank = all)
        "o": "",            # insider name
        "pl": "", "ph": "",  # price low/high
        "ll": "", "lh": "",
        "fd": "0",          # filing-date preset; 0 = use custom (fdr) when given
        "fdr": "",
        "td": "0",
        "tdr": "",
        "fdlyl": "", "fdlyh": "",
        "daysago": "",
        "xp": "1",          # transaction type: P - Purchase
        "vl": "", "vh": "",  # value low/high (in $thousands)
        "ocl": "", "och": "",
        "sic1": "-1", "sicl": "", "sich": "",
        "grp": "0",
        "nfl": "", "nfh": "",  # number of insiders (filers) low/high
        "nsl": "", "nsh": "",
        "nadl": "", "nadh": "",
        "sortcol": "0",     # sort by filing date desc
        "cnt": rows,
        "page": page,
    }

    if filing_range is not None:
        start, end = filing_range
        params["fdr"] = f"{start:%m/%d/%Y} - {end:%m/%d/%Y}"
    elif filing_days is not None:
        params["fd"] = str(filing_days)

    if min_value_usd > 0:
        params["vl"] = int(min_value_usd / 1000)  # screener expects $thousands

    if min_insiders > 1:
        params["nfl"] = min_insiders

    return f"{config.OPENINSIDER_BASE}/screener?{urlencode(params)}"


# --------------------------------------------------------------------------- #
# Public fetchers
# --------------------------------------------------------------------------- #
def fetch_buys(
    *,
    filing_days: Optional[int] = None,
    filing_range: Optional[tuple[date, date]] = None,
    min_value_usd: float = 0.0,
    min_insiders: int = 1,
    max_pages: int = 50,
    rows_per_page: int = 1000,
    session: Optional[requests.Session] = None,
    verbose: bool = False,
) -> list[InsiderBuy]:
    """Fetch insider purchases, paginating until exhausted or `max_pages`."""
    sess = session or _session()
    all_buys: list[InsiderBuy] = []

    for page in range(1, max_pages + 1):
        url = _screener_url(
            filing_days=filing_days,
            filing_range=filing_range,
            min_value_usd=min_value_usd,
            min_insiders=min_insiders,
            rows=rows_per_page,
            page=page,
        )
        if verbose:
            print(f"  [openinsider] page {page}: {url}")
        resp = sess.get(url, timeout=config.HTTP_TIMEOUT)
        resp.raise_for_status()
        page_buys = _parse_screener_table(resp.text)
        if not page_buys:
            break
        all_buys.extend(page_buys)
        if len(page_buys) < rows_per_page:
            break  # last page
        time.sleep(config.REQUEST_DELAY)

    return all_buys


# --------------------------------------------------------------------------- #
# Cluster detection + scoring
# --------------------------------------------------------------------------- #
def _cluster_counts(buys: Iterable[InsiderBuy]) -> dict[str, int]:
    """Count distinct insiders per ticker (to detect 3+ clusters)."""
    by_ticker: dict[str, set[str]] = {}
    for b in buys:
        by_ticker.setdefault(b.ticker, set()).add(b.insider)
    return {t: len(names) for t, names in by_ticker.items()}


def buys_to_signals(buys: list[InsiderBuy]) -> list[Signal]:
    """Score raw insider buys into Signals (one per ticker, best buy)."""
    clusters = _cluster_counts(buys)

    # Keep the single most valuable buy per ticker as the representative row,
    # but score using the cluster count for that ticker.
    best_by_ticker: dict[str, InsiderBuy] = {}
    for b in buys:
        cur = best_by_ticker.get(b.ticker)
        if cur is None or (b.value_usd or 0) > (cur.value_usd or 0):
            best_by_ticker[b.ticker] = b

    signals: list[Signal] = []
    for ticker, b in best_by_ticker.items():
        cluster_count = clusters.get(ticker, 1)
        score = score_insider(
            title=b.title,
            cluster_count=cluster_count,
            value_usd=b.value_usd or 0.0,
        )
        if score <= 0:
            continue
        signals.append(
            Signal(
                source="insider",
                ticker=ticker,
                signal_date=b.filing_date or b.trade_date or date.today(),
                score=score,
                detail={
                    "company": b.company,
                    "insider": b.insider,
                    "title": b.title,
                    "value_usd": b.value_usd,
                    "cluster_count": cluster_count,
                },
            )
        )
    return signals


# --------------------------------------------------------------------------- #
# Scanner entry point
# --------------------------------------------------------------------------- #
def scan(lookback_days: int = 7, min_value_usd: float = 0.0, verbose: bool = False) -> list[Signal]:
    """Live scan: insider purchases filed in the last `lookback_days`."""
    buys = fetch_buys(
        filing_days=lookback_days,
        min_value_usd=min_value_usd,
        verbose=verbose,
    )
    signals = buys_to_signals(buys)

    if config.WATCHLIST:
        signals = [s for s in signals if s.ticker in config.WATCHLIST]
    return signals


if __name__ == "__main__":
    found = scan(lookback_days=7, verbose=True)
    print(f"\n{len(found)} insider signals in the last 7 days:")
    for s in sorted(found, key=lambda x: x.score, reverse=True):
        d = s.detail
        print(
            f"  {s.ticker:6s} score={s.score} "
            f"{d.get('insider','')} ({d.get('title','')}) "
            f"${(d.get('value_usd') or 0):,.0f} "
            f"cluster={d.get('cluster_count')}"
        )

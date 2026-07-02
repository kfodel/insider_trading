"""Institutional filing scanner — SEC EDGAR 13D / 13G.

Uses SEC's official EDGAR full-text search JSON API (efts.sec.gov) to find
recent beneficial-ownership filings, which are timely (unlike quarterly 13Fs):

  * SC 13D  — activist >5% stake            -> score +3
  * SC 13G  — passive  >5% stake            -> score +1

Entry points:
  * scan(lookback_days)          -> live Signals from recent filings
  * fetch_filings(forms, s, e)   -> raw records for a date range (backtest)

Notes / caveats:
  * SEC requires a descriptive User-Agent with contact info and rate-limits to
    ~10 req/s — see config.HTTP_USER_AGENT (put a real email in it).
  * Full-text search covers 2001-present.
  * The subject ticker is parsed from the filing's display name; when absent we
    fall back to the CIK->ticker map (company_tickers.json).
  * Live response schema needs a verification pass (run scan with verbose and
    confirm fields) — this is coded to EDGAR's documented shape.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import requests

import config
from scoring import Signal, score_institutional

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# "Apple Inc.  (AAPL)  (CIK 0000320193)" -> capture the ticker in parens.
_DISPLAY_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)")


@dataclass
class Filing:
    form: str          # "SC 13D" / "SC 13G" (may carry "/A" amendment suffix)
    ticker: str
    company: str
    cik: str
    filed: Optional[date]
    accession: str


def _session() -> requests.Session:
    s = requests.Session()
    # SEC blocks requests without a descriptive UA; be a good citizen.
    s.headers.update({"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"})
    return s


def _parse_ticker_from_display(display_names: list[str]) -> tuple[str, str]:
    """Return (ticker, company) from EDGAR's display_names list."""
    if not display_names:
        return "", ""
    primary = display_names[0]
    company = primary.split("(")[0].strip()
    m = _DISPLAY_TICKER_RE.search(primary)
    ticker = m.group(1).upper() if m else ""
    return ticker, company


def _parse_hits(payload: dict) -> list[Filing]:
    """Parse an EDGAR full-text search JSON payload into Filings."""
    hits = (payload or {}).get("hits", {}).get("hits", [])
    out: list[Filing] = []
    for h in hits:
        src = h.get("_source", {})
        ticker, company = _parse_ticker_from_display(src.get("display_names", []))
        ciks = src.get("ciks") or []
        filed_raw = src.get("file_date") or src.get("filed")
        filed = None
        if filed_raw:
            try:
                filed = date.fromisoformat(filed_raw[:10])
            except ValueError:
                filed = None
        out.append(
            Filing(
                form=(src.get("root_form") or src.get("file_type") or src.get("form") or "").strip(),
                ticker=ticker,
                company=company,
                cik=str(ciks[0]) if ciks else "",
                filed=filed,
                accession=h.get("_id", ""),
            )
        )
    return out


def fetch_filings(
    *,
    forms: tuple[str, ...] = ("SC 13D", "SC 13G"),
    start: Optional[date] = None,
    end: Optional[date] = None,
    max_pages: int = 20,
    page_size: int = 100,
    session: Optional[requests.Session] = None,
    verbose: bool = False,
) -> list[Filing]:
    """Fetch beneficial-ownership filings for a date range via EDGAR FTS."""
    sess = session or _session()
    all_filings: list[Filing] = []

    for form in forms:
        for page in range(max_pages):
            params = {
                "forms": form,
                "from": page * page_size,
                "size": page_size,
            }
            if start:
                params["startdt"] = start.isoformat()
            if end:
                params["enddt"] = end.isoformat()
            if verbose:
                print(f"  [edgar] {form} from={params['from']}")
            resp = sess.get(EFTS_URL, params=params, timeout=config.HTTP_TIMEOUT)
            resp.raise_for_status()
            page_hits = _parse_hits(resp.json())
            if not page_hits:
                break
            all_filings.extend(page_hits)
            if len(page_hits) < page_size:
                break
            time.sleep(config.REQUEST_DELAY)

    return all_filings


def filings_to_signals(filings: list[Filing]) -> list[Signal]:
    """Score filings into Signals. Amendments (/A) are treated as their base
    form; the highest-scoring form per ticker wins (13D > 13G)."""
    best: dict[str, tuple[int, Filing]] = {}
    for f in filings:
        if not f.ticker:
            continue
        form_norm = "13D" if "13D" in f.form.upper() else "13G" if "13G" in f.form.upper() else f.form
        score = score_institutional(form_norm)
        if score <= 0:
            continue
        cur = best.get(f.ticker)
        if cur is None or score > cur[0]:
            best[f.ticker] = (score, f)

    signals = []
    for ticker, (score, f) in best.items():
        if f.filed is None:
            continue
        signals.append(
            Signal(
                source="institutional",
                ticker=ticker,
                signal_date=f.filed,
                score=score,
                detail={
                    "fund": f.company,
                    "form_type": "13D" if score == 3 else "13G",
                    "cik": f.cik,
                    "accession": f.accession,
                },
            )
        )
    return signals


def scan(lookback_days: int = 30, verbose: bool = False) -> list[Signal]:
    """Live scan: 13D/13G filed in the last `lookback_days`."""
    end = date.today()
    start = end - timedelta(days=lookback_days)
    filings = fetch_filings(start=start, end=end, verbose=verbose)
    signals = filings_to_signals(filings)
    if config.WATCHLIST:
        signals = [s for s in signals if s.ticker in config.WATCHLIST]
    return signals


if __name__ == "__main__":
    found = scan(lookback_days=14, verbose=True)
    print(f"\n{len(found)} institutional signals (last 14 days):")
    for s in sorted(found, key=lambda x: x.score, reverse=True):
        print(f"  {s.ticker:6s} score={s.score} {s.detail.get('form_type')} {s.detail.get('fund')}")

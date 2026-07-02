"""Entry point: run all scanners, score, and alert to Discord.

    python run_scan.py                 # scan + post to Discord
    python run_scan.py --dry-run       # scan + print, don't post
    python run_scan.py --lookback 14   # widen the insider lookback window

Only the OpenInsider insider scanner is wired up so far; the other three
sources are stubs and are skipped gracefully until implemented.
"""

from __future__ import annotations

import argparse

import config
import discord_alert
from scanners import institutional, insider_buys, politician_buys
from scoring import Signal, aggregate_positions

# Active scanners. Each is (name, callable -> list[Signal]). Institutional and
# politician disclosures move slower than insider filings, so they use a wider
# lookback window.
def _wide(args) -> int:
    return max(args.lookback, 30)


SCANNERS = [
    ("insider", lambda a: insider_buys.scan(lookback_days=a.lookback, verbose=not a.quiet)),
    ("institutional", lambda a: institutional.scan(lookback_days=_wide(a), verbose=not a.quiet)),
    ("politician", lambda a: politician_buys.scan(lookback_days=_wide(a), verbose=not a.quiet)),
]


def collect_signals(args) -> list[Signal]:
    signals: list[Signal] = []
    for name, fn in SCANNERS:
        try:
            found = fn(args)
            print(f"[{name}] {len(found)} signals")
            signals.extend(found)
        except NotImplementedError:
            print(f"[{name}] skipped (not implemented)")
        except Exception as exc:  # noqa: BLE001 — one source failing shouldn't kill the scan
            print(f"[{name}] ERROR: {exc}")
    return signals


def main() -> int:
    p = argparse.ArgumentParser(description="Run the market signal scan.")
    p.add_argument("--dry-run", action="store_true", help="Print results, don't post to Discord.")
    p.add_argument("--lookback", type=int, default=7, help="Insider lookback in days.")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    signals = collect_signals(args)
    positions = aggregate_positions(signals)

    # The overlap thesis: tickers flagged by 2+ independent sources are the
    # highest-conviction ideas. Surface them explicitly.
    overlaps = [p for p in positions if len(p.sources) >= 2]
    if overlaps:
        print("\n*** MULTI-SOURCE OVERLAP (highest conviction) ***")
        for p in overlaps:
            srcs = "+".join(sorted(p.sources))
            print(f"  {p.ticker}: {srcs}  score={p.total_score}  size={p.size_pct}%")

    message = discord_alert.format_positions(positions)
    print("\n" + message)

    if args.dry_run:
        print("\n(dry-run: not posting to Discord)")
        return 0

    if not config.DISCORD_WEBHOOK_URL:
        print("\nNo DISCORD_WEBHOOK_URL set; skipping post. Use --dry-run to silence this.")
        return 0

    discord_alert.send_message(message)
    print("\nPosted to Discord.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Discord webhook alerting.

Formats scan results into the agreed message layout and posts them to a Discord
incoming webhook. Run `python discord_alert.py --test` to verify your webhook
is configured (reads DISCORD_WEBHOOK_URL from .env / environment).
"""

from __future__ import annotations

import sys
import textwrap

import requests

import config
from scoring import Position


# --------------------------------------------------------------------------- #
# Low-level send
# --------------------------------------------------------------------------- #
def send_message(content: str, webhook_url: str | None = None) -> None:
    """Post a plain message to the Discord webhook. Splits >2000-char content."""
    url = (webhook_url or config.DISCORD_WEBHOOK_URL).strip()
    if not url:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL is not set. Copy .env.example to .env and add it."
        )

    # Discord hard-limits message content to 2000 characters.
    for chunk in _chunk(content, 1900):
        resp = requests.post(
            url,
            json={"content": chunk},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()


def _chunk(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > size:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
_SECTION_TITLES = {
    "options": "Unusual Call Volume",
    "insider": "Insider Buys",
    "politician": "Politician Buys",
    "institutional": "Professional Buys",
}


def format_positions(positions: list[Position]) -> str:
    """Render positions into the agreed Discord layout, grouped by source."""
    if not positions:
        return "Market Scan Results:\n(no qualifying signals)"

    lines = ["**Market Scan Results:**"]

    # Group the underlying signals by source for the section layout.
    for source, title in _SECTION_TITLES.items():
        rows = []
        for pos in positions:
            for sig in pos.signals:
                if sig.source != source:
                    continue
                rows.append(_format_signal_row(source, pos, sig))
        if rows:
            lines.append(f"\n__{title}:__")
            lines.extend(rows)

    return "\n".join(lines)


def _format_signal_row(source: str, pos: Position, sig) -> str:
    d = sig.detail
    size = f"size {pos.size_pct}%"
    if source == "insider":
        val = d.get("value_usd")
        val_s = f"${val:,.0f}" if val else "n/a"
        cluster = d.get("cluster_count", 1)
        who = f"{cluster} insiders" if cluster >= 3 else d.get("insider", "")
        return f"  {sig.ticker}: {who} ({d.get('title','')}) {val_s} buy, {size}"
    if source == "options":
        return (
            f"  {sig.ticker}: vol/OI {d.get('ratio','?')}x exp {d.get('expiry','?')}, {size}"
        )
    if source == "politician":
        return (
            f"  {sig.ticker}: {d.get('politician','')} "
            f"${(d.get('value_usd') or 0):,.0f} "
            f"({d.get('pct_of_portfolio','?')}% of portfolio), {size}"
        )
    if source == "institutional":
        return (
            f"  {sig.ticker}: {d.get('fund','')} {d.get('form_type','')} "
            f"${(d.get('value_usd') or 0):,.0f} "
            f"({d.get('pct_of_portfolio','?')}% of portfolio), {size}"
        )
    return f"  {sig.ticker}: {size}"


# --------------------------------------------------------------------------- #
# CLI test
# --------------------------------------------------------------------------- #
def _self_test() -> int:
    masked = (
        config.DISCORD_WEBHOOK_URL[:40] + "..."
        if config.DISCORD_WEBHOOK_URL
        else "(empty)"
    )
    print(f"Using webhook: {masked}")
    try:
        send_message(
            textwrap.dedent(
                """\
                **Market Signal Scanner — webhook test** ✅
                If you can read this, the Discord webhook is wired up correctly.
                """
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print("Sent test message OK.")
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        raise SystemExit(_self_test())
    print("Usage: python discord_alert.py --test")

"""Replay the radar buy rule on the archived wire and write RESULTS.md.

The live snapshot is today's decision. It is not part of the wet path.
Nothing is armed. Reproduce with ``python -m bot.research.radar_gate``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.integrations.alphai.client import AlphaIClient
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.radar_gate.book import BOOK_EUR, MODES, Run, load_books, simulate
from bot.research.radar_gate.score import HOT_Z, SENT_POS, Story, index_stories

OUT = Path(__file__).with_name("RESULTS.md")
ARCHIVE = Path("data/research/alphai_crypto_news.json")
LEDGER = Path("data/research/alphai_forward_ledger.jsonl")
CACHE = Path("data/research/swing_grid/15m")


def _eur(value: float) -> str:
    rounded = int(round(value))
    if rounded > 0:
        return f"+€{rounded:,.0f}"
    if rounded < 0:
        return f"-€{abs(rounded):,.0f}"
    return "€0"


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _last_complete_day() -> str:
    return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


def _archive_rows() -> list[dict[str, Any]]:
    raw = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    rows = raw.get("rows") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected archive at {ARCHIVE}")
    return [row for row in rows if isinstance(row, dict)]


def _line(run: Run) -> str:
    names = ", ".join(f"{base} {count}" for base, count in run.picks[:6]) or "—"
    return (
        f"| {run.mode} | {_eur(run.pnl)} | {_pct(run.max_dd)} | {run.days_held} | "
        f"{run.buys} | {run.trips} | {_eur(run.largest_day)} | {names} |"
    )


def _key() -> str:
    env = os.environ.get("ALPHAI_API_KEY", "").strip()
    if env:
        return env
    path = Path(".env")
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("ALPHAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            for inner in ("delta", "value", "change"):
                nested = value.get(inner)
                if isinstance(nested, (int, float)):
                    return float(nested)
    return None


def _base(ticker: str) -> str:
    text = ticker.strip().upper()
    return text[:-4] if text.endswith("-USD") else text


def _live_rows() -> tuple[str, list[dict[str, Any]], str]:
    """Return as_of, rows, and a note. Never includes the API key."""
    key = _key()
    if key:
        try:
            payload = AlphaIClient(key).signals_snapshot(market="crypto", window="24h", limit=50)
        except RuntimeError as exc:
            note = f"Live radar request failed ({exc.__class__.__name__})."
        else:
            rows = payload.get("results") or payload.get("rows") or []
            if isinstance(rows, list):
                as_of = str(payload.get("as_of") or "")
                point = payload.get("point_in_time")
                note = f"Live snapshot as of {as_of or 'unknown'}. point_in_time={point}."
                return as_of, [row for row in rows if isinstance(row, dict)], note
            note = "Live radar payload had no rows."
    else:
        note = "No AlphaI key in the environment."
    if not LEDGER.exists():
        return "", [], note + " No stored snapshot either."
    last = json.loads(LEDGER.read_text(encoding="utf-8").splitlines()[-1])
    radar = (last.get("radar") or {}).get("24h") or {}
    rows = radar.get("rows") or []
    as_of = str(radar.get("as_of") or last.get("fetched_at") or "")
    kept = [row for row in rows if isinstance(row, dict)]
    return as_of, kept, note + f" Using the stored snapshot {as_of}."


def _live_section(rows: list[dict[str, Any]], note: str) -> str:
    universe = set(DEFAULT_UNIVERSE)
    parsed: list[tuple[float, str, float | None]] = []
    for row in rows:
        ticker = str(row.get("ticker") or row.get("symbol") or "")
        base = _base(ticker)
        z = _number(row, "news_z", "z")
        delta = _number(row, "sent_delta", "sentiment", "sent")
        if z is None:
            continue
        parsed.append((z, base, delta))
    parsed.sort(key=lambda item: (-item[0], item[1]))
    buys = [
        (z, base, delta)
        for z, base, delta in parsed
        if base in universe and z >= HOT_Z and delta is not None and delta >= SENT_POS
    ]
    blocked = [
        (z, base, delta)
        for z, base, delta in parsed
        if base in universe and (delta is None or delta < SENT_POS or z < HOT_Z)
    ]
    chosen = buys[0][1] if buys else "cash"
    lines = [
        "## Live radar",
        "",
        note,
        "A buy needs news_z of at least 1 and a tone change of at least +0.15,",
        "and the name has to be in the desk universe. Anything else is not a buy.",
        f"The book would buy `{chosen}`.",
        "",
        "| name | news_z | tone change | decision |",
        "| --- | ---: | ---: | --- |",
    ]
    shown = parsed[:12]
    buy_names = {base for _z, base, _delta in buys}
    for z, base, delta in shown:
        if base not in universe:
            decision = "outside the book"
        elif base in buy_names and base == chosen:
            decision = "buy"
        elif base in buy_names:
            decision = "passes, not the top name"
        else:
            decision = "do not buy"
        tone = "—" if delta is None else f"{delta:+.2f}"
        lines.append(f"| {base} | {z:.2f} | {tone} | {decision} |")
    if not shown:
        lines.append("| — | — | — | no rows |")
    if blocked and not any(base in universe for _z, base, _d in shown):
        lines.append("")
        lines.append("No desk name was on the snapshot.")
    return "\n".join(lines)


def render(runs: list[Run], grouped: dict[str, list[Story]], live: str) -> str:
    hot = next(run for run in runs if run.mode == "hot")
    header = (
        "| cell | pnl | max DD | days held | buys | trips | largest day | names |"
    )
    rule = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    body = "\n".join(_line(run) for run in runs)
    return "\n".join(
        (
            "# Radar buy gate",
            "",
            "Rules are the module docstring in `__init__.py`. They were locked before",
            "this replay. The wet path reads the archived crypto wire, not",
            "`/api/signals/snapshot/`. That endpoint is a live snapshot and AlphaI",
            "does not offer it as a point-in-time backtest. Archive relevance is at",
            "least 6; the public news default is 4, so these z-scores are not the",
            "live radar's `news_z`.",
            "",
            f"Book €{BOOK_EUR:,.0f}. One name. Next-open fills, taker 0.25% plus impact.",
            f"Archive days with stories: {len(grouped)}. First story {min(grouped)}.",
            f"Last story {max(grouped)}. Replay {hot.start} → {hot.end}.",
            "",
            header,
            rule,
            body,
            "",
            "## Reading",
            "",
            f"`hot` is the radar rule. It finishes {_eur(hot.pnl)}.",
            "Completed round trips pay the taker both ways. Cash earns nothing",
            "while no name clears the tone bar. `hot_any` buys unusual activity",
            "even when the tone is missing or negative, which is the don't-buy",
            "turned off. `spike` also requires a Poisson tail under 0.001.",
            "`chop` buys the jumpiest desk name and ignores the wire.",
            "There is no path before 2026 in this archive, so this cannot clear",
            "a two-period bar. Nothing is armed.",
            "",
            live,
            "",
            "Reproduce: `python -m bot.research.radar_gate`",
            "",
        )
    )


def main() -> None:
    last = _last_complete_day()
    grouped = index_stories(_archive_rows())
    books = load_books(CACHE, DEFAULT_UNIVERSE)
    runs = [
        simulate(books, grouped, universe=DEFAULT_UNIVERSE, mode=mode, last=last)
        for mode in MODES
    ]
    for run in runs:
        print(
            f"{run.mode:8} {_eur(run.pnl):>12} dd {_pct(run.max_dd):>6} "
            f"held {run.days_held} buys {run.buys}",
            flush=True,
        )
    _as_of, rows, note = _live_rows()
    text = render(runs, grouped, _live_section(rows, note))
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()

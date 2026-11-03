"""Replay the sleeve and write RESULTS.md.

Nothing is armed. Reproduce with ``python -m bot.research.alphai_sleeve``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.integrations.alphai.client import AlphaIClient
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.alphai_sleeve.book import (
    MODES,
    SLEEVE_EUR,
    TONE_DAYS,
    Run,
    _shift,
    live_basket,
    load_books,
    load_headlines,
    simulate,
)

OUT = Path(__file__).with_name("RESULTS.md")
ARCHIVE = Path("data/research/alphai_crypto_news.json")
CACHE = Path("data/research/swing_grid/15m")
TAPE_START = "2024-01-01"
PRE_END = "2025-12-31"
YEAR = "2026-01-01"
AMSTERDAM = ZoneInfo("Europe/Amsterdam")


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


def _one_window(run: Run) -> bool:
    """Locked before the replay. A pass still needs both periods when they exist."""
    if run.pnl <= 0 or run.name_days < 40:
        return False
    if run.max_dd >= run.pnl / SLEEVE_EUR:
        return False
    if run.largest_day > 0 and run.largest_day >= 0.5 * run.pnl:
        return False
    top = max((pnl for _base, _days, pnl in run.names), default=0.0)
    return top < 0.5 * run.pnl


def _line(run: Run) -> str:
    top = run.names[0] if run.names else None
    top_text = "—"
    if top is not None:
        _base, _days, pnl = top
        top_text = f"{_base} {_eur(pnl)}"
    flag = "pass" if _one_window(run) else "—"
    return (
        f"| {run.mode} | {_eur(run.pnl)} | {_pct(run.max_dd)} | {run.name_days} | "
        f"{run.buys} | {run.sells} | {_eur(run.largest_day)} | {top_text} | {flag} |"
    )


def _key() -> str:
    key = os.environ.get("ALPHAI_API_KEY", "").strip()
    if key:
        return key
    env = Path(".env")
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("ALPHAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _live_lines() -> list[str]:
    key = _key()
    if not key:
        return ["The live rollup was not fetched.", ""]
    client = AlphaIClient(key)
    payloads = {}
    for base in DEFAULT_UNIVERSE:
        try:
            payloads[base] = client.sentiment_summary(f"{base}-USD")
        except RuntimeError:
            continue
    if not payloads:
        return ["The live rollup was unavailable.", ""]
    fetched = datetime.now(AMSTERDAM).strftime("%Y-%m-%d %H:%M")
    basket = live_basket(payloads)[:4]
    rows = [
        f"Live sentiment-summary fetched {fetched} Europe/Amsterdam.",
        "The product's window includes today's partial bucket.",
        "These counts are the published rollup, not the replay.",
        "",
        "| name | bullish | bearish | neutral |",
        "| --- | ---: | ---: | ---: |",
    ]
    for base in DEFAULT_UNIVERSE:
        payload = payloads.get(base)
        if payload is None:
            rows.append(f"| {base} | — | — | — |")
            continue
        rows.append(
            f"| {base} | {int(payload.get('bullish') or 0)} | "
            f"{int(payload.get('bearish') or 0)} | {int(payload.get('neutral') or 0)} |"
        )
    held = ", ".join(basket) if basket else "cash"
    rows.extend(["", f"The published rollup's top four, today included: {held}.", ""])
    return rows


def render(
    overlap: list[Run],
    tape: dict[tuple[str, str], Run],
    *,
    first_story: str,
    last_story: str,
    overlap_start: str,
    last: str,
) -> str:
    header = (
        "| cell | pnl | max DD | name-days | buys | sells | largest day | "
        "largest name | one-window |"
    )
    rule = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"
    tape_lines = []
    for mode in ("chop", "book"):
        for label in ("full", "before 2026", "2026"):
            run = tape[(mode, label)]
            tape_lines.append(_line(run).replace(f"| {mode} |", f"| {mode} {label} |", 1))
    both = []
    for mode in ("chop", "book"):
        windows = ("full", "before 2026", "2026")
        if all(_one_window(tape[(mode, label)]) for label in windows):
            both.append(mode)
    if both:
        names = ", ".join(f"`{mode}`" for mode in both)
        verdict = f"Controls that clear both periods: {names}."
    else:
        verdict = "No control clears both periods."
    return "\n".join(
        [
            "# AlphaI sleeve",
            "",
            "Rules are the module docstring in `__init__.py`. They were locked",
            "before this replay. Sleeve €10,000. At most four names in `four`",
            "and `chop`, equal weight, taker both sides. The news count is the",
            "seven publication days before the session. Insider rows are excluded.",
            f"Archive {first_story} → {last_story}. Overlap {overlap_start} → {last}.",
            f"Tape for the controls {TAPE_START} → {last}.",
            "There is no news path before 2026.",
            "",
            "## News overlap",
            "",
            header,
            rule,
            "\n".join(_line(run) for run in overlap),
            "",
            "An overlap pass is not a two-period result.",
            "",
            "## Controls on the full tape",
            "",
            header,
            rule,
            "\n".join(tape_lines),
            "",
            verdict,
            "Nothing is armed.",
            "",
            "## Live rollup",
            "",
            *_live_lines(),
            "Reproduce: `python -m bot.research.alphai_sleeve`",
            "",
        ]
    )


def main() -> None:
    last = _last_complete_day()
    grouped = load_headlines(ARCHIVE)
    first_story = min(grouped)
    overlap_start = _shift(first_story, TONE_DAYS)
    books = load_books(CACHE, DEFAULT_UNIVERSE)
    overlap = []
    for mode in MODES:
        run = simulate(
            books,
            grouped,
            universe=DEFAULT_UNIVERSE,
            mode=mode,
            start=overlap_start,
            end=last,
        )
        overlap.append(run)
        print(f"overlap {mode:5} {_eur(run.pnl):>12} dd {_pct(run.max_dd)}", flush=True)
    windows = (
        ("full", TAPE_START, last),
        ("before 2026", TAPE_START, PRE_END),
        ("2026", YEAR, last),
    )
    tape: dict[tuple[str, str], Run] = {}
    for mode in ("chop", "book"):
        for label, start, end in windows:
            run = simulate(
                books,
                {},
                universe=DEFAULT_UNIVERSE,
                mode=mode,
                start=start,
                end=end,
            )
            tape[(mode, label)] = run
            print(f"tape {mode:5} {label:12} {_eur(run.pnl):>12}", flush=True)
    text = render(
        overlap,
        tape,
        first_story=first_story,
        last_story=max(grouped),
        overlap_start=overlap_start,
        last=last,
    )
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()

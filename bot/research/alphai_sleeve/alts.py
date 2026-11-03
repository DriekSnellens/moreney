"""Replay the sleeve on every Bitvavo EUR alt.

Nothing is armed. Reproduce with ``python -m bot.research.alphai_sleeve.alts``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.alphai_sleeve.book import (
    MIN_QUOTE_EUR,
    SLEEVE_EUR,
    TONE_DAYS,
    Run,
    _shift,
    load_books,
    load_headlines,
    simulate,
)
from bot.research.alphai_sleeve.universe import (
    DAILY,
    LISTED_MIN_QUOTE,
    alt_bases,
    ensure_daily,
    fetch_markets,
)

OUT = Path(__file__).with_name("ALTS.md")
ARCHIVE = Path("data/research/alphai_crypto_news.json")
TAPE_START = "2024-01-01"
PRE_END = "2025-12-31"
YEAR = "2026-01-01"


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
    if run.pnl <= 0 or run.name_days < 40:
        return False
    if run.max_dd >= run.pnl / SLEEVE_EUR:
        return False
    if run.largest_day > 0 and run.largest_day >= 0.5 * run.pnl:
        return False
    top = max((pnl for _base, _days, pnl in run.names), default=0.0)
    return top < 0.5 * run.pnl


def _names(run: Run) -> str:
    held = [base for base, days, _pnl in run.names if days]
    top = ", ".join(f"{base} {_eur(pnl)}" for base, _days, pnl in run.names[:4]) or "—"
    return f"{len(held)} | {top}"


def _line(label: str, run: Run) -> str:
    flag = "pass" if _one_window(run) else "—"
    return (
        f"| {label} | {_eur(run.pnl)} | {_pct(run.max_dd)} | {run.name_days} | "
        f"{run.buys} | {_eur(run.largest_day)} | {_names(run)} | {flag} |"
    )


def _run(
    books: dict,
    grouped: dict,
    *,
    universe: tuple[str, ...],
    mode: str,
    start: str,
    end: str,
    min_quote: float,
) -> Run:
    return simulate(
        books,
        grouped,
        universe=universe,
        mode=mode,
        start=start,
        end=end,
        min_bars=1,
        min_quote=min_quote,
    )


def render(
    overlap: list[tuple[str, Run]],
    tape: list[tuple[str, Run]],
    *,
    n_listed: int,
    n_cached: int,
    first_story: str,
    last_story: str,
    overlap_start: str,
    last: str,
) -> str:
    passed = [label for label, run in tape if label.endswith(" full") and _both_label(label, tape)]
    if passed:
        verdict = "Full-tape pairs that clear both periods: " + ", ".join(passed) + "."
    else:
        verdict = "No full-tape cell clears both periods."
    header = (
        "| cell | pnl | max DD | name-days | buys | largest day | "
        "names | largest | one-window |"
    )
    rule = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"
    return "\n".join(
        [
            "# Alt-market sleeve",
            "",
            "Rules are the module docstring in `__init__.py`. This grid was",
            "locked before the replay. The universe is every trading Bitvavo",
            f"EUR alt. Listed bases {n_listed}, with a daily cache for {n_cached}.",
            "BTC and the cash pegs are outside it. Daily candles, taker both",
            f"sides, sleeve €{SLEEVE_EUR:,.0f}.",
            f"Archive {first_story} → {last_story}. Overlap {overlap_start} → {last}.",
            "There is no news path before 2026.",
            "",
            "## News overlap",
            "",
            header,
            rule,
            "\n".join(_line(label, run) for label, run in overlap),
            "",
            "An overlap pass is not a two-period result. `desk` is the sixteen",
            "large names on this same daily tape. `liquid` keeps the €100,000",
            "quote floor. `listed` lowers that floor to €10,000.",
            "",
            "## Full tape, no news",
            "",
            header,
            rule,
            "\n".join(_line(label, run) for label, run in tape),
            "",
            verdict,
            "Nothing is armed.",
            "",
            "Reproduce: `python -m bot.research.alphai_sleeve.alts`",
            "",
        ]
    )


def _both_label(full_label: str, tape: list[tuple[str, Run]]) -> bool:
    prefix = full_label.removesuffix(" full")
    wanted = {f"{prefix} {label}" for label in ("full", "before 2026", "2026")}
    found = {label: run for label, run in tape if label in wanted}
    if set(found) != wanted:
        return False
    return all(_one_window(found[label]) for label in wanted)


def main() -> None:
    last = _last_complete_day()
    markets = fetch_markets()
    listed = alt_bases(markets)
    print(f"listed alts {len(listed)}", flush=True)
    cached = ensure_daily(listed)
    print(f"daily cache {cached}", flush=True)
    books = load_books(DAILY, listed)
    grouped = load_headlines(ARCHIVE)
    first_story = min(grouped)
    overlap_start = _shift(first_story, TONE_DAYS)
    desk = tuple(base for base in DEFAULT_UNIVERSE if base in books)
    overlap: list[tuple[str, Run]] = []
    jobs = (
        ("desk four", desk, "four", MIN_QUOTE_EUR),
        ("desk all", desk, "all", MIN_QUOTE_EUR),
        ("liquid four", listed, "four", MIN_QUOTE_EUR),
        ("liquid all", listed, "all", MIN_QUOTE_EUR),
        ("listed four", listed, "four", LISTED_MIN_QUOTE),
        ("listed all", listed, "all", LISTED_MIN_QUOTE),
        ("listed chop", listed, "chop", LISTED_MIN_QUOTE),
        ("listed book", listed, "book", LISTED_MIN_QUOTE),
    )
    for label, universe, mode, floor in jobs:
        run = _run(
            books,
            grouped,
            universe=universe,
            mode=mode,
            start=overlap_start,
            end=last,
            min_quote=floor,
        )
        overlap.append((label, run))
        print(f"overlap {label:14} {_eur(run.pnl):>12}", flush=True)
    windows = (
        ("full", TAPE_START, last),
        ("before 2026", TAPE_START, PRE_END),
        ("2026", YEAR, last),
    )
    tape: list[tuple[str, Run]] = []
    for label, mode, floor in (
        ("listed book", "book", LISTED_MIN_QUOTE),
        ("listed chop", "chop", LISTED_MIN_QUOTE),
        ("liquid book", "book", MIN_QUOTE_EUR),
    ):
        for window, start, end in windows:
            run = _run(
                books,
                {},
                universe=listed,
                mode=mode,
                start=start,
                end=end,
                min_quote=floor,
            )
            tape.append((f"{label} {window}", run))
            print(f"tape {label:14} {window:12} {_eur(run.pnl):>12}", flush=True)
    text = render(
        overlap,
        tape,
        n_listed=len(listed),
        n_cached=len(books),
        first_story=first_story,
        last_story=max(grouped),
        overlap_start=overlap_start,
        last=last,
    )
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()

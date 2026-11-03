"""Replay the sentiment-gated bounce and write RESULTS.md.

Nothing is armed. Reproduce with ``python -m bot.research.sentiment_dip``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.sentiment_dip.gate import (
    MODES,
    TONE_DAYS,
    Run,
    _shift,
    load_headlines,
    simulate,
)
from bot.research.swing_grid.bars import load_cache
from bot.research.swing_grid.grid import BOOK_EUR, sessions_from_rows

OUT = Path(__file__).with_name("RESULTS.md")
ARCHIVE = Path("data/research/alphai_crypto_news.json")
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


def _one_window(runs: list[Run]) -> str:
    """Same drawdown and sample checks as the swing bar, on this overlap only."""
    held: list[str] = []
    for run in runs:
        one_day = run.largest_day <= 0 or run.largest_day < 0.5 * run.pnl
        drawdown = run.max_dd < (run.pnl / BOOK_EUR) if run.pnl > 0 else False
        if run.pnl > 0 and run.maker_exits >= 40 and one_day and drawdown:
            held.append(run.mode)
    if not held:
        return "No cell clears even that one-window check."
    names = ", ".join(f"`{mode}`" for mode in held)
    return f"One-window check only, still not a two-period pass: {names}."


def _line(run: Run) -> str:
    names = ", ".join(f"{base} {count}" for base, count in run.picks[:6]) or "—"
    return (
        f"| {run.mode} | {_eur(run.pnl)} | {_pct(run.max_dd)} | {run.selected} | "
        f"{run.filled} | {run.maker_exits} | {run.flattens} | {_eur(run.maker_pnl)} | "
        f"{_eur(run.flatten_pnl)} | {_eur(run.largest_day)} | {names} |"
    )


def render(runs: list[Run], first_story: str, last_story: str, start: str, end: str) -> str:
    by_mode = {run.mode: run for run in runs}
    tone = by_mode["tone"]
    header = (
        "| cell | pnl | max DD | days selected | days filled | maker exits | "
        "flattens | maker pnl | flatten pnl | largest day | names |"
    )
    rule = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    return "\n".join(
        (
            "# Sentiment dip",
            "",
            "Rules are the module docstring in `__init__.py`. `tone`, `block`, and",
            "`plain` were locked before their first replay. `rank` was locked after",
            "that reading and before its own. The order is the 1% bid above today's",
            "printed low, sold 5% higher. Sentiment is AlphaI's live score on the",
            f"seven publication days before the session. Archive {first_story} → {last_story}.",
            f"Replay {start} → {end}. Book €{BOOK_EUR:,.0f}.",
            "The archive does not reach before 2026.",
            "",
            header,
            rule,
            "\n".join(_line(run) for run in runs),
            "",
            "## Reading",
            "",
            "`tone` buys the dip only on a name sentiment marks as a buy. "
            f"It finishes {_eur(tone.pnl)}.",
            "`block` skips the choppiest name when sentiment says it is not a buy. "
            f"It finishes {_eur(by_mode['block'].pnl)}.",
            "`plain` is the same bounce with the wire ignored. "
            f"It finishes {_eur(by_mode['plain'].pnl)}.",
            "`rank` keeps the buy rule and takes the highest score instead of the "
            f"choppest name. It finishes {_eur(by_mode['rank'].pnl)}.",
            "A missing headline is not a buy under `tone` or `rank`. "
            "It is still allowed under `block`.",
            "There is no path before 2026, so this overlap cannot clear a two-period bar.",
            _one_window(runs),
            "Nothing is armed.",
            "",
            "Reproduce: `python -m bot.research.sentiment_dip`",
            "",
        )
    )


def main() -> None:
    end = _last_complete_day()
    grouped = load_headlines(ARCHIVE)
    first_story = min(grouped)
    start = _shift(first_story, TONE_DAYS)
    raw = load_cache(CACHE, DEFAULT_UNIVERSE)
    books = {base: sessions_from_rows(rows) for base, rows in raw.items()}
    runs = [
        simulate(
            books,
            grouped,
            universe=DEFAULT_UNIVERSE,
            mode=mode,
            start=start,
            end=end,
        )
        for mode in MODES
    ]
    for run in runs:
        print(
            f"{run.mode:8} {_eur(run.pnl):>12} dd {_pct(run.max_dd):>6} "
            f"sel {run.selected} fill {run.filled}",
            flush=True,
        )
    text = render(runs, first_story, max(grouped), start, end)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()

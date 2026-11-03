"""Replay the locked swing grid on Bitvavo 15-minute bars and write RESULTS.md.

Nothing is armed. Reproduce with ``python -m bot.research.swing_grid``.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.swing_grid.bars import ensure_candles
from bot.research.swing_grid.grid import MODES, Run, sessions_from_rows, simulate

OUT = Path(__file__).with_name("RESULTS.md")
CACHE = Path("data/research/swing_grid/15m")
START = "2024-01-01"
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
    today = datetime.now(UTC).date()
    return (today - timedelta(days=1)).isoformat()


def _windows(last: str) -> tuple[tuple[str, str, str], ...]:
    return (
        ("full", START, last),
        ("before 2026", START, PRE_END),
        ("2026", YEAR, last),
    )


def _line(label: str, run: Run) -> str:
    return (
        f"| {run.mode} | {label} | {_eur(run.pnl)} | {_pct(run.max_dd)} | "
        f"{run.selected} | {run.filled} | {run.maker_exits} | {run.flattens} | "
        f"{_eur(run.maker_pnl)} | {_eur(run.flatten_pnl)} | {_eur(run.largest_day)} |"
    )


def _reading(runs: dict[tuple[str, str], Run]) -> str:
    strict = {label: runs[("strict", label)] for label in ("full", "before 2026", "2026")}
    hold = runs[("hold", "full")]
    oracle = runs[("oracle", "full")]
    path = runs[("path", "full")]
    retest = runs[("retest", "full")]
    bounce5 = runs[("bounce5", "full")]
    bounce10 = runs[("bounce10", "full")]
    return "\n".join(
        (
            "The headline is `strict`: limits from the prior five sessions, and no",
            "round trip inside a single 15-minute bar. On a €20,000 book restarted",
            f"for each window it finishes {_eur(strict['full'].pnl)} on the full path,",
            f"{_eur(strict['before 2026'].pnl)} before 2026, and {_eur(strict['2026'].pnl)}",
            "in 2026.",
            "",
            f"Completed maker swings on the full path contribute {_eur(strict['full'].maker_pnl)}.",
            f"Inventory sold at the close contributes {_eur(strict['full'].flatten_pnl)}.",
            f"The largest single day is {_eur(strict['full'].largest_day)}.",
            "`path` allows a same-bar round trip when the bar closes up and",
            f"finishes {_eur(path.pnl)}.",
            "`retest` buys a retest of extremes that have already printed and",
            f"finishes {_eur(retest.pnl)}.",
            "`bounce5` bids 1% above the low already printed today and sells",
            f"5% above that fill. Full path {_eur(bounce5.pnl)}, maker",
            f"{_eur(bounce5.maker_pnl)}, close-outs {_eur(bounce5.flatten_pnl)}.",
            "`bounce10` uses the same bid and sells 10% above the fill.",
            f"Full path {_eur(bounce10.pnl)}, maker {_eur(bounce10.maker_pnl)},",
            f"close-outs {_eur(bounce10.flatten_pnl)}.",
            "`confirm5` waits until a later bar holds the low. It finishes",
            f"{_eur(runs[('confirm5', 'full')].pnl)} on the full path,",
            f"{_eur(runs[('confirm5', 'before 2026')].pnl)} before 2026, and",
            f"{_eur(runs[('confirm5', '2026')].pnl)} in 2026.",
            "`stop5` sells a break 2% through that low. It finishes",
            f"{_eur(runs[('stop5', 'full')].pnl)},",
            f"{_eur(runs[('stop5', 'before 2026')].pnl)} before 2026, and",
            f"{_eur(runs[('stop5', '2026')].pnl)} in 2026.",
            "`shallow5` skips a low more than 4% under the open. It finishes",
            f"{_eur(runs[('shallow5', 'full')].pnl)},",
            f"{_eur(runs[('shallow5', 'before 2026')].pnl)} before 2026, and",
            f"{_eur(runs[('shallow5', '2026')].pnl)} in 2026.",
            "`hold3` keeps the fill for up to three sessions. It finishes",
            f"{_eur(runs[('hold3', 'full')].pnl)},",
            f"{_eur(runs[('hold3', 'before 2026')].pnl)} before 2026, and",
            f"{_eur(runs[('hold3', '2026')].pnl)} in 2026.",
            "`hold` buys that same name at the open and sells the close and",
            f"finishes {_eur(hold.pnl)}.",
            "The oracle ceiling buys the day's low and sells the day's high and",
            f"finishes {_eur(oracle.pnl)}.",
            "That ceiling uses the session extremes and is not a strategy.",
            "",
            "Nothing is armed. The first cells were locked before their replay.",
            "The 1% cushion and the 5% and 10% sells were locked before the",
            "second replay. None of those numbers were moved after the result.",
        )
    )


def _coverage(books: dict[str, list]) -> str:
    lines = ["| base | sessions | first | last |", "| --- | ---: | --- | --- |"]
    for base in DEFAULT_UNIVERSE:
        rows = books.get(base) or []
        if not rows:
            lines.append(f"| {base} | 0 | — | — |")
            continue
        lines.append(f"| {base} | {len(rows)} | {rows[0].day} | {rows[-1].day} |")
    return "\n".join(lines)


def _picks(run: Run) -> str:
    if not run.picks:
        return "No name was selected."
    head = ", ".join(f"{base} {count}" for base, count in run.picks[:8])
    return f"Strict full-path selections: {head}."


def render(runs: dict[tuple[str, str], Run], books: dict[str, list], last: str) -> str:
    header = (
        "| cell | window | pnl | max DD | days selected | days filled | "
        "maker exits | flattens | maker pnl | flatten pnl | largest day |"
    )
    rule = "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    rows = [
        _line(label, runs[(mode, label)])
        for mode in MODES
        for label, _start, _end in _windows(last)
    ]
    body = "\n".join(rows)
    return "\n".join(
        (
            "# Swing grid",
            "",
            "Rules are the module docstring in `__init__.py`. They were locked before",
            "this replay. Fills are 15-minute Bitvavo EUR bars. Maker 0.15% at the",
            "limit. A close-out is taker 0.25% plus impact",
            "`0.02 * sqrt(notional / median quote volume)`, capped at 3%.",
            "Each window restarts at €20,000. The in-progress UTC day is dropped.",
            "",
            f"Last complete session: {last}.",
            "",
            header,
            rule,
            body,
            "",
            "## Reading",
            "",
            _reading(runs),
            "",
            _picks(runs[("strict", "full")]),
            "",
            "## Coverage",
            "",
            _coverage(books),
            "",
            "Reproduce: `python -m bot.research.swing_grid`",
            "",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default=str(CACHE))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()
    last = _last_complete_day()
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int(datetime.fromisoformat(START).replace(tzinfo=UTC).timestamp() * 1000)
    raw = ensure_candles(
        DEFAULT_UNIVERSE,
        Path(args.cache_dir),
        start_ms=start_ms,
        end_ms=end_ms,
    )
    books = {base: sessions_from_rows(rows) for base, rows in raw.items()}
    runs: dict[tuple[str, str], Run] = {}
    for mode in MODES:
        for label, start, end in _windows(last):
            run = simulate(books, universe=DEFAULT_UNIVERSE, start=start, end=end, mode=mode)
            runs[(mode, label)] = run
            print(
                f"{mode:8} {label:12} {_eur(run.pnl):>12} dd {_pct(run.max_dd):>6} "
                f"sel {run.selected} fill {run.filled}",
                flush=True,
            )
    text = render(runs, books, last)
    Path(args.out).write_text(text)
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()

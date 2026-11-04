"""Search the best strategy for each news sentiment, then trade the later days.

Bullish and bearish are separate. The menu is long or short, for 24 hours or
7 days, with or without the 6% stop, or flat. A strategy is eligible once 20
of its trades have already exited and its mean net is positive. The halfway
cut of the vote span is fixed: the search sees only trades that exited before
that cut, and the second half trades the frozen mapping. A walk-forward does
the same search at each entry using only trades that have closed.

The menu and the cut were set before the run. Nothing is armed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.alphai_sentiment_accuracy.payoff import (
    BOOK_EUR,
    FEE,
    _ohlc,
    entry_index,
    slot_book_compound,
    walk_exit,
)
from bot.research.alphai_sentiment_accuracy.score import HORIZONS
from bot.research.alphai_sentiment_accuracy.selector import MIN_MARKET

_H = dict(HORIZONS)


@dataclass(frozen=True)
class Strat:
    name: str
    horizon: int
    side: int
    stop: float


# Earlier names win an exact tie. Flat wins when nothing is strictly positive.
MENU: tuple[Strat, ...] = (
    Strat("long 24h", _H["24h"], 1, 0.0),
    Strat("short 24h", _H["24h"], -1, 0.0),
    Strat("long 7d", _H["7d"], 1, 0.0),
    Strat("short 7d", _H["7d"], -1, 0.0),
    Strat("long 7d stop 6%", _H["7d"], 1, 0.06),
    Strat("short 7d stop 6%", _H["7d"], -1, 0.06),
)
MIN_COMPLETED = MIN_MARKET
OUT_PATH = Path("bot/research/alphai_sentiment_accuracy/SEARCH.md")


@dataclass(frozen=True)
class Outcome:
    entry_ms: int
    exit_ms: int
    base: str
    label_side: int
    strategy: str
    net: float


def choose_side(outcomes: list[Outcome], label_side: int, as_of: int) -> str:
    """Best eligible strategy for one sentiment, using exits at or before ``as_of``."""
    best = "flat"
    best_mean = 0.0
    for strat in MENU:
        nets = [
            row.net
            for row in outcomes
            if row.label_side == label_side and row.strategy == strat.name and row.exit_ms <= as_of
        ]
        if len(nets) < MIN_COMPLETED:
            continue
        mean = float(np.mean(nets))
        if mean > best_mean:
            best = strat.name
            best_mean = mean
    return best


def time_cut(entry_times: list[int]) -> int:
    """Halfway between the first and last vote midnight."""
    first = min(entry_times)
    last = max(entry_times)
    return first + (last - first) // 2


def _votes(outcomes: list[Outcome]) -> list[tuple[int, str, int]]:
    seen: dict[tuple[int, str], int] = {}
    for row in outcomes:
        seen.setdefault((row.entry_ms, row.base), row.label_side)
    return [(ms, base, side) for (ms, base), side in sorted(seen.items())]


def _index(outcomes: list[Outcome]) -> dict[tuple[int, str, str], Outcome]:
    return {(row.entry_ms, row.base, row.strategy): row for row in outcomes}


def frozen_trades(outcomes: list[Outcome], cut: int) -> tuple[dict[int, str], list[Outcome]]:
    """Fit on exits before ``cut``. Deploy that map on entries at or after ``cut``."""
    mapping = {side: choose_side(outcomes, side, cut) for side in (1, -1)}
    found = _index(outcomes)
    deployed: list[Outcome] = []
    for ms, base, side in _votes(outcomes):
        if ms < cut:
            continue
        name = mapping[side]
        if name == "flat":
            continue
        row = found.get((ms, base, name))
        if row is not None:
            deployed.append(row)
    return mapping, deployed


def walk_forward_trades(outcomes: list[Outcome]) -> list[Outcome]:
    """Refit at each entry from trades of that sentiment that have already closed."""
    found = _index(outcomes)
    deployed: list[Outcome] = []
    for ms, base, side in _votes(outcomes):
        name = choose_side(outcomes, side, ms)
        if name == "flat":
            continue
        row = found.get((ms, base, name))
        if row is not None:
            deployed.append(row)
    return deployed


def _mean(rows: list[Outcome]) -> float:
    if not rows:
        return 0.0
    return float(np.mean([row.net for row in rows]))


def _book_line(name: str, deployed: list[Outcome]) -> str:
    triples = [(row.entry_ms, row.exit_ms, row.base, row.net) for row in deployed]
    taken, end, max_dd = slot_book_compound(triples)
    ret = end / BOOK_EUR - 1.0
    return (
        f"| {name} | {len(deployed)} | {taken} | {_mean(deployed) * 100:+.3f}% | "
        f"{end:,.2f} | {100 * ret:+.1f}% | {100 * max_dd:.1f}% |"
    )


def _side_name(side: int) -> str:
    return "bullish" if side > 0 else "bearish"


def _load() -> tuple[list[Outcome], int]:
    from bot.research.alphai_sentiment_accuracy.__main__ import (  # noqa: PLC0415
        BARS,
        _daily_votes,
        _load_calls,
    )

    calls, _directional, _neutral = _load_calls()
    missing = 0
    outcomes: list[Outcome] = []
    for base, rows in sorted(calls.items()):
        path = BARS / f"{base}.json"
        if not path.exists():
            missing += len(rows)
            continue
        ts, opens, highs, lows, closes = _ohlc(path)
        for midnight, side in _daily_votes(rows):
            for strat in MENU:
                i = entry_index(ts, midnight, strat.horizon)
                if i is None:
                    continue
                fill = walk_exit(
                    ts,
                    opens,
                    highs,
                    lows,
                    closes,
                    i,
                    strat.side,
                    strat.stop,
                    0.0,
                    strat.horizon,
                    FEE,
                )
                if fill is None:
                    continue
                outcomes.append(Outcome(midnight, fill.exit_ms, base, side, strat.name, fill.net))
    return outcomes, missing


def _train_rows(outcomes: list[Outcome], cut: int) -> list[str]:
    lines = [
        "| sentiment | strategy | n | mean net | chosen |",
        "|---|---|---:|---:|---|",
    ]
    for side in (1, -1):
        chosen = choose_side(outcomes, side, cut)
        for strat in MENU:
            nets = [
                row.net
                for row in outcomes
                if row.label_side == side and row.strategy == strat.name and row.exit_ms <= cut
            ]
            mark = "yes" if strat.name == chosen else ""
            mean = f"{100 * float(np.mean(nets)):+.3f}%" if nets else "—"
            lines.append(f"| {_side_name(side)} | {strat.name} | {len(nets)} | {mean} | {mark} |")
        if chosen == "flat":
            lines.append(f"| {_side_name(side)} | flat | — | — | yes |")
    return lines


def _reading(held: list[Outcome], held_end: float, forward_end: float) -> str:
    return (
        f"The frozen mapping averages {_mean(held) * 100:+.3f}% per signal on the "
        f"second half. Its four-slot book ends at €{held_end:,.2f}. "
        f"The walk-forward book ends at €{forward_end:,.2f}. "
        "The search is implemented. It is not wired into the live bot."
    )


def main() -> None:
    outcomes, missing = _load()
    entries = [row.entry_ms for row in outcomes]
    cut = time_cut(entries) if entries else 0
    mapping, held = frozen_trades(outcomes, cut)
    forward = walk_forward_trades(outcomes)
    _taken, forward_end, _dd = slot_book_compound(
        [(row.entry_ms, row.exit_ms, row.base, row.net) for row in forward]
    )
    if entries:
        first = datetime.fromtimestamp(min(entries) / 1000, UTC).date()
        last = datetime.fromtimestamp(max(entries) / 1000, UTC).date()
        cut_day = datetime.fromtimestamp(cut / 1000, UTC).date()
        span = f"{first.isoformat()} to {last.isoformat()}, cut {cut_day.isoformat()}"
    else:
        span = "—"
    n_votes = len(_votes(outcomes))
    lines = [
        "# Search a strategy for each news sentiment",
        "",
        "One majority vote per coin per UTC day. Bullish and bearish are searched",
        "separately. The menu is long or short, held 24 hours or 7 days, and the",
        "7-day holds again with a 6% stop. Flat is the choice when no strategy",
        f"has {MIN_COMPLETED} completed trades and a positive mean net.",
        "",
        "The cut is halfway between the first and last vote midnight. Training",
        "uses only trades that have exited before the cut. Entries on or after",
        "the cut trade that frozen mapping. The walk-forward refits at each",
        "entry from closed trades of the same sentiment. Fees are the Bitvavo",
        "taker on both sides. The euro book is four slots starting at €20,000,",
        "resized with equity, skipping a coin that is already open.",
        "The menu and the cut were fixed before the run. Nothing is armed.",
        "",
        f"Daily votes {n_votes}. Directional rows with no 15m file: {missing}.",
        f"Vote span {span}.",
        f"Frozen map: bullish `{mapping.get(1, 'flat')}`, bearish `{mapping.get(-1, 'flat')}`.",
        "",
        "### Training means, trades that exited before the cut",
        "",
        *_train_rows(outcomes, cut),
        "",
        "### Deployed on trades the search had not seen",
        "",
        "Mean net is every deployed signal. The euro columns are the four-slot",
        "book, which skips a coin already open and resizes the next slot.",
        "",
        "| book | signals | slotted | mean net | end € | return | max DD |",
        "|---|---:|---:|---:|---:|---:|---:|",
        _book_line("frozen map, second half", held),
        _book_line("walk-forward, full span", forward),
        "",
        "### Second half by sentiment",
        "",
        "| sentiment | signals | mean net |",
        "|---|---:|---:|",
    ]
    for side in (1, -1):
        rows = [row for row in held if row.label_side == side]
        mean = f"{_mean(rows) * 100:+.3f}%" if rows else "—"
        lines.append(f"| {_side_name(side)} | {len(rows)} | {mean} |")
    counts: dict[tuple[str, str], int] = defaultdict(int)
    found = _index(outcomes)
    for ms, base, side in _votes(outcomes):
        name = choose_side(outcomes, side, ms)
        if found.get((ms, base, name)) is None and name != "flat":
            name = "flat"
        counts[(_side_name(side), name)] += 1
    lines += [
        "",
        "### Walk-forward choices",
        "",
        "| sentiment | strategy | votes |",
        "|---|---|---:|",
    ]
    for (sentiment, name), n in sorted(counts.items()):
        lines.append(f"| {sentiment} | {name} | {n} |")
    _held_taken, held_end, _held_dd = slot_book_compound(
        [(row.entry_ms, row.exit_ms, row.base, row.net) for row in held]
    )
    lines += ["", _reading(held, held_end, forward_end), ""]
    text = "\n".join(lines)
    OUT_PATH.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

"""Asymmetric exit on the AlphaI labels: cap the loss, do not cap the profit.

The entry is the same next 15-minute open as the hit-rate study. There is
no profit target. A hard stop fills inside the bar (at the open when the
bar is already through the stop). A trail, when it is on, is known at the
close and fills on the next open. Anything still open leaves at the horizon
open. Rules below were fixed before the run. Nothing is armed.

Bitvavo spot cannot short. Rows that sell are the same 0.25% taker applied
to a short, so the test is the shape of the payoff. They are not a spot order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from bot.core.venue_fees import VENUE_TAKER_FEE
from bot.research.alphai_sentiment_accuracy.score import BAR_MS, HORIZONS

FEE = float(VENUE_TAKER_FEE["bitvavo"])
SLOT_EUR = 5_000.0
N_SLOTS = 4
BOOK_EUR = N_SLOTS * SLOT_EUR
OUT = Path("bot/research/alphai_sentiment_accuracy/PAYOFF.md")

_H = dict(HORIZONS)


@dataclass(frozen=True)
class Cell:
    name: str
    horizon_bars: int
    stop_pct: float
    trail_pct: float
    book: bool = False
    control: bool = False


# Widths are the locked readings of "cap the loss": 6% echoes the old lock
# floor, 10% echoes the live trail used here only as a loss cap, 3% is the
# tight news stop. Report every width. Do not add one after seeing the result.
CELLS: tuple[Cell, ...] = (
    Cell("hold 24h", _H["24h"], 0.0, 0.0),
    Cell("hold 7d", _H["7d"], 0.0, 0.0, book=True, control=True),
    Cell("stop 3% to 7d", _H["7d"], 0.03, 0.0),
    Cell("stop 6% to 7d", _H["7d"], 0.06, 0.0, book=True, control=True),
    Cell("stop 10% to 7d", _H["7d"], 0.10, 0.0),
    Cell("stop 6% trail 10% to 7d", _H["7d"], 0.06, 0.10, control=True),
)


@dataclass(frozen=True)
class Fill:
    net: float
    gross: float
    reason: str
    exit_ms: int


@dataclass(frozen=True)
class Dealt:
    base: str
    label_side: int
    net: float
    gross: float
    reason: str
    entry_ms: int
    exit_ms: int


def net_return(side: int, entry: float, fill: float, fee: float) -> float:
    """Round trip. Long buys at entry and sells at fill. Short is the mirror."""
    if side > 0:
        return (fill * (1.0 - fee)) / (entry * (1.0 + fee)) - 1.0
    if side < 0:
        return (entry * (1.0 - fee)) / (fill * (1.0 + fee)) - 1.0
    raise ValueError("side must be long or short")


def entry_index(ts: np.ndarray, published_ms: int, horizon_bars: int) -> int | None:
    """First bar at or after the label, if the horizon stays on a 15m grid."""
    if horizon_bars <= 0 or len(ts) == 0:
        return None
    i = int(np.searchsorted(ts, int(published_ms), side="left"))
    if i >= len(ts):
        return None
    if int(ts[i]) - int(published_ms) > BAR_MS:
        return None
    last = i + int(horizon_bars)
    if last >= len(ts):
        return None
    if int(ts[last]) - int(ts[i]) != int(horizon_bars) * BAR_MS:
        return None
    return i


def walk_exit(
    ts: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    i: int,
    side: int,
    stop_pct: float,
    trail_pct: float,
    horizon_bars: int,
    fee: float,
) -> Fill | None:
    """Stop inside the bar, then the peak, then an optional trail.

    The stop is checked before the peak updates, so a wick through the stop
    does not count as a new high. A trail fires on the close and fills at the
    next open. The time exit is the open one horizon after the entry.
    """
    last = i + int(horizon_bars)
    if side == 0 or i < 0 or horizon_bars <= 0 or last >= len(ts):
        return None
    if int(ts[last]) - int(ts[i]) != int(horizon_bars) * BAR_MS:
        return None
    entry = float(opens[i])
    if entry <= 0.0:
        return None
    stop_px = entry * (1.0 - side * stop_pct) if stop_pct > 0.0 else None
    peak = entry
    for k in range(i, last):
        if stop_px is not None and side > 0 and float(lows[k]) <= stop_px:
            opened = float(opens[k])
            fill_px = opened if opened < stop_px else stop_px
            return _fill(side, entry, fill_px, fee, "stop", int(ts[k]))
        if stop_px is not None and side < 0 and float(highs[k]) >= stop_px:
            opened = float(opens[k])
            fill_px = opened if opened > stop_px else stop_px
            return _fill(side, entry, fill_px, fee, "stop", int(ts[k]))
        peak = max(peak, float(highs[k])) if side > 0 else min(peak, float(lows[k]))
        if trail_pct > 0.0 and k + 1 <= last:
            closed = float(closes[k])
            if side > 0 and closed <= peak * (1.0 - trail_pct):
                return _fill(side, entry, float(opens[k + 1]), fee, "trail", int(ts[k + 1]))
            if side < 0 and closed >= peak * (1.0 + trail_pct):
                return _fill(side, entry, float(opens[k + 1]), fee, "trail", int(ts[k + 1]))
    return _fill(side, entry, float(opens[last]), fee, "time", int(ts[last]))


def _fill(
    side: int,
    entry: float,
    fill_px: float,
    fee: float,
    reason: str,
    exit_ms: int,
) -> Fill | None:
    if fill_px <= 0.0:
        return None
    return Fill(
        net=net_return(side, entry, fill_px, fee),
        gross=net_return(side, entry, fill_px, 0.0),
        reason=reason,
        exit_ms=exit_ms,
    )


def slot_book(
    trades: list[tuple[int, int, str, float]],
    *,
    n_slots: int = N_SLOTS,
    slot_eur: float = SLOT_EUR,
) -> tuple[int, float]:
    """Up to ``n_slots`` names. Skip a base that is already open.

    Each taken trade deploys ``slot_eur``. The next trade is not resized.
    A slot is free when its exit is at or before the new entry.
    """
    ordered = sorted(trades, key=lambda row: (row[0], row[2], row[1]))
    open_slots: list[tuple[int, str]] = []
    taken = 0
    pnl = 0.0
    for entry_ms, exit_ms, base, net in ordered:
        open_slots = [(until, name) for until, name in open_slots if until > entry_ms]
        if any(name == base for _, name in open_slots):
            continue
        if len(open_slots) >= n_slots:
            continue
        open_slots.append((exit_ms, base))
        taken += 1
        pnl += float(net) * slot_eur
    return taken, pnl


def _ohlc(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rows = json.loads(path.read_text())
    ts = np.asarray([int(r[0]) for r in rows], dtype=np.int64)
    opens = np.asarray([float(r[1]) for r in rows], dtype=np.float64)
    highs = np.asarray([float(r[2]) for r in rows], dtype=np.float64)
    lows = np.asarray([float(r[3]) for r in rows], dtype=np.float64)
    closes = np.asarray([float(r[4]) for r in rows], dtype=np.float64)
    return ts, opens, highs, lows, closes


def _traded_side(mode: str, label_side: int) -> int:
    if mode == "sentiment":
        return label_side
    if mode == "flip":
        return -label_side
    if mode == "long":
        return 1
    if mode == "short":
        return -1
    raise ValueError(mode)


def _summarize(rows: list[Dealt]) -> dict[str, float]:
    if not rows:
        return {}
    nets = np.asarray([row.net for row in rows], dtype=np.float64)
    gross = np.asarray([row.gross for row in rows], dtype=np.float64)
    reasons = [row.reason for row in rows]
    n = len(rows)
    return {
        "n": float(n),
        "stop": sum(reason == "stop" for reason in reasons) / n,
        "trail": sum(reason == "trail" for reason in reasons) / n,
        "hit": float(np.mean(nets > 0.0)),
        "avg": float(nets.mean()),
        "median": float(np.median(nets)),
        "gross": float(gross.mean()),
        "eur": float(nets.mean()) * SLOT_EUR,
    }


def _fmt_row(name: str, stats: dict[str, float]) -> str:
    if not stats:
        return f"| {name} | 0 | — | — | — | — | — | — | — |"
    return (
        f"| {name} | {int(stats['n'])} | {100 * stats['stop']:.1f}% | "
        f"{100 * stats['trail']:.1f}% | {100 * stats['hit']:.1f}% | "
        f"{100 * stats['gross']:+.3f}% | {100 * stats['avg']:+.3f}% | "
        f"{100 * stats['median']:+.3f}% | {stats['eur']:+.2f} |"
    )


def _header() -> list[str]:
    return [
        "| cell | n | stopped | trail | net>0 | avg gross | avg net | median | € / €5,000 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]


def _per_base(
    rows: list[tuple[int, int, int]],
    ts: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    cell: Cell,
    mode: str,
) -> list[Dealt]:
    base_rows: list[Dealt] = []
    for published_ms, label_side, _rel in rows:
        i = entry_index(ts, published_ms, cell.horizon_bars)
        if i is None:
            continue
        side = _traded_side(mode, label_side)
        fill = walk_exit(
            ts,
            opens,
            highs,
            lows,
            closes,
            i,
            side,
            cell.stop_pct,
            cell.trail_pct,
            cell.horizon_bars,
            FEE,
        )
        if fill is None:
            continue
        base_rows.append(
            Dealt(
                base="",
                label_side=label_side,
                net=fill.net,
                gross=fill.gross,
                reason=fill.reason,
                entry_ms=int(ts[i]),
                exit_ms=fill.exit_ms,
            )
        )
    return base_rows


def _stamp_base(dealt: list[Dealt], base: str) -> list[Dealt]:
    return [
        Dealt(
            base=base,
            label_side=row.label_side,
            net=row.net,
            gross=row.gross,
            reason=row.reason,
            entry_ms=row.entry_ms,
            exit_ms=row.exit_ms,
        )
        for row in dealt
    ]


def _verdict(title: str, rows: list[tuple[str, dict[str, float]]]) -> str:
    scored = [(name, stats) for name, stats in rows if stats]
    winners = [name for name, stats in scored if stats["avg"] > 0.0]
    tails = [
        f"`{name}` averages {100 * stats['avg']:+.3f}% with a median of "
        f"{100 * stats['median']:+.3f}%."
        for name, stats in scored
        if stats["avg"] > 0.0 and stats["median"] <= 0.0
    ]
    tail = (" " + " ".join(tails)) if tails else ""
    if not winners:
        return (
            f"{title}: every locked cell has a negative average net after the "
            "0.25% taker on both sides."
        )
    if len(winners) == 1:
        return (
            f"{title}: only `{winners[0]}` has a positive average net. "
            "The other locked widths do not, so that cell is not a book to trade." + tail
        )
    names = ", ".join(f"`{name}`" for name in winners)
    return (
        f"{title}: positive average net on {names}. "
        "Read those next to always-short and always-long on the same timestamps." + tail
    )


def _book_line(name: str, trades: list[tuple[int, int, str, float]]) -> str:
    taken, pnl = slot_book(trades)
    end = BOOK_EUR + pnl
    return f"| {name} | {taken} | {len(trades)} | {pnl:+.2f} | {end:,.2f} |"


def main() -> None:
    from bot.research.alphai_sentiment_accuracy.__main__ import (  # noqa: PLC0415
        BARS,
        _daily_votes,
        _load_calls,
    )

    calls, directional, neutral = _load_calls()
    missing = 0
    books: dict[tuple[str, str], list[Dealt]] = {}
    for base, rows in sorted(calls.items()):
        path = BARS / f"{base}.json"
        if not path.exists():
            missing += len(rows)
            continue
        ts, opens, highs, lows, closes = _ohlc(path)
        for cell in CELLS:
            modes = ["sentiment"]
            if cell.control:
                modes = ["sentiment", "flip", "long", "short"]
            for mode in modes:
                dealt = _stamp_base(
                    _per_base(rows, ts, opens, highs, lows, closes, cell, mode),
                    base,
                )
                books.setdefault((mode, cell.name), []).extend(dealt)
        for midnight, side in _daily_votes(rows):
            for cell in CELLS:
                if not cell.book:
                    continue
                i = entry_index(ts, midnight, cell.horizon_bars)
                if i is None:
                    continue
                fill = walk_exit(
                    ts,
                    opens,
                    highs,
                    lows,
                    closes,
                    i,
                    side,
                    cell.stop_pct,
                    cell.trail_pct,
                    cell.horizon_bars,
                    FEE,
                )
                if fill is None:
                    continue
                books.setdefault(("daily", cell.name), []).append(
                    Dealt(
                        base=base,
                        label_side=side,
                        net=fill.net,
                        gross=fill.gross,
                        reason=fill.reason,
                        entry_ms=int(ts[i]),
                        exit_ms=fill.exit_ms,
                    )
                )

    sentiment_rows = [
        (cell.name, _summarize(books.get(("sentiment", cell.name), []))) for cell in CELLS
    ]
    spot_rows = []
    for cell in CELLS:
        bulls = [row for row in books.get(("sentiment", cell.name), []) if row.label_side > 0]
        spot_rows.append((cell.name, _summarize(bulls)))
    control_rows: list[tuple[str, dict[str, float]]] = []
    for cell in CELLS:
        if not cell.control:
            continue
        for mode, label in (
            ("flip", "flipped"),
            ("long", "always long"),
            ("short", "always short"),
        ):
            stats = _summarize(books.get((mode, cell.name), []))
            control_rows.append((f"{cell.name}, {label}", stats))

    lines = [
        "# AlphaI sentiment, let the profit run and cap the loss",
        "",
        "Same labels and the same next 15-minute Bitvavo open as the hit-rate",
        "study. No profit target. A long stops when the low touches",
        "entry × (1 − stop); if the open is already through, the fill is the",
        "open. A short is the mirror. The stop is checked before the peak",
        "updates. A trail, where one is on, is known at the close and fills",
        "on the next open. Otherwise the exit is the open at the horizon.",
        "A gap in the 15-minute grid drops the call.",
        "",
        f"Fee is the Bitvavo taker, {100 * FEE:.2f}% on the entry and again",
        "on the exit. Gross is the same fill with a zero fee. The euro column",
        "is the average net on one €5,000 signal. Adding those euros across",
        "overlapping headlines is not an account result: one coin with many",
        "headlines would dominate. The slot book below is the account.",
        "",
        "Bitvavo spot cannot short. The short rows use the same taker so the",
        "payoff shape can be compared. They are not a spot order. Stops of",
        "3%, 6% and 10%, the 24h and 7d holds, and the 10% trail were fixed",
        "before the run. Nothing is armed.",
        "",
        f"Directional labels {directional}. Neutral labels {neutral}.",
        f"Labels with no Bitvavo 15m file: {missing}.",
        "",
        "### Sentiment side, every directional label",
        "",
        "Bullish labels are long. Bearish labels are short.",
        "",
        *_header(),
    ]
    for name, stats in sentiment_rows:
        lines.append(_fmt_row(name, stats))
    lines += [
        "",
        "### Same timestamps, other sides",
        "",
        "Flip sells a bullish label and buys a bearish one. Always-long and",
        "always-short ignore the label and keep the entry time and the exit",
        "rule. When always-short matches the bearish labels, the result is",
        "the tape, not the headline.",
        "",
        *_header(),
    ]
    for name, stats in control_rows:
        lines.append(_fmt_row(name, stats))
    lines += [
        "",
        "### Bullish and bearish labels, separately",
        "",
        "Same sentiment-side fills, split by the label. Bear avg is short",
        "only where the label was bearish.",
        "",
        "| cell | bull n | bull avg net | bear n | bear avg net |",
        "|---|---:|---:|---:|---:|",
    ]
    for cell in CELLS:
        rows = books.get(("sentiment", cell.name), [])
        bulls = [row.net for row in rows if row.label_side > 0]
        bears = [row.net for row in rows if row.label_side < 0]
        bull_txt = f"{100 * float(np.mean(bulls)):+.3f}%" if bulls else "—"
        bear_txt = f"{100 * float(np.mean(bears)):+.3f}%" if bears else "—"
        lines.append(f"| {cell.name} | {len(bulls)} | {bull_txt} | {len(bears)} | {bear_txt} |")
    lines += [
        "",
        "### Bullish labels only",
        "",
        "This is the spot book: long only, and only where the label was bullish.",
        "",
        *_header(),
    ]
    for name, stats in spot_rows:
        lines.append(_fmt_row(name, stats))
    lines += [
        "",
        "### One majority vote per coin per UTC day",
        "",
        "Ties dropped. Entry is the next UTC midnight. One trade per coin",
        "per day, still overlapping across coins. Bullish-only is the spot cut.",
        "",
        *_header(),
    ]
    for cell in CELLS:
        if not cell.book:
            continue
        daily = books.get(("daily", cell.name), [])
        lines.append(_fmt_row(f"{cell.name}, both sides", _summarize(daily)))
        bulls = [row for row in daily if row.label_side > 0]
        lines.append(_fmt_row(f"{cell.name}, bullish only", _summarize(bulls)))
    lines += [
        "",
        f"### €{BOOK_EUR:,.0f} book, {N_SLOTS} slots of €{SLOT_EUR:,.0f}",
        "",
        "Signals in time order. A slot stays on that coin until the exit.",
        "A new headline on an open coin is skipped, and so is a signal when",
        "all four slots are full. Each taken trade is €5,000. Nothing is",
        "resized after a win or a loss. Ties at the same entry time follow",
        "the base name.",
        "",
        "| book | taken | signals | pnl € | end € |",
        "|---|---:|---:|---:|---:|",
    ]
    for cell in CELLS:
        if not cell.book:
            continue
        both = books.get(("sentiment", cell.name), [])
        lines.append(
            _book_line(
                f"{cell.name}, both sides",
                [(row.entry_ms, row.exit_ms, row.base, row.net) for row in both],
            )
        )
        bulls = [row for row in both if row.label_side > 0]
        lines.append(
            _book_line(
                f"{cell.name}, bullish only",
                [(row.entry_ms, row.exit_ms, row.base, row.net) for row in bulls],
            )
        )
        daily = books.get(("daily", cell.name), [])
        lines.append(
            _book_line(
                f"{cell.name}, one vote a day, both sides",
                [(row.entry_ms, row.exit_ms, row.base, row.net) for row in daily],
            )
        )
    lines += [
        "",
        "### Reading",
        "",
        _verdict("Sentiment side", sentiment_rows),
        _verdict("Bullish labels only", spot_rows),
        "",
        "No further stop was tried after this run.",
        "",
    ]
    text = "\n".join(lines)
    OUT.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

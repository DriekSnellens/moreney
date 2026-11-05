"""Buy a new high on a volatile alt, sell the break of the lower channel.

Published 4-hour Donchian tests make money at a 0.05% fee and lose on
Bitcoin once the fee is small and 2025 ranges. This book is the same
idea on Bitvavo daily bars, at the 0.25% taker plus impact, split by
obscure and liquid names and by a 20-day range of at least 20%.

The signal is the close above the prior channel. The buy is the next
open. The sell is the next open after the close breaks the lower
channel, or after the close gives back 15% from its peak since entry.
Three slots, each a third of the book and at most 2% of that day's
quote. Pegs and the benchmark stay out.

A book clears the bar with at least 30 round trips, an end above
€20,000, and a plus with at least eight round trips in each of 2024,
2025 and 2026. Nothing is armed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from bot.research.capital_flow.engine import is_peg
from bot.research.clip_exit_lab.engine import WET, extra_slip
from bot.research.week_book.engine import DAY_MS, Coin

START = 20_000.0
FLOOR = 10_000.0
LARGE = 1_000_000.0
SLOTS = 3
ADV_CAP = 0.02
TRAIL = 0.15
VOL = 0.20
BENCHMARK = "BTC"
TAKER = 0.0025
PAIRS = ((10, 5), (20, 10), (55, 20))
UNIVERSES = (
    ("alles", "alles"),
    ("obscure", "obscure"),
    ("liquide", "liquide"),
    ("obscure_vol", "obscure en volatiel"),
    ("liquide_vol", "liquide en volatiel"),
)
YEARS = (2024, 2025, 2026)
MIN_TRADES = 30
MIN_YEAR_TRADES = 8


@dataclass(frozen=True)
class Spec:
    name: str
    family: str
    universe: str
    entry: int
    exit_n: int
    trail: float = 0.0
    regime: str = ""
    news: str = ""

    def key(self) -> tuple:
        return (self.universe, self.entry, self.exit_n, self.trail, self.regime, self.news)


def grid() -> list[Spec]:
    specs: list[Spec] = []
    for key, label in UNIVERSES:
        for entry, exit_n in PAIRS:
            specs.append(
                Spec(f"{label}, uitbraak {entry}/{exit_n}", "uitbraak", key, entry, exit_n)
            )
            specs.append(
                Spec(
                    f"{label}, uitbraak {entry}, spoor 15%",
                    "uitbraak",
                    key,
                    entry,
                    exit_n,
                    trail=TRAIL,
                )
            )
    specs.append(
        Spec("alles, uitbraak 20/10, marktwaarde omhoog", "cmc", "alles", 20, 10, regime="mcap+")
    )
    specs.append(
        Spec("alles, uitbraak 20/10, marktwaarde omlaag", "cmc", "alles", 20, 10, regime="mcap-")
    )
    for key, label in (("alles", "alles"), ("obscure", "obscure"), ("liquide", "liquide")):
        specs.append(
            Spec(f"{label}, uitbraak 20/10, positief nieuws", "alphai", key, 20, 10, news="pos")
        )
    return specs


@dataclass
class Tape:
    base: str
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    quote: np.ndarray
    idx: dict[int, int]
    prior_max: dict[int, np.ndarray] = field(default_factory=dict)
    prior_min: dict[int, np.ndarray] = field(default_factory=dict)
    width: np.ndarray = field(default_factory=lambda: np.asarray([]))


def tape_of(coin: Coin) -> Tape:
    tape = Tape(
        coin.base, coin.ts, coin.open, coin.high, coin.low, coin.close, coin.quote, coin.idx
    )
    tape.width = _width(tape)
    return tape


def _width(tape: Tape) -> np.ndarray:
    n = len(tape.ts)
    out = np.full(n, np.nan)
    if n < 20:
        return out
    span = tape.ts[19:] - tape.ts[:-19] == 19 * DAY_MS
    hi = np.lib.stride_tricks.sliding_window_view(tape.high, 20).max(axis=1)
    lo = np.lib.stride_tricks.sliding_window_view(tape.low, 20).min(axis=1)
    close = tape.close[19:]
    good = span & (close > 0) & (hi > lo)
    out[19:] = np.where(good, (hi - lo) / close, np.nan)
    return out


def _prior(values: np.ndarray, ts: np.ndarray, n: int, how: str) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) <= n:
        return out
    span = ts[n - 1 : -1] - ts[: len(ts) - n] == (n - 1) * DAY_MS
    window = np.lib.stride_tricks.sliding_window_view(values, n)
    level = window.max(axis=1) if how == "max" else window.min(axis=1)
    # window[i-n] covers values[i-n:i], stored at index i.
    picked = level[: len(values) - n]
    out[n:] = np.where(span, picked, np.nan)
    return out


def prior_max(tape: Tape, n: int) -> np.ndarray:
    cached = tape.prior_max.get(n)
    if cached is None:
        cached = _prior(tape.high, tape.ts, n, "max")
        tape.prior_max[n] = cached
    return cached


def prior_min(tape: Tape, n: int) -> np.ndarray:
    cached = tape.prior_min.get(n)
    if cached is None:
        cached = _prior(tape.low, tape.ts, n, "min")
        tape.prior_min[n] = cached
    return cached


def broke_out(tape: Tape, i: int, n: int) -> float:
    """How far the close cleared the prior channel. NaN when it did not."""
    if i < n or i + 1 >= len(tape.ts):
        return np.nan
    if int(tape.ts[i + 1]) - int(tape.ts[i]) != DAY_MS:
        return np.nan
    level = float(prior_max(tape, n)[i])
    close = float(tape.close[i])
    if not np.isfinite(level) or level <= 0.0 or close <= level:
        return np.nan
    return close / level - 1.0


def _tradable(tape: Tape, i: int) -> bool:
    if i < 6 or float(tape.quote[i]) < FLOOR or float(tape.close[i]) <= 0.0:
        return False
    return not is_peg(tape.close[i - 6 : i + 1])


def _universe(spec: Spec, tape: Tape, i: int) -> bool:
    quote = float(tape.quote[i])
    volatile = np.isfinite(tape.width[i]) and float(tape.width[i]) >= VOL
    if spec.universe == "alles":
        return True
    if spec.universe == "obscure":
        return quote < LARGE
    if spec.universe == "liquide":
        return quote >= LARGE
    if spec.universe == "obscure_vol":
        return quote < LARGE and volatile
    if spec.universe == "liquide_vol":
        return quote >= LARGE and volatile
    raise KeyError(spec.universe)


@dataclass
class Position:
    base: str
    qty: float
    adv: float
    entry_di: int
    peak: float


@dataclass
class Row:
    name: str
    family: str
    end: float
    pnl: float
    dd: float
    trades: int
    year_pnl: dict[int, float]
    year_trades: dict[int, int]
    clears: bool = False


def _buy(cash: float, notional: float, open_: float, adv: float) -> tuple[float, float]:
    slip = extra_slip(notional, adv, WET)
    px = open_ * (1.0 + slip)
    qty = notional / (px * (1.0 + TAKER))
    return cash - notional, qty


def _sell(cash: float, qty: float, level: float, adv: float) -> float:
    gross = qty * level
    slip = extra_slip(gross, adv, WET)
    return cash + qty * level * (1.0 - slip) * (1.0 - TAKER)


def _mark(
    cash: float,
    positions: dict[str, Position],
    tapes: dict[str, Tape],
    stamp: int,
    field: str,
) -> float:
    equity = cash
    for pos in positions.values():
        tape = tapes[pos.base]
        i = tape.idx.get(stamp)
        px = float(getattr(tape, field)[i]) if i is not None else 0.0
        if px <= 0.0:
            px = pos.peak
        equity += pos.qty * px
    return equity


def run(
    tapes: list[Tape],
    spec: Spec,
    dates: np.ndarray,
    *,
    start_ms: int,
    mcap_up: set[int],
    mcap_down: set[int],
    news_pos: set[tuple[str, int]],
    news_from: int,
) -> Row:
    by_base = {tape.base: tape for tape in tapes}
    cash = START
    positions: dict[str, Position] = {}
    pending_exit: set[str] = set()
    pending_entry: list[tuple[float, str, int]] = []
    peak = START
    worst = 0.0
    trades = 0
    year_trades = {year: 0 for year in YEARS}
    year_end: dict[int, float] = {}

    def sell(base: str, level: float, stamp: int) -> None:
        nonlocal cash, trades
        pos = positions.pop(base, None)
        if pos is None or level <= 0.0:
            return
        cash = _sell(cash, pos.qty, level, pos.adv)
        trades += 1
        year = _year(stamp)
        if year in year_trades:
            year_trades[year] += 1

    for di, stamp in enumerate(int(day) for day in dates):
        if stamp < start_ms:
            continue
        for base in list(pending_exit):
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is None:
                continue
            sell(base, float(tape.open[i]), stamp)
        pending_exit.clear()
        equity = _mark(cash, positions, by_base, stamp, "open")
        room = cash
        ranked = sorted(pending_entry, reverse=True)
        for _strength, base, signal_i in ranked:
            if len(positions) >= SLOTS or base in positions:
                continue
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is None or int(tape.ts[i]) - int(tape.ts[signal_i]) != DAY_MS:
                continue
            open_ = float(tape.open[i])
            if open_ <= 0.0:
                continue
            notional = min(equity / SLOTS, ADV_CAP * float(tape.quote[signal_i]), room)
            if notional < 1.0:
                continue
            room -= notional
            cash, qty = _buy(cash, notional, open_, float(tape.quote[signal_i]))
            positions[base] = Position(base, qty, float(tape.quote[signal_i]), di, open_)
        pending_entry = []
        for base, pos in list(positions.items()):
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is None:
                continue
            close = float(tape.close[i])
            if pos.entry_di != di:
                if spec.trail > 0.0:
                    leave = close <= pos.peak * (1.0 - spec.trail)
                else:
                    floor = float(prior_min(tape, spec.exit_n)[i])
                    leave = np.isfinite(floor) and close < floor
                if leave:
                    pending_exit.add(base)
            pos.peak = max(pos.peak, close)
        found: list[tuple[float, str, int]] = []
        allow_regime = _regime_ok(spec, stamp, mcap_up, mcap_down)
        for tape in tapes:
            if tape.base in positions or tape.base in pending_exit:
                continue
            i = tape.idx.get(stamp)
            if i is None or not _tradable(tape, i) or not _universe(spec, tape, i):
                continue
            if not allow_regime or not _news_ok(spec, tape.base, stamp, news_pos, news_from):
                continue
            strength = broke_out(tape, i, spec.entry)
            if np.isfinite(strength):
                found.append((float(strength), tape.base, i))
        pending_entry = found
        mark = _mark(cash, positions, by_base, stamp, "close")
        peak = max(peak, mark)
        if peak > 0.0:
            worst = max(worst, 1.0 - mark / peak)
        year_end[_year(stamp)] = mark

    if dates.size:
        last = int(dates[-1])
        for base, pos in list(positions.items()):
            tape = by_base[base]
            i = tape.idx.get(last)
            level = float(tape.close[i]) if i is not None else pos.peak
            sell(base, level, last)
        if last >= start_ms:
            year_end[_year(last)] = cash
    end = cash if dates.size else START
    # The last mark already stored the pre-flatten equity. Replace it.
    if dates.size and int(dates[-1]) >= start_ms:
        end = cash
        year_end[_year(int(dates[-1]))] = cash
        if peak > 0.0:
            worst = max(worst, 1.0 - cash / peak)
    year_pnl: dict[int, float] = {}
    prev = START
    for year in sorted(year_end):
        year_pnl[year] = year_end[year] - prev
        prev = year_end[year]
    return Row(spec.name, spec.family, end, end - START, worst, trades, year_pnl, year_trades)


def _regime_ok(spec: Spec, stamp: int, mcap_up: set[int], mcap_down: set[int]) -> bool:
    if spec.regime == "":
        return True
    if spec.regime == "mcap+":
        return stamp in mcap_up
    if spec.regime == "mcap-":
        return stamp in mcap_down
    raise KeyError(spec.regime)


def _news_ok(
    spec: Spec, base: str, stamp: int, news_pos: set[tuple[str, int]], news_from: int
) -> bool:
    if spec.news == "":
        return True
    if stamp < news_from:
        return False
    return (base, stamp) in news_pos


def clears(row: Row) -> bool:
    if row.family == "alphai" or row.trades < MIN_TRADES or row.end <= START:
        return False
    return all(
        row.year_pnl.get(year, 0.0) > 0.0 and row.year_trades.get(year, 0) >= MIN_YEAR_TRADES
        for year in YEARS
    )


def _year(stamp: int) -> int:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(stamp / 1000, UTC).year

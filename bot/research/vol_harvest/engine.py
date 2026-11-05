"""Harvest the swing of a volatile alt, two ways, with every rule set before the run.

Ladder: open a rung on a volatile name, buy another rung each time the
close sits a step under the lowest filled rung, sell a rung once the
close sits a step above its cost. Four rungs, three names, the most
liquid qualifying name first. A gate closes the whole ladder: none,
the name's own 50-day mean, the benchmark's 50-day mean, or a rising
7-day market cap from CoinMarketCap. Every ladder is sold after 90
days so one dead name cannot hold a slot for the whole tape.

Follow-day: when the benchmark closes a big day up, buy the three
names with the highest 60-day beta at the next open and sell at the
open one, three or seven sessions later.

Every fill is the next open. Nothing scans the high or low. Taker
0.25% each side plus impact. Pegs and the benchmark stay out.

A book clears with at least 30 round trips, an end above €20,000 and
a plus with at least eight round trips in each of 2024, 2025 and
2026. Nothing is armed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from bot.research.capital_flow.engine import is_peg
from bot.research.clip_exit_lab.engine import WET, extra_slip
from bot.research.week_book.engine import DAY_MS, Coin

START = 20_000.0
FLOOR = 10_000.0
LARGE = 1_000_000.0
SLOTS = 3
RUNGS = 4
ADV_CAP = 0.02
VOL = 0.20
SMA = 50
BETA_DAYS = 60
LADDER_AGE = 90
BENCHMARK = "BTC"
TAKER = 0.0025
STEPS = (0.05, 0.10, 0.15)
GATES = (
    ("geen", "zonder poort"),
    ("eigen", "eigen 50-daags"),
    ("bench", "benchmark 50-daags"),
    ("mcap", "marktwaarde omhoog"),
)
TRIGGERS = (0.03, 0.05)
HOLDS = (1, 3, 7)
UNIVERSES = (("liquide_vol", "liquide en volatiel"), ("obscure_vol", "obscure en volatiel"))
FOLLOW_UNIVERSES = (("liquide", "liquide"), ("obscure", "obscure"))
YEARS = (2024, 2025, 2026)
MIN_TRADES = 30
MIN_YEAR_TRADES = 8


@dataclass(frozen=True)
class Spec:
    name: str
    family: str
    universe: str
    step: float = 0.0
    gate: str = ""
    trigger: float = 0.0
    hold: int = 0

    def key(self) -> tuple:
        return (self.family, self.universe, self.step, self.gate, self.trigger, self.hold)


def grid() -> list[Spec]:
    specs: list[Spec] = []
    for key, label in UNIVERSES:
        for step in STEPS:
            for gate, gate_label in GATES:
                specs.append(
                    Spec(
                        f"{label}, ladder {_pct(step)}, {gate_label}",
                        "ladder",
                        key,
                        step=step,
                        gate=gate,
                    )
                )
    for key, label in FOLLOW_UNIVERSES:
        for trigger in TRIGGERS:
            for hold in HOLDS:
                specs.append(
                    Spec(
                        f"{label}, volgdag na {_pct(trigger)}, {hold} sessies",
                        "volgdag",
                        key,
                        trigger=trigger,
                        hold=hold,
                    )
                )
    return specs


def _pct(value: float) -> str:
    return f"{round(100 * value)}%"


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
    width: np.ndarray = field(default_factory=lambda: np.asarray([]))
    sma: np.ndarray = field(default_factory=lambda: np.asarray([]))
    ret: dict[int, float] = field(default_factory=dict)


def tape_of(coin: Coin) -> Tape:
    tape = Tape(
        coin.base, coin.ts, coin.open, coin.high, coin.low, coin.close, coin.quote, coin.idx
    )
    tape.width = _width(tape)
    tape.sma = _sma(tape)
    tape.ret = _returns(tape)
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


def _sma(tape: Tape) -> np.ndarray:
    n = len(tape.ts)
    out = np.full(n, np.nan)
    if n < SMA:
        return out
    span = tape.ts[SMA - 1 :] - tape.ts[: n - SMA + 1] == (SMA - 1) * DAY_MS
    mean = np.lib.stride_tricks.sliding_window_view(tape.close, SMA).mean(axis=1)
    out[SMA - 1 :] = np.where(span, mean, np.nan)
    return out


def _returns(tape: Tape) -> dict[int, float]:
    out: dict[int, float] = {}
    for i in range(1, len(tape.ts)):
        if int(tape.ts[i]) - int(tape.ts[i - 1]) == DAY_MS and tape.close[i - 1] > 0:
            out[int(tape.ts[i])] = float(tape.close[i] / tape.close[i - 1] - 1.0)
    return out


def beta(tape: Tape, bench: Tape, stamp: int) -> float:
    """Slope of the name's daily return on the benchmark's, over the last 60 sessions."""
    xs: list[float] = []
    ys: list[float] = []
    for age in range(BETA_DAYS):
        day = stamp - age * DAY_MS
        x = bench.ret.get(day)
        y = tape.ret.get(day)
        if x is None or y is None:
            return np.nan
        xs.append(x)
        ys.append(y)
    x_arr = np.asarray(xs)
    y_arr = np.asarray(ys)
    var = float(np.var(x_arr))
    if var <= 0.0:
        return np.nan
    return float(np.cov(x_arr, y_arr, bias=True)[0, 1] / var)


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


def above_sma(tape: Tape, stamp: int) -> bool:
    i = tape.idx.get(stamp)
    if i is None:
        return False
    level = float(tape.sma[i])
    return np.isfinite(level) and float(tape.close[i]) > level


def gate_open(spec: Spec, tape: Tape, bench: Tape | None, stamp: int, mcap_up: set[int]) -> bool:
    if spec.gate == "geen":
        return True
    if spec.gate == "eigen":
        return above_sma(tape, stamp)
    if spec.gate == "bench":
        return bench is not None and above_sma(bench, stamp)
    if spec.gate == "mcap":
        return stamp in mcap_up
    raise KeyError(spec.gate)


@dataclass
class Rung:
    qty: float
    cost: float


@dataclass
class Ladder:
    base: str
    adv: float
    opened_di: int
    rungs: list[Rung] = field(default_factory=list)
    buy_next: bool = False
    sell_next: list[int] = field(default_factory=list)
    close_next: bool = False


@dataclass
class Follow:
    base: str
    qty: float
    adv: float
    sell_di: int
    cost: float


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


def _buy(cash: float, notional: float, open_: float, adv: float) -> tuple[float, float, float]:
    slip = extra_slip(notional, adv, WET)
    px = open_ * (1.0 + slip) * (1.0 + TAKER)
    return cash - notional, notional / px, px


def _sell(cash: float, qty: float, level: float, adv: float) -> float:
    gross = qty * level
    slip = extra_slip(gross, adv, WET)
    return cash + gross * (1.0 - slip) * (1.0 - TAKER)


def _year(stamp: int) -> int:
    return datetime.fromtimestamp(stamp / 1000, UTC).year


class Book:
    def __init__(self) -> None:
        self.cash = START
        self.peak = START
        self.worst = 0.0
        self.trades = 0
        self.year_trades = {year: 0 for year in YEARS}
        self.year_end: dict[int, float] = {}

    def sold(self, stamp: int) -> None:
        self.trades += 1
        year = _year(stamp)
        if year in self.year_trades:
            self.year_trades[year] += 1

    def mark(self, equity: float, stamp: int) -> None:
        self.peak = max(self.peak, equity)
        if self.peak > 0.0:
            self.worst = max(self.worst, 1.0 - equity / self.peak)
        self.year_end[_year(stamp)] = equity

    def row(self, spec: Spec) -> Row:
        year_pnl: dict[int, float] = {}
        prev = START
        for year in sorted(self.year_end):
            year_pnl[year] = self.year_end[year] - prev
            prev = self.year_end[year]
        return Row(
            spec.name,
            spec.family,
            self.cash,
            self.cash - START,
            self.worst,
            self.trades,
            year_pnl,
            self.year_trades,
        )


def _price(tape: Tape, stamp: int, field_name: str, fallback: float) -> float:
    i = tape.idx.get(stamp)
    if i is None:
        return fallback
    px = float(getattr(tape, field_name)[i])
    return px if px > 0.0 else fallback


def run_ladder(
    tapes: list[Tape],
    spec: Spec,
    dates: np.ndarray,
    *,
    start_ms: int,
    bench: Tape | None,
    mcap_up: set[int],
) -> Row:
    by_base = {tape.base: tape for tape in tapes}
    book = Book()
    ladders: dict[str, Ladder] = {}
    queued: list[str] = []

    def rung_notional(equity: float, adv: float, room: float) -> float:
        return min(equity / (SLOTS * RUNGS), ADV_CAP * adv, room)

    for di, stamp in enumerate(int(day) for day in dates):
        if stamp < start_ms:
            continue
        # Fills at today's open: sells first, then buys, then new ladders.
        for base, ladder in list(ladders.items()):
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is None:
                continue
            open_ = float(tape.open[i])
            if open_ <= 0.0:
                continue
            if ladder.close_next:
                for rung in ladder.rungs:
                    book.cash = _sell(book.cash, rung.qty, open_, ladder.adv)
                    book.sold(stamp)
                ladders.pop(base)
                continue
            for index in sorted(ladder.sell_next, reverse=True):
                rung = ladder.rungs.pop(index)
                book.cash = _sell(book.cash, rung.qty, open_, ladder.adv)
                book.sold(stamp)
            ladder.sell_next = []
            if not ladder.rungs and not ladder.buy_next:
                ladders.pop(base)
                continue
        equity = book.cash + sum(
            rung.qty * _price(by_base[base], stamp, "open", rung.cost)
            for base, ladder in ladders.items()
            for rung in ladder.rungs
        )
        room = book.cash
        for base, ladder in ladders.items():
            if not ladder.buy_next:
                continue
            ladder.buy_next = False
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is None:
                continue
            open_ = float(tape.open[i])
            notional = rung_notional(equity, ladder.adv, room)
            if open_ <= 0.0 or notional < 1.0:
                continue
            room -= notional
            book.cash, qty, px = _buy(book.cash, notional, open_, ladder.adv)
            ladder.rungs.append(Rung(qty, px))
        for base in queued:
            if len(ladders) >= SLOTS or base in ladders:
                continue
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is None:
                continue
            open_ = float(tape.open[i])
            adv = float(tape.quote[i - 1]) if i > 0 else 0.0
            notional = rung_notional(equity, adv, room)
            if open_ <= 0.0 or notional < 1.0:
                continue
            room -= notional
            book.cash, qty, px = _buy(book.cash, notional, open_, adv)
            ladders[base] = Ladder(base, adv, di, [Rung(qty, px)])
        queued = []
        for base in [base for base, ladder in ladders.items() if not ladder.rungs]:
            ladders.pop(base)
        # Decisions at today's close.
        for base, ladder in ladders.items():
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is None:
                continue
            close = float(tape.close[i])
            if close <= 0.0:
                continue
            if di - ladder.opened_di >= LADDER_AGE or not gate_open(
                spec, tape, bench, stamp, mcap_up
            ):
                ladder.close_next = True
                continue
            ladder.sell_next = [
                index
                for index, rung in enumerate(ladder.rungs)
                if close >= rung.cost * (1.0 + spec.step)
            ]
            lowest = min(rung.cost for rung in ladder.rungs)
            keep = len(ladder.rungs) - len(ladder.sell_next)
            if keep + 1 <= RUNGS and close <= lowest * (1.0 - spec.step) and _tradable(tape, i):
                ladder.buy_next = True
                ladder.adv = float(tape.quote[i])
        free = SLOTS - len(ladders)
        if free > 0:
            found: list[tuple[float, str]] = []
            for tape in tapes:
                if tape.base in ladders:
                    continue
                i = tape.idx.get(stamp)
                if i is None or not _tradable(tape, i) or not _universe(spec, tape, i):
                    continue
                if not gate_open(spec, tape, bench, stamp, mcap_up):
                    continue
                found.append((float(tape.quote[i]), tape.base))
            found.sort(reverse=True)
            queued = [base for _quote, base in found[:free]]
        equity = book.cash + sum(
            rung.qty * _price(by_base[base], stamp, "close", rung.cost)
            for base, ladder in ladders.items()
            for rung in ladder.rungs
        )
        book.mark(equity, stamp)

    _flatten_ladders(book, ladders, by_base, dates, start_ms)
    return book.row(spec)


def _flatten_ladders(
    book: Book,
    ladders: dict[str, Ladder],
    by_base: dict[str, Tape],
    dates: np.ndarray,
    start_ms: int,
) -> None:
    if not dates.size or int(dates[-1]) < start_ms:
        return
    last = int(dates[-1])
    for base, ladder in list(ladders.items()):
        tape = by_base[base]
        for rung in ladder.rungs:
            level = _price(tape, last, "close", rung.cost)
            book.cash = _sell(book.cash, rung.qty, level, ladder.adv)
            book.sold(last)
    ladders.clear()
    book.mark(book.cash, last)


def run_follow(
    tapes: list[Tape],
    spec: Spec,
    dates: np.ndarray,
    *,
    start_ms: int,
    bench: Tape,
) -> Row:
    by_base = {tape.base: tape for tape in tapes}
    book = Book()
    held: dict[str, Follow] = {}
    queued: list[tuple[str, int]] = []

    for di, stamp in enumerate(int(day) for day in dates):
        if stamp < start_ms:
            continue
        for base, pos in list(held.items()):
            if di < pos.sell_di:
                continue
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is None or float(tape.open[i]) <= 0.0:
                continue
            book.cash = _sell(book.cash, pos.qty, float(tape.open[i]), pos.adv)
            book.sold(stamp)
            held.pop(base)
        equity = book.cash + sum(
            pos.qty * _price(by_base[base], stamp, "open", pos.cost) for base, pos in held.items()
        )
        room = book.cash
        for base, signal_i in queued:
            if len(held) >= SLOTS or base in held:
                continue
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is None or int(tape.ts[i]) - int(tape.ts[signal_i]) != DAY_MS:
                continue
            open_ = float(tape.open[i])
            adv = float(tape.quote[signal_i])
            notional = min(equity / SLOTS, ADV_CAP * adv, room)
            if open_ <= 0.0 or notional < 1.0:
                continue
            room -= notional
            book.cash, qty, px = _buy(book.cash, notional, open_, adv)
            held[base] = Follow(base, qty, adv, di + spec.hold, px)
        queued = []
        if not held:
            move = bench.ret.get(stamp)
            if move is not None and move >= spec.trigger:
                found: list[tuple[float, str, int]] = []
                for tape in tapes:
                    i = tape.idx.get(stamp)
                    if i is None or not _tradable(tape, i) or not _universe(spec, tape, i):
                        continue
                    slope = beta(tape, bench, stamp)
                    if np.isfinite(slope):
                        found.append((slope, tape.base, i))
                found.sort(reverse=True)
                queued = [(base, i) for _slope, base, i in found[:SLOTS]]
        equity = book.cash + sum(
            pos.qty * _price(by_base[base], stamp, "close", pos.cost) for base, pos in held.items()
        )
        book.mark(equity, stamp)

    if dates.size and int(dates[-1]) >= start_ms:
        last = int(dates[-1])
        for base, pos in list(held.items()):
            level = _price(by_base[base], last, "close", pos.cost)
            book.cash = _sell(book.cash, pos.qty, level, pos.adv)
            book.sold(last)
        held.clear()
        book.mark(book.cash, last)
    return book.row(spec)


def clears(row: Row) -> bool:
    if row.trades < MIN_TRADES or row.end <= START:
        return False
    return all(
        row.year_pnl.get(year, 0.0) > 0.0 and row.year_trades.get(year, 0) >= MIN_YEAR_TRADES
        for year in YEARS
    )

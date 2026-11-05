"""Buy a random coin with the desk's own clock and exits, and see what remains.

The live pack picks one name a week by residual momentum, holds it on
a 10% trail with a +12%/+6% lock, and sits in cash when the benchmark
is under its 50-day mean. This study keeps that machinery and swaps
the pick for something that cannot know anything: the phase of the
moon, the timestamp, the day of the month, the first letter of the
weekday, or a seeded coin flip. Five hundred seeds give the distribution a
random bot lands in. Whatever the pack earns above that distribution
is selection; whatever a random pick earns is the exit and the gate.

Signal at the close, fill at the next open, taker 0.25% each side plus
impact, one name, at most 2% of that day's quote. Pegs and the
benchmark stay out. Nothing is armed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from bot.research.vol_harvest.engine import (
    ADV_CAP,
    FLOOR,
    LARGE,
    START,
    YEARS,
    Book,
    Row,
    Tape,
    _buy,
    _price,
    _sell,
    _tradable,
    above_sma,
)
from bot.research.week_book.engine import DAY_MS

CLOCK = 7
TRAIL = 0.10
LOCK_ARM = 0.12
LOCK_FLOOR = 0.06
SEEDS = 500
MIN_TRADES = 30
MIN_YEAR_TRADES = 8
SYNODIC_MS = 29.530588853 * DAY_MS
NEW_MOON_MS = int(datetime(2000, 1, 6, 18, 14, tzinfo=UTC).timestamp() * 1000)
WEEKDAY_LETTER = ("M", "D", "W", "D", "V", "Z", "Z")

SELECTORS = (
    ("maan", "stand van de maan"),
    ("maan_wassend", "alleen bij wassende maan"),
    ("tijd", "tijdstempel modulo"),
    ("hash", "hash van de tijdstempel"),
    ("dag", "dag van de maand"),
    ("letter", "letter van de weekdag"),
)
UNIVERSES = (("alles", "alles"), ("liquide", "liquide"))
EXITS = (("klok", "weekklok"), ("spoor", "weekklok, spoor 10%, lock 12/6"))
GATES = (("geen", "zonder poort"), ("bench", "benchmark 50-daags"))


@dataclass(frozen=True)
class Spec:
    name: str
    selector: str
    universe: str
    exit: str
    gate: str
    seed: int = -1

    @property
    def family(self) -> str:
        return "toeval" if self.selector == "toeval" else "vast"

    def key(self) -> tuple:
        return (self.selector, self.universe, self.exit, self.gate, self.seed)


def grid() -> list[Spec]:
    specs: list[Spec] = []
    for selector, label in SELECTORS:
        for universe, u_label in UNIVERSES:
            for exit_key, e_label in EXITS:
                for gate, g_label in GATES:
                    specs.append(
                        Spec(
                            f"{label}, {u_label}, {e_label}, {g_label}",
                            selector,
                            universe,
                            exit_key,
                            gate,
                        )
                    )
    return specs


def seed_grid() -> list[Spec]:
    specs: list[Spec] = []
    for universe, u_label in UNIVERSES:
        for exit_key, e_label in EXITS:
            for gate, g_label in GATES:
                for seed in range(SEEDS):
                    specs.append(
                        Spec(
                            f"toeval, {u_label}, {e_label}, {g_label}",
                            "toeval",
                            universe,
                            exit_key,
                            gate,
                            seed=seed,
                        )
                    )
    return specs


def moon_phase(stamp: int) -> float:
    """0 is new moon, 0.5 is full moon."""
    return float(((stamp - NEW_MOON_MS) / SYNODIC_MS) % 1.0)


def pick(
    spec: Spec, stamp: int, candidates: list[str], rng: np.random.Generator | None
) -> str | None:
    """One name out of the sorted candidates, or None for cash."""
    n = len(candidates)
    if n == 0:
        return None
    if spec.selector == "maan":
        return candidates[min(n - 1, int(moon_phase(stamp) * n))]
    if spec.selector == "maan_wassend":
        phase = moon_phase(stamp)
        if phase >= 0.5:
            return None
        return candidates[min(n - 1, int(phase * 2.0 * n))]
    if spec.selector == "tijd":
        return candidates[(stamp // DAY_MS) % n]
    if spec.selector == "hash":
        digest = hashlib.sha256(str(stamp).encode()).hexdigest()
        return candidates[int(digest, 16) % n]
    if spec.selector == "dag":
        day = datetime.fromtimestamp(stamp / 1000, UTC).day
        return candidates[(day - 1) % n]
    if spec.selector == "letter":
        letter = WEEKDAY_LETTER[datetime.fromtimestamp(stamp / 1000, UTC).weekday()]
        later = [base for base in candidates if base[:1].upper() >= letter]
        return later[0] if later else candidates[0]
    if spec.selector == "toeval":
        if rng is None:
            raise ValueError("toeval needs a generator")
        return candidates[int(rng.integers(n))]
    raise KeyError(spec.selector)


def in_universe(spec: Spec, tape: Tape, i: int) -> bool:
    if spec.universe == "alles":
        return True
    if spec.universe == "liquide":
        return float(tape.quote[i]) >= LARGE
    raise KeyError(spec.universe)


def candidates_by_day(tapes: list[Tape], dates: np.ndarray, universe: str) -> dict[int, list[str]]:
    probe = Spec("", "tijd", universe, "klok", "geen")
    out: dict[int, list[str]] = {}
    for stamp in (int(day) for day in dates):
        names = []
        for tape in tapes:
            i = tape.idx.get(stamp)
            if i is None or not _tradable(tape, i) or not in_universe(probe, tape, i):
                continue
            names.append(tape.base)
        names.sort()
        out[stamp] = names
    return out


@dataclass
class Held:
    base: str
    qty: float
    adv: float
    cost: float
    peak: float


def run(
    tapes: list[Tape],
    spec: Spec,
    dates: np.ndarray,
    *,
    start_ms: int,
    bench: Tape | None,
    candidates: dict[int, list[str]],
) -> Row:
    by_base = {tape.base: tape for tape in tapes}
    book = Book()
    rng = np.random.default_rng(spec.seed) if spec.seed >= 0 else None
    held: Held | None = None
    pending_sell = False
    pending_buy: tuple[str, int] | None = None
    clock_di: int | None = None

    for di, stamp in enumerate(int(day) for day in dates):
        if stamp < start_ms:
            continue
        if clock_di is None:
            clock_di = di
        if held is not None and pending_sell:
            tape = by_base[held.base]
            i = tape.idx.get(stamp)
            if i is not None and float(tape.open[i]) > 0.0:
                book.cash = _sell(book.cash, held.qty, float(tape.open[i]), held.adv)
                book.sold(stamp)
                held = None
                pending_sell = False
        if held is None and pending_buy is not None:
            base, signal_i = pending_buy
            tape = by_base[base]
            i = tape.idx.get(stamp)
            if i is not None and int(tape.ts[i]) - int(tape.ts[signal_i]) == DAY_MS:
                open_ = float(tape.open[i])
                adv = float(tape.quote[signal_i])
                notional = min(book.cash, ADV_CAP * adv)
                if open_ > 0.0 and notional >= 1.0:
                    book.cash, qty, px = _buy(book.cash, notional, open_, adv)
                    held = Held(base, qty, adv, px, px)
        pending_buy = None
        # Decisions at the close.
        gate_ok = spec.gate == "geen" or (bench is not None and above_sma(bench, stamp))
        on_clock = (di - clock_di) % CLOCK == 0
        if held is not None:
            tape = by_base[held.base]
            i = tape.idx.get(stamp)
            if i is not None:
                close = float(tape.close[i])
                high = float(tape.high[i])
                if spec.exit == "spoor" and close > 0.0:
                    armed = held.peak >= held.cost * (1.0 + LOCK_ARM)
                    if close <= held.peak * (1.0 - TRAIL):
                        pending_sell = True
                    if armed and close < held.cost * (1.0 + LOCK_FLOOR):
                        pending_sell = True
                    held.peak = max(held.peak, high)
            if not gate_ok:
                pending_sell = True
        if on_clock and gate_ok:
            names = candidates.get(stamp, [])
            choice = pick(spec, stamp, names, rng)
            if choice is None:
                if held is not None:
                    pending_sell = True
            elif held is None or held.base != choice or pending_sell:
                if held is not None:
                    pending_sell = True
                tape = by_base[choice]
                i = tape.idx.get(stamp)
                if i is not None:
                    pending_buy = (choice, i)
        equity = book.cash
        if held is not None:
            equity += held.qty * _price(by_base[held.base], stamp, "close", held.cost)
        book.mark(equity, stamp)

    if held is not None and dates.size:
        last = int(dates[-1])
        level = _price(by_base[held.base], last, "close", held.cost)
        book.cash = _sell(book.cash, held.qty, level, held.adv)
        book.sold(last)
        book.mark(book.cash, last)
    return book.row(spec)


def clears(row: Row) -> bool:
    if row.trades < MIN_TRADES or row.end <= START:
        return False
    return all(
        row.year_pnl.get(year, 0.0) > 0.0 and row.year_trades.get(year, 0) >= MIN_YEAR_TRADES
        for year in YEARS
    )


__all__ = [
    "FLOOR",
    "Row",
    "Spec",
    "candidates_by_day",
    "clears",
    "grid",
    "moon_phase",
    "pick",
    "run",
    "seed_grid",
]

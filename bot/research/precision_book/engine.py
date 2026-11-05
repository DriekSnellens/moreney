"""Trade the precision ladder and keep every cell.

The names are the ones the ladder already points at. Nothing here adds a
score, a slice or a sleeve. Two ways to size the book and four exits were
fixed before this tape ran.

Sizing:

* vol — the whole book, split equally across the names chosen that week
* kap — the same split, except a name never takes more than 2% of its own
  quote that day and the rest stays cash

Exits, from the next open:

* week — the open seven days later
* stop15 — a daily bar that trades 15% under the entry, otherwise the week
  open. A gap through the stop fills at the open.
* doel10 — a daily bar that trades 10% above the entry, otherwise the week
  open. A gap through the target fills at the open.
* doel50 — the same at 50%

On a daily bar the path inside the bar is unknown. The stop and the target
are separate books, so a bar never has to choose between them. The exit day
itself is sold at the open and its high and low are not used.

Costs are the Bitvavo taker, 0.25% a side, plus impact
``0.02 * sqrt(notional / quote)`` capped at 3% a side. The quote is the
signal day's turnover. The same path without taker and without impact is
kept beside it.

The book starts at €20,000 and compounds. A week with fewer than 10 names
stays in cash. A logistic score that has no fit yet stays in cash. A book
clears the bar only when it trades at least 30 weeks, puts at least a
quarter of the capital to work on average, ends above €20,000, ends above
the book that holds every eligible name in that sleeve with the same size
and the same exit, and each of 2024, 2025 and 2026 has at least eight
weeks and a positive euro result. Nothing is armed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from bot.research.capital_flow.engine import wet_net
from bot.research.cross_section.engine import (
    FLOOR_BROAD,
    START,
    Obs,
    weeks_of,
)
from bot.research.precision_ladder.engine import (
    SCORES,
    SLICES,
    _logit,
    _pick,
    _simple_scores,
)
from bot.research.rise_engine.engine import MIN_POOL, WARMUP, feature_row, sleeve_of
from bot.research.week_book.engine import DAY_MS, Coin, master_dates

EXITS = ("week", "stop15", "doel10", "doel50")
DEPLOYS = ("vol", "kap")
TARGETS = {"doel10": 0.10, "doel50": 0.50}
STOP = 0.15
CAP_FRAC = 0.02
MIN_FRAC = 0.25
MIN_WEEKS = 30
MIN_YEAR = 8
YEARS = (2024, 2025, 2026)
# Published armed book in bot/research/tactic_lab/RESULTS.md.
# Window 2024-03-16 through 2026-10-03, €20,000, one name.
LIVE_END = 488_677.0
LIVE_PNL = 468_677.0
LIVE_DD = 0.312
LIVE_TRADES = 96
LIVE_YEAR = {2024: 132_986.0, 2025: 18_812.0, 2026: 316_879.0}


@dataclass(frozen=True)
class Pick:
    entry: float
    adv: float
    exit_ms: int
    fills: dict[str, float]


@dataclass
class Book:
    score: str
    sleeve: str
    slice: str
    deploy: str
    exit: str
    equity: float
    gross_equity: float
    dd: float
    weeks: int
    traded: int
    mean_frac: float
    year_pnl: dict[int, float] = field(default_factory=dict)
    year_traded: dict[int, int] = field(default_factory=dict)
    clears: bool = False

    @property
    def pnl(self) -> float:
        return self.equity - START


def fill_price(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    entry: float,
    kind: str,
) -> float:
    """Raw sell reference. The last bar is the scheduled open and is not scanned."""
    if len(opens) == 0:
        return entry
    if kind == "week":
        return float(opens[-1])
    tol = abs(entry) * 1e-9
    if kind == "stop15":
        level = entry * (1.0 - STOP)
        for opened, low in zip(opens[:-1], lows[:-1], strict=True):
            if low > level + tol:
                continue
            return float(opened) if opened <= level else level
        return float(opens[-1])
    level = entry * (1.0 + TARGETS[kind])
    for opened, high in zip(opens[:-1], highs[:-1], strict=True):
        if high + tol < level:
            continue
        return float(opened) if opened >= level else level
    return float(opens[-1])


def _year(stamp: int) -> int:
    return datetime.fromtimestamp(stamp / 1000, UTC).year


def _fills(coin: Coin, row: Obs) -> tuple[float, dict[str, float]]:
    entry_ms = int(row.exit_ms) - 7 * DAY_MS
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    stamp = entry_ms
    while stamp <= int(row.exit_ms):
        i = coin.idx.get(stamp)
        if i is not None:
            opens.append(float(coin.open[i]))
            highs.append(float(coin.high[i]))
            lows.append(float(coin.low[i]))
        stamp += DAY_MS
    if not opens or opens[0] <= 0.0:
        return row.entry, {name: row.exit for name in EXITS}
    entry = opens[0]
    fills = {name: fill_price(opens, highs, lows, entry, name) for name in EXITS}
    return entry, fills


def _pick_of(coin: Coin, row: Obs) -> Pick:
    entry, fills = _fills(coin, row)
    return Pick(entry, row.adv, int(row.exit_ms), fills)


def _slots(picks: Sequence[Pick], equity: float, deploy: str) -> list[float]:
    full = equity / len(picks)
    if deploy == "vol":
        return [full] * len(picks)
    return [min(full, CAP_FRAC * pick.adv) for pick in picks]


def _ret(pick: Pick, slot: float, kind: str, *, cost: bool) -> float:
    fill = pick.fills[kind]
    if cost:
        return wet_net(pick.entry, fill, pick.adv, notional=slot)
    if pick.entry <= 0.0:
        return 0.0
    return fill / pick.entry - 1.0


@dataclass
class _Path:
    equity: float
    dd: float
    traded: int
    mean_frac: float
    year_pnl: dict[int, float]
    year_traded: dict[int, int]


def _simulate(
    weeks: Sequence[Sequence[Pick]],
    deploy: str,
    kind: str,
    *,
    cost: bool,
) -> _Path:
    equity = START
    peak = START
    worst = 0.0
    traded = 0
    frac_sum = 0.0
    year_pnl = {year: 0.0 for year in YEARS}
    year_traded = {year: 0 for year in YEARS}
    for picks in weeks:
        if not picks or equity <= 0.0:
            continue
        slots = _slots(picks, equity, deploy)
        deployed = float(sum(slots))
        if deployed <= 0.0:
            continue
        pnl = 0.0
        for pick, slot in zip(picks, slots, strict=True):
            if slot <= 0.0:
                continue
            pnl += slot * _ret(pick, slot, kind, cost=cost)
        before = equity
        equity += pnl
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
        year = _year(picks[0].exit_ms)
        year_pnl[year] = year_pnl.get(year, 0.0) + (equity - before)
        year_traded[year] = year_traded.get(year, 0) + 1
        traded += 1
        frac_sum += deployed / before
    mean_frac = frac_sum / traded if traded else 0.0
    return _Path(equity, worst, traded, mean_frac, year_pnl, year_traded)


def compound(
    score: str,
    sleeve: str,
    slice_name: str,
    weeks: Sequence[Sequence[Pick]],
    deploy: str,
    kind: str,
) -> Book:
    net = _simulate(weeks, deploy, kind, cost=True)
    gross = _simulate(weeks, deploy, kind, cost=False)
    return Book(
        score=score,
        sleeve=sleeve,
        slice=slice_name,
        deploy=deploy,
        exit=kind,
        equity=net.equity,
        gross_equity=gross.equity,
        dd=net.dd,
        weeks=len(weeks),
        traded=net.traded,
        mean_frac=net.mean_frac,
        year_pnl=net.year_pnl,
        year_traded=net.year_traded,
    )


def clears(book: Book, hold_equity: float) -> bool:
    """True when the book beats cash, beats holding the sleeve, and every year pays."""
    if book.score == "hold":
        return False
    if book.traded < MIN_WEEKS or book.mean_frac < MIN_FRAC:
        return False
    if book.equity <= START or book.equity <= hold_equity:
        return False
    return all(
        book.year_pnl.get(year, 0.0) > 0.0 and book.year_traded.get(year, 0) >= MIN_YEAR
        for year in YEARS
    )


def _chosen(rows: Sequence[Obs], picks: Sequence[Pick], scores: np.ndarray, frac: float | None):
    chosen = {id(row) for row in _pick(rows, scores, frac)}
    return [pick for row, pick in zip(rows, picks, strict=True) if id(row) in chosen]


def _series(
    weeks: Sequence[Sequence[Obs]], coins: dict[str, Coin]
) -> dict[tuple[str, str], list[list[Pick]]]:
    keys = [(score, name) for score in SCORES for name, _frac in SLICES]
    keys.append(("hold", "alles"))
    out: dict[tuple[str, str], list[list[Pick]]] = {key: [] for key in keys}
    for index, week in enumerate(weeks):
        if index < WARMUP or len(week) < MIN_POOL:
            continue
        picks = [_pick_of(coins[row.base], row) for row in week]
        out[("hold", "alles")].append(picks)
        simple = _simple_scores(week)
        for score in ("near", "fresh", "reversal", "size"):
            for name, frac in SLICES:
                out[(score, name)].append(_chosen(week, picks, simple[score], frac))
        for score, level in (("logit10", 0.10), ("logit20", 0.20)):
            fitted = _logit(weeks[:index], week, level)
            for name, frac in SLICES:
                if fitted is None:
                    out[(score, name)].append([])
                else:
                    out[(score, name)].append(_chosen(week, picks, fitted, frac))
    return out


def apply_bar(books: list[Book]) -> None:
    hold = {
        (book.sleeve, book.deploy, book.exit): book.equity
        for book in books
        if book.score == "hold"
    }
    for book in books:
        book.clears = clears(book, hold.get((book.sleeve, book.deploy, book.exit), math.inf))


def run_tape(coins: Sequence[Coin], dates: np.ndarray) -> list[Book]:
    raw = weeks_of(coins, dates, FLOOR_BROAD)
    by_base = {coin.base: coin for coin in coins}
    books: list[Book] = []
    for sleeve in ("all", "large", "obscure"):
        weeks = []
        for week in raw:
            rows = [
                row for row in week if feature_row(row) is not None and sleeve_of(row.adv, sleeve)
            ]
            weeks.append(rows)
        made = _series(weeks, by_base)
        for (score, slice_name), series in made.items():
            for deploy in DEPLOYS:
                for kind in EXITS:
                    books.append(compound(score, sleeve, slice_name, series, deploy, kind))
    apply_bar(books)
    return books


def tape_calendar(coins: Sequence[Coin]) -> np.ndarray:
    return master_dates(coins)

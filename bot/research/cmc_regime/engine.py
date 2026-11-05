"""Trade Bitvavo with a CoinMarketCap regime that was fixed first.

CoinMarketCap's daily stamp matches the Bitvavo close of the previous
session: the print at midnight is the close that just happened. A signal
on session T may see that print and nothing later. The buy is the next
open. The sell is the open seven days later.

Global series, known at that close:

* total market cap in euro, against the print seven days earlier
* BTC dominance, as a change in percentage points over those seven days
* world volume against the mean of the seven prints before this one

Rules, one per cell, fixed before the tape:

* altijd — every week
* mcap+ / mcap- — market cap up, or down
* dom+ / dom- — dominance up, or down
* seizoen — market cap up and dominance down
* btc_sterk — market cap up and dominance up
* luid — world volume at least 1.5 times the prior seven prints
* stil — world volume at most 0.7 times that mean

The book underneath is also fixed: every liquid name, the highest 7-day
rise that is still positive, the highest quote, or the five highest
positive 7-day rises. Liquid means €1,000,000 of quote and not a peg.
€20,000 compounds. A name pays the Bitvavo taker and impact. Empty weeks
stay cash. Top-five keeps the unused slots in cash.

A filter clears the bar only when it trades at least 30 weeks, ends above
€20,000, ends above the same book with no filter, and each of 2024, 2025
and 2026 has at least eight weeks and a positive euro result.

Coin quotes from this key only go back twelve months. On that window the
scores are CMC's own 7-day change, CMC market cap, and the Bitvavo 7-day
change. One name and the top five. That window cannot clear the
three-year bar. Nothing is armed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from bot.research.capital_flow.engine import is_peg, wet_net
from bot.research.cross_section.engine import START
from bot.research.week_book.engine import DAY_MS, HOLD, WARM, Coin

QUOTE = 1_000_000.0
LOUD = 1.5
QUIET = 0.7
TOP_N = 5
MIN_WEEKS = 30
MIN_YEAR = 8
YEARS = (2024, 2025, 2026)
SELECTIONS = ("alles", "mom7", "omzet", "top5")
REGIMES = ("altijd", "mcap+", "mcap-", "dom+", "dom-", "seizoen", "btc_sterk", "luid", "stil")
SCORE_NAMES = ("cmc7", "cmc_cap", "bv7")
LIVE_END = 488_677.0
LIVE_PNL = 468_677.0
LIVE_DD = 0.312
LIVE_TRADES = 96
LIVE_YEAR = {2024: 132_986.0, 2025: 18_812.0, 2026: 316_879.0}


@dataclass(frozen=True)
class Point:
    ts: int
    mcap: float
    volume: float
    btc_dom: float


@dataclass(frozen=True)
class View:
    mcap: float
    dom: float
    vol_x: float


@dataclass(frozen=True)
class Quote:
    ts: int
    mcap: float
    pct7: float


@dataclass(frozen=True)
class Order:
    entry: float
    exit: float
    adv: float
    exit_ms: int


@dataclass
class Book:
    selection: str
    regime: str
    equity: float
    gross_equity: float
    dd: float
    traded: int
    year_pnl: dict[int, float] = field(default_factory=dict)
    year_traded: dict[int, int] = field(default_factory=dict)
    clears: bool = False

    @property
    def pnl(self) -> float:
        return self.equity - START


class Market:
    def __init__(self, points: Sequence[Point]) -> None:
        self.by_ts = {int(point.ts): point for point in points}

    def view(self, signal_ms: int) -> View | None:
        """The close of ``signal_ms``. The next session's print stays hidden."""
        asof = int(signal_ms) + DAY_MS
        now = self.by_ts.get(asof)
        prev = self.by_ts.get(asof - 7 * DAY_MS)
        if now is None or prev is None or prev.mcap <= 0.0:
            return None
        vols: list[float] = []
        for age in range(1, 8):
            point = self.by_ts.get(asof - age * DAY_MS)
            if point is None:
                return None
            vols.append(point.volume)
        base = float(sum(vols) / len(vols))
        if base <= 0.0:
            return None
        return View(now.mcap / prev.mcap - 1.0, now.btc_dom - prev.btc_dom, now.volume / base)


def allows(regime: str, view: View | None) -> bool:
    if regime == "altijd":
        return True
    if view is None:
        return False
    if regime == "mcap+":
        return view.mcap > 0.0
    if regime == "mcap-":
        return view.mcap < 0.0
    if regime == "dom+":
        return view.dom > 0.0
    if regime == "dom-":
        return view.dom < 0.0
    if regime == "seizoen":
        return view.mcap > 0.0 and view.dom < 0.0
    if regime == "btc_sterk":
        return view.mcap > 0.0 and view.dom > 0.0
    if regime == "luid":
        return view.vol_x >= LOUD
    if regime == "stil":
        return view.vol_x <= QUIET
    raise KeyError(regime)


def _year(stamp: int) -> int:
    return datetime.fromtimestamp(stamp / 1000, UTC).year


def _name_ok(coin: Coin, i: int) -> bool:
    if i < HOLD or int(coin.ts[i]) - int(coin.ts[i - HOLD]) != HOLD * DAY_MS:
        return False
    if float(coin.quote[i]) < QUOTE or float(coin.close[i]) <= 0.0:
        return False
    if float(coin.close[i - HOLD]) <= 0.0:
        return False
    return not is_peg(coin.close[i - (HOLD - 1) : i + 1])


def _ret7(coin: Coin, i: int) -> float:
    return float(coin.close[i]) / float(coin.close[i - HOLD]) - 1.0


@dataclass(frozen=True)
class _Cand:
    base: str
    ret: float
    quote: float
    index: int


def _pool(coins: Sequence[Coin], stamp: int) -> list[_Cand]:
    found: list[_Cand] = []
    for coin in coins:
        i = coin.idx.get(int(stamp))
        if i is None or not _name_ok(coin, i):
            continue
        found.append(_Cand(coin.base, _ret7(coin, i), float(coin.quote[i]), i))
    return found


def _choose(selection: str, pool: Sequence[_Cand]) -> list[_Cand]:
    if selection == "alles":
        return list(pool)
    if selection == "omzet":
        if not pool:
            return []
        return [max(pool, key=lambda cand: (cand.quote, cand.base))]
    rising = [cand for cand in pool if cand.ret > 0.0]
    rising.sort(key=lambda cand: (-cand.ret, cand.base))
    if selection == "mom7":
        return rising[:1]
    if selection == "top5":
        return rising[:TOP_N]
    raise KeyError(selection)


def _slots(selection: str, n_picks: int) -> int:
    if selection == "alles":
        return max(1, n_picks)
    if selection == "top5":
        return TOP_N
    return 1


def _order(coin: Coin, dates: np.ndarray, signal: int, adv: float) -> Order | None:
    exit_at = signal + 1 + HOLD
    if int(dates[exit_at]) - int(dates[signal]) != (HOLD + 1) * DAY_MS:
        return None
    entry_i = coin.idx.get(int(dates[signal + 1]))
    exit_i = coin.idx.get(int(dates[exit_at]))
    if entry_i is None or exit_i is None:
        return None
    entry = float(coin.open[entry_i])
    exit_px = float(coin.open[exit_i])
    if entry <= 0.0 or exit_px <= 0.0:
        return None
    return Order(entry, exit_px, adv, int(dates[exit_at]))


def _weeks(
    selection: str,
    regime: str,
    coins: Sequence[Coin],
    dates: np.ndarray,
    market: Market,
) -> list[tuple[list[Order], int]]:
    by_base = {coin.base: coin for coin in coins}
    weeks: list[tuple[list[Order], int]] = []
    signal = WARM
    while signal + 1 + HOLD < len(dates):
        stamp = int(dates[signal])
        if allows(regime, market.view(stamp)):
            chosen = _choose(selection, _pool(coins, stamp))
            orders: list[Order] = []
            for cand in chosen:
                order = _order(by_base[cand.base], dates, signal, cand.quote)
                if order is not None:
                    orders.append(order)
            if orders:
                weeks.append((orders, _slots(selection, len(orders))))
        signal += HOLD
    return weeks


def compound(selection: str, regime: str, weeks: Sequence[tuple[list[Order], int]]) -> Book:
    equity = START
    gross_equity = START
    peak = START
    worst = 0.0
    traded = 0
    year_pnl = {year: 0.0 for year in YEARS}
    year_traded = {year: 0 for year in YEARS}
    for orders, slots in weeks:
        if not orders or slots <= 0 or equity <= 0.0:
            continue
        slot = equity / slots
        gross_slot = gross_equity / slots
        pnl = 0.0
        gross_pnl = 0.0
        for order in orders:
            pnl += slot * wet_net(order.entry, order.exit, order.adv, notional=slot)
            gross_pnl += gross_slot * (order.exit / order.entry - 1.0)
        before = equity
        equity += pnl
        gross_equity += gross_pnl
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
        year = _year(orders[0].exit_ms)
        year_pnl[year] = year_pnl.get(year, 0.0) + (equity - before)
        year_traded[year] = year_traded.get(year, 0) + 1
        traded += 1
    return Book(
        selection,
        regime,
        equity,
        gross_equity,
        worst,
        traded,
        year_pnl,
        year_traded,
    )


def clears(book: Book, plain_equity: float) -> bool:
    if book.regime == "altijd":
        return False
    if book.traded < MIN_WEEKS or book.equity <= START or book.equity <= plain_equity:
        return False
    return all(
        book.year_pnl.get(year, 0.0) > 0.0 and book.year_traded.get(year, 0) >= MIN_YEAR
        for year in YEARS
    )


def run_regimes(coins: Sequence[Coin], dates: np.ndarray, market: Market) -> list[Book]:
    books: list[Book] = []
    for selection in SELECTIONS:
        for regime in REGIMES:
            weeks = _weeks(selection, regime, coins, dates, market)
            books.append(compound(selection, regime, weeks))
    plain = {(book.selection, book.regime): book.equity for book in books}
    for book in books:
        book.clears = clears(book, plain.get((book.selection, "altijd"), math.inf))
    return books


def _quote_pool(
    coins: Sequence[Coin],
    stamp: int,
    series: Mapping[str, Mapping[int, Quote]],
) -> list[tuple[_Cand, Quote]]:
    asof = int(stamp) + DAY_MS
    found: list[tuple[_Cand, Quote]] = []
    for cand in _pool(coins, stamp):
        row = series.get(cand.base, {}).get(asof)
        if row is None or row.mcap <= 0.0:
            continue
        found.append((cand, row))
    return found


def _score_weeks(
    score: str,
    width: int,
    coins: Sequence[Coin],
    dates: np.ndarray,
    series: Mapping[str, Mapping[int, Quote]],
) -> list[tuple[list[Order], int]]:
    by_base = {coin.base: coin for coin in coins}
    weeks: list[tuple[list[Order], int]] = []
    signal = WARM
    while signal + 1 + HOLD < len(dates):
        stamp = int(dates[signal])
        pool = _quote_pool(coins, stamp, series)
        if score == "cmc7":
            pool = [item for item in pool if math.isfinite(item[1].pct7) and item[1].pct7 > 0.0]
            pool.sort(key=lambda item: (-item[1].pct7, item[0].base))
        elif score == "bv7":
            pool = [item for item in pool if item[0].ret > 0.0]
            pool.sort(key=lambda item: (-item[0].ret, item[0].base))
        elif score == "cmc_cap":
            pool.sort(key=lambda item: (-item[1].mcap, item[0].base))
        else:
            raise KeyError(score)
        orders: list[Order] = []
        for cand, _row in pool[:width]:
            order = _order(by_base[cand.base], dates, signal, cand.quote)
            if order is not None:
                orders.append(order)
        if orders:
            weeks.append((orders, width))
        signal += HOLD
    return weeks


def run_scores(
    coins: Sequence[Coin],
    dates: np.ndarray,
    series: Mapping[str, Mapping[int, Quote]],
) -> list[Book]:
    books: list[Book] = []
    for score in SCORE_NAMES:
        for width in (1, TOP_N):
            weeks = _score_weeks(score, width, coins, dates, series)
            books.append(compound(score, f"top{width}", weeks))
    return books

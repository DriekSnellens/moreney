"""Does a ranking across many names predict the next week?

Bitvavo lists a few hundred names, not thousands. Each week is one
observation: the coins move together, so a coin-week is not an
independent trial. The clock steps seven days. The signal is that
close. The forward return is the open seven days after the next open,
divided by that next open.

A week needs at least ten names with €10,000 of quote, or, in the
second universe, €1,000,000. A name whose last seven closes sit inside
0.5% is a peg and stays out. Scores, all known at the close, higher
meaning the rank expects a larger rise:

* ret1, ret7, ret28 — the close-to-close return over that many days
* volx — today's quote divided by the prior seven days
* near — the close divided by the high of the last twenty days
* size — today's quote

The rank correlation is Spearman between the score and the forward
return, one number per week. The t-stat uses those weekly numbers.
A score is visible when the mean correlation is positive, the t-stat
is at least 2, and each of 2024, 2025 and 2026 is positive on its own.

The top fifth and the bottom fifth are also traded. A fixed €1,000
shows the return after the taker and impact. A €20,000 book compounds,
split equally across the names in the slice. The book clears the bar
when it ends above €20,000, has at least thirty weeks, is positive in
each year, and ends above the book that holds every eligible name.
Twelve rankings are reported. Nothing is armed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from bot.research.capital_flow.engine import is_peg, wet_net
from bot.research.week_book.engine import DAY_MS, Coin, master_dates

HOLD = 7
WARM = 28
HIGH_DAYS = 20
MIN_POOL = 10
QUINTILE = 5
STAKE = 1_000.0
START = 20_000.0
FLOOR_BROAD = 10_000.0
FLOOR_LIQUID = 1_000_000.0
T_BAR = 2.0
MIN_WEEKS = 30
YEARS = (2024, 2025, 2026)
SCORES = ("ret1", "ret7", "ret28", "volx", "near", "size")


@dataclass
class Obs:
    base: str
    gross: float
    entry: float
    exit: float
    adv: float
    exit_ms: int
    ret1: float
    ret7: float
    ret28: float
    volx: float
    near: float
    size: float

    def score(self, name: str) -> float:
        return getattr(self, name)


@dataclass
class Slice:
    feature: str
    side: str
    weeks: int
    pool: float
    names: float
    corr: float
    t: float
    detectable: float
    year_corr: dict[int, float]
    gross: float
    net: float
    equity: float
    dd: float
    year_pnl: dict[int, float] = field(default_factory=dict)
    visible: bool = False
    clears: bool = False


@dataclass
class Study:
    floor: float
    coins: int
    weeks: int
    coin_weeks: int
    universe_equity: float
    rows: list[Slice]


def _year(stamp: int) -> int:
    return datetime.fromtimestamp(stamp / 1000, UTC).year


def rank_average(values: np.ndarray) -> np.ndarray:
    """Average ranks, 1-based, stable under ties."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    ordered = values[order]
    start = 0
    while start < len(values):
        stop = start + 1
        while stop < len(values) and ordered[stop] == ordered[start]:
            stop += 1
        average = (start + 1 + stop) / 2.0
        ranks[order[start:stop]] = average
        start = stop
    return ranks


def spearman(scores: np.ndarray, forwards: np.ndarray) -> float:
    if len(scores) < 3:
        return math.nan
    left = rank_average(scores)
    right = rank_average(forwards)
    left = left - left.mean()
    right = right - right.mean()
    denom = math.sqrt(float((left * left).sum() * (right * right).sum()))
    if denom == 0.0:
        return math.nan
    return float((left * right).sum() / denom)


def t_stat(values: np.ndarray) -> float:
    if len(values) < 3:
        return math.nan
    std = float(values.std(ddof=1))
    if std == 0.0 or not math.isfinite(std):
        return math.nan
    return float(values.mean() / (std / math.sqrt(len(values))))


def quintile_index(bases: Sequence[str], scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Top and bottom fifth. Ties break on the name, lowest name first."""
    order = sorted(range(len(bases)), key=lambda i: (-float(scores[i]), bases[i]))
    width = max(1, len(bases) // QUINTILE)
    top = np.asarray(order[:width], dtype=int)
    bottom = np.asarray(order[-width:], dtype=int)
    return top, bottom


def _aligned(coin: Coin, i: int, lookback: int) -> bool:
    if i < lookback:
        return False
    return int(coin.ts[i]) - int(coin.ts[i - lookback]) == lookback * DAY_MS


def _ret(coin: Coin, i: int, lookback: int) -> float:
    if not _aligned(coin, i, lookback):
        return math.nan
    previous = float(coin.close[i - lookback])
    if previous <= 0.0:
        return math.nan
    return float(coin.close[i]) / previous - 1.0


def _obs(coin: Coin, i: int, dates: np.ndarray, signal: int, floor: float) -> Obs | None:
    if not _aligned(coin, i, HOLD - 1):
        return None
    quote = float(coin.quote[i])
    if quote < floor or float(coin.close[i]) <= 0.0:
        return None
    if is_peg(coin.close[i - (HOLD - 1) : i + 1]):
        return None
    entry_at = signal + 1
    exit_at = signal + 1 + HOLD
    if int(dates[exit_at]) - int(dates[signal]) != (HOLD + 1) * DAY_MS:
        return None
    entry_i = coin.idx.get(int(dates[entry_at]))
    exit_i = coin.idx.get(int(dates[exit_at]))
    if entry_i is None or exit_i is None:
        return None
    entry = float(coin.open[entry_i])
    exit_px = float(coin.open[exit_i])
    if entry <= 0.0 or exit_px <= 0.0:
        return None
    near = math.nan
    if _aligned(coin, i, HIGH_DAYS - 1):
        span = float(coin.high[i - (HIGH_DAYS - 1) : i + 1].max())
        if span > 0.0:
            near = float(coin.close[i]) / span
    volx = math.nan
    if _aligned(coin, i, HOLD):
        baseline = float(np.mean(coin.quote[i - HOLD : i]))
        if baseline > 0.0:
            volx = quote / baseline
    return Obs(
        base=coin.base,
        gross=exit_px / entry - 1.0,
        entry=entry,
        exit=exit_px,
        adv=quote,
        exit_ms=int(dates[exit_at]),
        ret1=_ret(coin, i, 1),
        ret7=_ret(coin, i, HOLD),
        ret28=_ret(coin, i, WARM),
        volx=volx,
        near=near,
        size=quote,
    )


def weeks_of(coins: Sequence[Coin], dates: np.ndarray, floor: float) -> list[list[Obs]]:
    found: list[list[Obs]] = []
    signal = WARM
    while signal + 1 + HOLD < len(dates):
        pool: list[Obs] = []
        for coin in coins:
            i = coin.idx.get(int(dates[signal]))
            if i is None:
                continue
            row = _obs(coin, i, dates, signal, floor)
            if row is not None and math.isfinite(row.ret7):
                pool.append(row)
        if len(pool) >= MIN_POOL:
            found.append(pool)
        signal += HOLD
    return found


def _mean(values: np.ndarray) -> float:
    if len(values) == 0:
        return math.nan
    return float(values.mean())


def _book(picks: list[list[Obs]]) -> tuple[float, float, dict[int, float]]:
    equity = START
    peak = START
    worst = 0.0
    year_pnl = {year: 0.0 for year in YEARS}
    for names in picks:
        if not names:
            continue
        slot = equity / len(names)
        rets = [wet_net(row.entry, row.exit, row.adv, notional=slot) for row in names]
        before = equity
        equity *= 1.0 + float(np.mean(rets))
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
        year = _year(names[0].exit_ms)
        year_pnl[year] = year_pnl.get(year, 0.0) + (equity - before)
    return equity, worst, year_pnl


def _year_mean(values: list[float], stamps: list[int]) -> dict[int, float]:
    out: dict[int, float] = {}
    for year in YEARS:
        picked = [
            value for value, stamp in zip(values, stamps, strict=True) if _year(stamp) == year
        ]
        out[year] = float(np.mean(picked)) if picked else math.nan
    return out


def _visible(corr: float, stat: float, years: dict[int, float]) -> bool:
    if not (corr > 0.0 and stat >= T_BAR):
        return False
    return all(math.isfinite(years[year]) and years[year] > 0.0 for year in YEARS)


def _clears(equity: float, year_pnl: dict[int, float], weeks: int, universe: float) -> bool:
    if weeks < MIN_WEEKS or equity <= START or equity <= universe:
        return False
    return all(year_pnl.get(year, 0.0) > 0.0 for year in YEARS)


def _slice(feature: str, side: str, weeks: list[list[Obs]], universe: float) -> Slice:
    corrs: list[float] = []
    stamps: list[int] = []
    grosses: list[float] = []
    nets: list[float] = []
    pools: list[int] = []
    widths: list[int] = []
    picks: list[list[Obs]] = []
    for pool in weeks:
        scores = np.asarray([row.score(feature) for row in pool], dtype=np.float64)
        keep = np.isfinite(scores)
        if int(keep.sum()) < MIN_POOL:
            continue
        rows = [row for row, ok in zip(pool, keep, strict=True) if ok]
        scores = scores[keep]
        forwards = np.asarray([row.gross for row in rows], dtype=np.float64)
        corrs.append(spearman(scores, forwards))
        stamps.append(rows[0].exit_ms)
        top, bottom = quintile_index([row.base for row in rows], scores)
        chosen = top if side == "top" else bottom
        picked_rows = [rows[int(i)] for i in chosen]
        picks.append(picked_rows)
        grosses.append(float(np.mean([row.gross for row in picked_rows])))
        nets.append(
            float(
                np.mean(
                    [wet_net(row.entry, row.exit, row.adv, notional=STAKE) for row in picked_rows]
                )
            )
        )
        pools.append(len(rows))
        widths.append(len(picked_rows))
    paired = [
        (value, stamp) for value, stamp in zip(corrs, stamps, strict=True) if math.isfinite(value)
    ]
    corr_arr = np.asarray([value for value, _stamp in paired], dtype=np.float64)
    year_corr = _year_mean(
        [value for value, _stamp in paired],
        [stamp for _value, stamp in paired],
    )
    corr = _mean(corr_arr)
    stat = t_stat(corr_arr)
    detectable = math.nan
    if len(corr_arr) >= 3:
        std = float(corr_arr.std(ddof=1))
        if std > 0.0:
            detectable = 2.0 * std / math.sqrt(len(corr_arr))
    equity, dd, year_pnl = _book(picks)
    return Slice(
        feature=feature,
        side=side,
        weeks=len(picks),
        pool=float(np.mean(pools)) if pools else math.nan,
        names=float(np.mean(widths)) if widths else math.nan,
        corr=corr,
        t=stat,
        detectable=detectable,
        year_corr=year_corr,
        gross=_mean(np.asarray(grosses, dtype=np.float64)),
        net=_mean(np.asarray(nets, dtype=np.float64)),
        equity=equity,
        dd=dd,
        year_pnl=year_pnl,
        visible=_visible(corr, stat, year_corr),
        clears=_clears(equity, year_pnl, len(picks), universe),
    )


def study(coins: Sequence[Coin], dates: np.ndarray, floor: float) -> Study:
    weeks = weeks_of(coins, dates, floor)
    bases = {row.base for pool in weeks for row in pool}
    universe_picks = [pool for pool in weeks]
    universe_equity, _dd, _years = _book(universe_picks)
    rows = [
        _slice(feature, side, weeks, universe_equity)
        for feature in SCORES
        for side in ("top", "bottom")
    ]
    # The correlation does not depend on the side. Copy the top-side
    # correlation onto the bottom row so both lines agree.
    by_feature = {row.feature: row for row in rows if row.side == "top"}
    for row in rows:
        if row.side == "bottom":
            source = by_feature[row.feature]
            row.corr = source.corr
            row.t = source.t
            row.detectable = source.detectable
            row.year_corr = source.year_corr
            row.visible = source.visible
    return Study(
        floor=floor,
        coins=len(bases),
        weeks=len(weeks),
        coin_weeks=sum(len(pool) for pool in weeks),
        universe_equity=universe_equity,
        rows=rows,
    )


def calendar(coins: Sequence[Coin]) -> np.ndarray:
    return master_dates(coins)

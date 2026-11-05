"""Score names before the next week, large and obscure apart.

The fit uses only weeks that have already closed. Inside a week each
feature is turned into a rank, and so is the forward return. A linear
fit on those past ranks scores the new week. The book buys the top
fifth at the next open and sells the open seven days later.

Features, locked: distance of the close to the twenty-day high, the
1-day, 7-day and 28-day returns, turnover against the prior week, and
the log of turnover. A name needs all six. The first 26 weeks are the
warmup. A sleeve needs 50 past names before it trades, and 10 names in
the week itself.

Sleeves are fit on their own past. Large means at least €1,000,000 of
quote that day. Obscure means at least €10,000 and under €1,000,000.
Two comparisons use the same weeks and the same names: the top fifth by
distance to the high, and every eligible name.

A rise is a forward move of +10%, +20% or +50%. The book starts at
€20,000, splits the stake equally, and pays the Bitvavo taker plus
impact. It clears the bar only when it ends above €20,000, is positive
in 2024, 2025 and 2026, and ends above the book of every eligible name
in that sleeve. Nothing is armed.

AlphaI covers a few months, and the earlier peak and taker studies did
not mark the next rise, so those streams are not features here.
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
    Obs,
    quintile_index,
    rank_average,
    weeks_of,
)
from bot.research.week_book.engine import START, Coin, master_dates

LARGE = 1_000_000.0
WARMUP = 26
MIN_TRAIN = 50
MIN_POOL = 10
LEVELS = (0.10, 0.20, 0.50)
YEARS = (2024, 2025, 2026)
FEATURES = ("near", "ret1", "ret7", "ret28", "volx", "log_size")
SLEEVES = ("all", "large", "obscure")
MODELS = ("engine", "near", "hold")


@dataclass
class Book:
    model: str
    sleeve: str
    equity: float
    dd: float
    weeks: int
    picks: int
    year_pnl: dict[int, float]
    gross: float
    hit: dict[float, float]
    base_hit: dict[float, float]
    year_lift: dict[int, float] = field(default_factory=dict)
    clears: bool = False


@dataclass
class Fit:
    sleeve: str
    weeks: int
    coef: dict[str, float]


@dataclass
class Engine:
    books: list[Book]
    fits: list[Fit]


def _year(stamp: int) -> int:
    return datetime.fromtimestamp(stamp / 1000, UTC).year


def sleeve_of(adv: float, sleeve: str) -> bool:
    if sleeve == "large":
        return adv >= LARGE
    if sleeve == "obscure":
        return adv < LARGE
    return True


def feature_row(row: Obs) -> np.ndarray | None:
    values = (row.near, row.ret1, row.ret7, row.ret28, row.volx, row.size)
    if not all(math.isfinite(value) for value in values) or row.size <= 0.0:
        return None
    return np.asarray(
        [row.near, row.ret1, row.ret7, row.ret28, row.volx, math.log(row.size)],
        dtype=np.float64,
    )


def _rank01(values: np.ndarray) -> np.ndarray:
    if len(values) == 1:
        return np.ones(1, dtype=np.float64)
    return (rank_average(values) - 1.0) / (len(values) - 1.0)


def feature_ranks(rows: Sequence[Obs]) -> np.ndarray | None:
    """Within-week ranks. The forward return is not an input."""
    raw = [feature_row(row) for row in rows]
    if not raw or any(vector is None for vector in raw):
        return None
    matrix = np.vstack(raw)
    return np.column_stack([_rank01(matrix[:, column]) for column in range(matrix.shape[1])])


def fit_score(
    history: Sequence[Sequence[Obs]],
    today: Sequence[Obs],
    *,
    min_train: int = MIN_TRAIN,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Score ``today`` from ranks fitted on earlier weeks only."""
    blocks: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    for week in history:
        ranks = feature_ranks(week)
        if ranks is None or len(week) < 2:
            continue
        blocks.append(ranks)
        targets.append(_rank01(np.asarray([row.gross for row in week], dtype=np.float64)))
    if not blocks or sum(len(block) for block in blocks) < min_train:
        return None
    today_ranks = feature_ranks(today)
    if today_ranks is None:
        return None
    ranks = np.vstack(blocks)
    target = np.concatenate(targets)
    coef, *_ = np.linalg.lstsq(
        np.column_stack([np.ones(len(ranks)), ranks]),
        target,
        rcond=None,
    )
    scores = np.column_stack([np.ones(len(today_ranks)), today_ranks]) @ coef
    return scores, coef


def _ready(week: Sequence[Obs], sleeve: str) -> list[Obs]:
    return [row for row in week if sleeve_of(row.adv, sleeve) and feature_row(row) is not None]


def _hits(values: list[float]) -> dict[float, float]:
    if not values:
        return {level: math.nan for level in LEVELS}
    array = np.asarray(values, dtype=np.float64)
    return {level: float(np.mean(array >= level)) for level in LEVELS}


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


def _lift(selected: list[tuple[int, float]], base: list[tuple[int, float]]) -> dict[int, float]:
    out: dict[int, float] = {}
    for year in YEARS:
        chosen = [value for stamp, value in selected if _year(stamp) == year]
        universe = [value for stamp, value in base if _year(stamp) == year]
        if not chosen or not universe:
            out[year] = math.nan
            continue
        base_rate = float(np.mean(np.asarray(universe) >= LEVELS[0]))
        rate = float(np.mean(np.asarray(chosen) >= LEVELS[0]))
        out[year] = rate / base_rate if base_rate > 0.0 else math.nan
    return out


def _clears(equity: float, year_pnl: dict[int, float], weeks: int, hold: float) -> bool:
    if weeks < 30 or equity <= START or equity <= hold:
        return False
    return all(year_pnl.get(year, 0.0) > 0.0 for year in YEARS)


def run(
    weeks: Sequence[Sequence[Obs]],
    *,
    warmup: int = WARMUP,
    min_train: int = MIN_TRAIN,
) -> Engine:
    picked: dict[tuple[str, str], list[list[Obs]]] = {
        (model, sleeve): [] for model in MODELS for sleeve in SLEEVES
    }
    chosen_gross: dict[tuple[str, str], list[float]] = {key: [] for key in picked}
    base_gross: dict[str, list[float]] = {sleeve: [] for sleeve in SLEEVES}
    chosen_year: dict[tuple[str, str], list[tuple[int, float]]] = {key: [] for key in picked}
    base_year: dict[str, list[tuple[int, float]]] = {sleeve: [] for sleeve in SLEEVES}
    coefs: dict[str, list[np.ndarray]] = {sleeve: [] for sleeve in SLEEVES}
    for index, week in enumerate(weeks):
        if index < warmup:
            continue
        for sleeve in SLEEVES:
            today = _ready(week, sleeve)
            history = [_ready(past, sleeve) for past in weeks[:index]]
            fitted = fit_score(history, today, min_train=min_train)
            if len(today) < MIN_POOL or fitted is None:
                continue
            scores, coef = fitted
            coefs[sleeve].append(coef)
            bases = [row.base for row in today]
            top, _bottom = quintile_index(bases, scores)
            near_score = np.asarray([row.near for row in today], dtype=np.float64)
            near_top, _near_bottom = quintile_index(bases, near_score)
            choice = {
                "engine": [today[int(i)] for i in top],
                "near": [today[int(i)] for i in near_top],
                "hold": today,
            }
            for model, names in choice.items():
                picked[(model, sleeve)].append(names)
                for row in names:
                    chosen_gross[(model, sleeve)].append(row.gross)
                    chosen_year[(model, sleeve)].append((row.exit_ms, row.gross))
            for row in today:
                base_gross[sleeve].append(row.gross)
                base_year[sleeve].append((row.exit_ms, row.gross))
    books: list[Book] = []
    for model in MODELS:
        for sleeve in SLEEVES:
            groups = picked[(model, sleeve)]
            equity, dd, year_pnl = _book(groups)
            hold_equity, _hold_dd, _hold_years = _book(picked[("hold", sleeve)])
            values = chosen_gross[(model, sleeve)]
            books.append(
                Book(
                    model=model,
                    sleeve=sleeve,
                    equity=equity,
                    dd=dd,
                    weeks=len(groups),
                    picks=len(values),
                    year_pnl=year_pnl,
                    gross=float(np.mean(values)) if values else math.nan,
                    hit=_hits(values),
                    base_hit=_hits(base_gross[sleeve]),
                    year_lift=_lift(chosen_year[(model, sleeve)], base_year[sleeve]),
                    clears=_clears(equity, year_pnl, len(groups), hold_equity),
                )
            )
    fits = []
    for sleeve in SLEEVES:
        rows = coefs[sleeve]
        if not rows:
            fits.append(Fit(sleeve, 0, {name: math.nan for name in ("intercept", *FEATURES)}))
            continue
        mean = np.mean(np.vstack(rows), axis=0)
        names = ("intercept", *FEATURES)
        fits.append(Fit(sleeve, len(rows), {name: float(mean[i]) for i, name in enumerate(names)}))
    return Engine(books, fits)


def build(coins: Sequence[Coin], dates: np.ndarray) -> Engine:
    return run(weeks_of(coins, dates, FLOOR_BROAD))


def tape_calendar(coins: Sequence[Coin]) -> np.ndarray:
    return master_dates(coins)

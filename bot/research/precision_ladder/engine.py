"""Precision of pointing at next week's risers.

A rise is measured from the next open to the open seven days later.
Four labels are fixed: any gain, +10%, +20% and +50%. The base rate is
the share of eligible names that earn the label. Precision is the share
of the names we point at. Recall is the share of all risers that were
among those names.

Slices are fixed: the single highest name, and the top 1%, 5%, 10% and
20% of the sleeve that week. Sleeves are every name from €10,000, the
large names from €1,000,000, and the obscure names in between. A week
needs 10 names. The first 26 weeks stay out of the measurement so a
model can train on them.

Scores, fixed before the run:

* near — close divided by the twenty-day high
* fresh — the same, with names that already rose over seven days sorted
  behind the names that did not
* reversal — the seven-day return, low to high
* size — the day's quote
* logit10 and logit20 — a logistic model of +10% and of +20%, fit only
  on weeks that have already closed, with balanced class weights and
  penalty C = 1

The headline maximum is the highest precision among cells with at least
100 picks. The absolute maximum is reported with its count as well.
Nothing is armed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression

from bot.research.cross_section.engine import FLOOR_BROAD, Obs, rank_average, weeks_of
from bot.research.rise_engine.engine import MIN_POOL, WARMUP, feature_row, sleeve_of
from bot.research.week_book.engine import Coin, master_dates

LABELS = (("groen", 0.0), ("+10%", 0.10), ("+20%", 0.20), ("+50%", 0.50))
SLICES = (("1 naam", None), ("1%", 0.01), ("5%", 0.05), ("10%", 0.10), ("20%", 0.20))
SCORES = ("near", "fresh", "reversal", "size", "logit10", "logit20")
HEADLINE_N = 100
MIN_ROWS = 20
YEARS = (2024, 2025, 2026)


@dataclass
class Cell:
    score: str
    sleeve: str
    slice: str
    n: int
    weeks: int
    gross: float
    precision: dict[str, float]
    recall: dict[str, float]
    base: dict[str, float]
    year_precision: dict[str, dict[int, float]]


def rank01(values: np.ndarray) -> np.ndarray:
    if len(values) == 1:
        return np.ones(1, dtype=np.float64)
    return (rank_average(values) - 1.0) / (len(values) - 1.0)


def _width(n: int, frac: float | None) -> int:
    if frac is None:
        return 1
    return min(n, max(1, int(round(n * frac))))


def _labels(gross: np.ndarray) -> dict[str, np.ndarray]:
    out = {}
    for name, level in LABELS:
        if level == 0.0:
            out[name] = gross > 0.0
        else:
            out[name] = gross >= level
    return out


def _simple_scores(rows: Sequence[Obs]) -> dict[str, np.ndarray]:
    near = np.asarray([row.near for row in rows], dtype=np.float64)
    ret7 = np.asarray([row.ret7 for row in rows], dtype=np.float64)
    fresh = near.copy()
    fresh[ret7 > 0.0] -= 2.0
    return {
        "near": near,
        "fresh": fresh,
        "reversal": -ret7,
        "size": np.log(np.asarray([row.size for row in rows], dtype=np.float64)),
    }


def _ranks(rows: Sequence[Obs]) -> np.ndarray:
    matrix = np.vstack([feature_row(row) for row in rows])
    return np.column_stack([rank01(matrix[:, column]) for column in range(matrix.shape[1])])


def _logit(
    history: Sequence[Sequence[Obs]], today: Sequence[Obs], level: float
) -> np.ndarray | None:
    blocks: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    for week in history:
        if len(week) < 2:
            continue
        gross = np.asarray([row.gross for row in week], dtype=np.float64)
        blocks.append(_ranks(week))
        targets.append(gross >= level if level > 0.0 else gross > 0.0)
    if not blocks:
        return None
    train_x = np.vstack(blocks)
    train_y = np.concatenate(targets).astype(np.int8)
    if len(train_y) < MIN_ROWS or train_y.min() == train_y.max():
        return None
    model = LogisticRegression(
        C=1.0,
        class_weight="balanced",
        solver="lbfgs",
        max_iter=200,
    )
    model.fit(train_x, train_y)
    return model.predict_proba(_ranks(today))[:, 1]


def _pick(rows: Sequence[Obs], scores: np.ndarray, frac: float | None) -> list[Obs]:
    order = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), rows[i].base))
    return [rows[i] for i in order[: _width(len(rows), frac)]]


def _rate(flags: list[bool]) -> float:
    if not flags:
        return math.nan
    return float(np.mean(flags))


def _cells_for_score(
    weeks: Sequence[Sequence[Obs]],
    *,
    score: str,
    sleeve: str,
    warmup: int,
) -> list[Cell]:
    picked: dict[str, list[Obs]] = {name: [] for name, _frac in SLICES}
    universe: list[Obs] = []
    used = 0
    for index, week in enumerate(weeks):
        if index < warmup or len(week) < MIN_POOL:
            continue
        if score in ("logit10", "logit20"):
            target = 0.10 if score == "logit10" else 0.20
            scores = _logit(weeks[:index], week, target)
            if scores is None:
                continue
        else:
            scores = _simple_scores(week)[score]
        universe.extend(week)
        used += 1
        for name, frac in SLICES:
            picked[name].extend(_pick(week, scores, frac))
    return [_cell(score, sleeve, name, picked[name], universe, used) for name, _frac in SLICES]


def _cell(
    score: str, sleeve: str, slice_name: str, chosen: list[Obs], universe: list[Obs], weeks: int
) -> Cell:
    chosen_gross = np.asarray([row.gross for row in chosen], dtype=np.float64)
    all_gross = np.asarray([row.gross for row in universe], dtype=np.float64)
    chosen_labels = (
        _labels(chosen_gross) if len(chosen_gross) else {name: np.asarray([]) for name, _ in LABELS}
    )
    all_labels = (
        _labels(all_gross) if len(all_gross) else {name: np.asarray([]) for name, _ in LABELS}
    )
    precision = {name: _rate(flags.tolist()) for name, flags in chosen_labels.items()}
    base = {name: _rate(flags.tolist()) for name, flags in all_labels.items()}
    recall = {}
    for name, _level in LABELS:
        total = float(all_labels[name].sum()) if len(all_labels[name]) else 0.0
        hit = float(chosen_labels[name].sum()) if len(chosen_labels[name]) else 0.0
        recall[name] = hit / total if total > 0.0 else math.nan
    year_precision: dict[str, dict[int, float]] = {}
    for name, _level in LABELS:
        year_precision[name] = {}
        for year in YEARS:
            flags = [
                (row.gross > 0.0) if name == "groen" else (row.gross >= _level)
                for row in chosen
                if row.exit_ms == year
            ]
            year_precision[name][year] = _rate(flags)
    return Cell(
        score=score,
        sleeve=sleeve,
        slice=slice_name,
        n=len(chosen),
        weeks=weeks,
        gross=float(chosen_gross.mean()) if len(chosen_gross) else math.nan,
        precision=precision,
        recall=recall,
        base=base,
        year_precision=year_precision,
    )


def measure(
    weeks: Sequence[Sequence[Obs]],
    *,
    warmup: int = WARMUP,
) -> list[Cell]:
    """One sleeve of weeks. ``exit_ms`` on each row must already be the year."""
    cells: list[Cell] = []
    for score in SCORES:
        cells.extend(_cells_for_score(weeks, score=score, sleeve="all", warmup=warmup))
    return cells


def _stamp_year(rows: Sequence[Obs]) -> list[Obs]:
    out = []
    for row in rows:
        out.append(
            Obs(
                row.base,
                row.gross,
                row.entry,
                row.exit,
                row.adv,
                _exit_year(row.exit_ms),
                row.ret1,
                row.ret7,
                row.ret28,
                row.volx,
                row.near,
                row.size,
            )
        )
    return out


def _exit_year(stamp: int) -> int:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(stamp / 1000, UTC).year


def run_tape(coins: Sequence[Coin], dates: np.ndarray, *, warmup: int = WARMUP) -> list[Cell]:
    raw = weeks_of(coins, dates, FLOOR_BROAD)
    cells: list[Cell] = []
    for sleeve in ("all", "large", "obscure"):
        weeks = []
        for week in raw:
            rows = [
                row for row in week if feature_row(row) is not None and sleeve_of(row.adv, sleeve)
            ]
            weeks.append(_stamp_year(rows))
        made = measure(weeks, warmup=warmup)
        for cell in made:
            cell.sleeve = sleeve
        cells.extend(made)
    return cells


def tape_calendar(coins: Sequence[Coin]) -> np.ndarray:
    return master_dates(coins)


def headline(cells: Sequence[Cell], label: str = "+10%") -> tuple[Cell | None, Cell | None]:
    """Best precision with at least 100 picks, and the best cell of any size."""
    ranked = [cell for cell in cells if cell.n > 0 and math.isfinite(cell.precision[label])]
    if not ranked:
        return None, None
    any_size = max(ranked, key=lambda cell: (cell.precision[label], cell.n))
    stable = [cell for cell in ranked if cell.n >= HEADLINE_N]
    if not stable:
        return None, any_size
    return max(stable, key=lambda cell: (cell.precision[label], cell.n)), any_size

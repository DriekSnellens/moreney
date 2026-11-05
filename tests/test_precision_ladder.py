"""Pointing at the top name uses the score, not the rise that follows."""

from __future__ import annotations

import pytest

from bot.research.cross_section.engine import Obs
from bot.research.precision_ladder.engine import Cell, headline, measure


def _row(base: str, near: float, gross: float, ret7: float) -> Obs:
    return Obs(
        base,
        gross,
        100.0,
        100.0 * (1.0 + gross),
        50_000.0,
        2024,
        0.01,
        ret7,
        0.03,
        1.1,
        near,
        50_000.0,
    )


def _pattern(reverse: bool) -> list[Obs]:
    rows = []
    for i in range(10):
        near = (i + 1) / 10.0
        risen = near >= 0.8
        if reverse:
            risen = not risen
        gross = 0.2 if risen else -0.05
        rows.append(_row(f"N{i:02d}", near, gross, near - 0.5))
    return rows


def test_the_top_name_is_the_high_score_and_the_base_rate_is_the_sleeve() -> None:
    cells = measure([_pattern(False)], warmup=0)
    one = next(cell for cell in cells if cell.score == "near" and cell.slice == "1 naam")
    fifth = next(cell for cell in cells if cell.score == "near" and cell.slice == "20%")
    assert one.n == 1
    assert one.precision["+10%"] == 1.0
    assert one.base["+10%"] == pytest.approx(0.3)
    assert one.recall["+10%"] == pytest.approx(1.0 / 3.0)
    assert fifth.n == 2
    assert fifth.precision["+10%"] == 1.0
    assert fifth.recall["+10%"] == pytest.approx(2.0 / 3.0)


def test_the_model_does_not_read_the_week_it_scores() -> None:
    cells = measure([_pattern(False), _pattern(False), _pattern(True)], warmup=2)
    picked = next(cell for cell in cells if cell.score == "logit10" and cell.slice == "1 naam")
    assert picked.n == 1
    assert picked.precision["+10%"] == 0.0


def test_headline_ignores_a_perfect_cell_of_one_pick() -> None:
    stable, absolute = headline(
        [
            Cell("near", "all", "1 naam", 1, 1, 0.2, {"+10%": 1.0}, {}, {}, {}),
            Cell("near", "all", "20%", 100, 10, 0.02, {"+10%": 0.3}, {}, {}, {}),
        ]
    )
    assert stable is not None and stable.n == 100
    assert absolute is not None and absolute.n == 1

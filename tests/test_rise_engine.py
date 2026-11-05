"""The fit ranks a week it has not seen, and a flat week pays the fee."""

from __future__ import annotations

from bot.research.cross_section.engine import Obs
from bot.research.rise_engine.engine import START, run, sleeve_of
from bot.research.week_book.engine import START as BOOK_START


def _row(base: str, near: float, gross: float) -> Obs:
    return Obs(
        base=base,
        gross=gross,
        entry=100.0,
        exit=100.0 * (1.0 + gross),
        adv=50_000.0,
        exit_ms=1_704_067_200_000,
        ret1=0.01,
        ret7=0.02,
        ret28=0.03,
        volx=1.2,
        near=near,
        size=50_000.0,
    )


def _week(reverse: bool) -> list[Obs]:
    rows = []
    for i in range(10):
        near = (i + 1) / 10.0
        gross = (1.0 - near) if reverse else near
        rows.append(_row(f"N{i:02d}", near, gross))
    return rows


def test_this_weeks_rise_is_not_in_the_fit() -> None:
    result = run([_week(False), _week(True)], warmup=1, min_train=8)
    engine = next(row for row in result.books if row.model == "engine" and row.sleeve == "all")
    near = next(row for row in result.books if row.model == "near" and row.sleeve == "all")
    assert engine.weeks == 1
    assert engine.picks == 2
    assert engine.gross < 0.2
    assert near.gross < 0.2
    assert engine.gross == near.gross


def test_a_flat_book_pays_the_fee_and_large_is_not_obscure() -> None:
    def flat(name: str) -> Obs:
        row = _row(name, 0.5, 0.0)
        return Obs(
            row.base,
            0.0,
            100.0,
            100.0,
            2_000_000.0,
            row.exit_ms,
            row.ret1,
            row.ret7,
            row.ret28,
            row.volx,
            0.5,
            2_000_000.0,
        )

    week = [flat(f"L{i:02d}") for i in range(10)]
    result = run([week, week], warmup=1, min_train=8)
    hold = next(row for row in result.books if row.model == "hold" and row.sleeve == "large")
    assert hold.weeks == 1
    assert hold.equity < START
    assert START == BOOK_START
    assert sleeve_of(2_000_000.0, "large")
    assert not sleeve_of(2_000_000.0, "obscure")
    assert sleeve_of(50_000.0, "obscure")

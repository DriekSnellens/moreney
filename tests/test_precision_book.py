"""Sizing and exits stay on the pick. A losing year does not clear the bar."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bot.research.capital_flow.engine import wet_net
from bot.research.cross_section.engine import START
from bot.research.precision_book.engine import (
    YEARS,
    Book,
    Pick,
    clears,
    compound,
    fill_price,
)


def _stamp() -> int:
    return int(datetime(2024, 6, 15, tzinfo=UTC).timestamp() * 1000)


def test_the_target_fills_before_the_exit_open_and_ignores_the_last_high() -> None:
    opens = [100.0, 100.0, 100.0]
    highs = [101.0, 101.0, 150.0]
    lows = [99.0, 99.0, 99.0]
    assert fill_price(opens, highs, lows, 100.0, "doel10") == 100.0
    highs[1] = 110.0
    assert fill_price(opens, highs, lows, 100.0, "doel10") == pytest.approx(110.0)


def test_a_gap_through_the_stop_fills_at_the_open() -> None:
    opens = [100.0, 70.0, 100.0]
    highs = [101.0, 71.0, 101.0]
    lows = [99.0, 69.0, 99.0]
    assert fill_price(opens, highs, lows, 100.0, "stop15") == 70.0


def test_a_stop_inside_the_bar_fills_at_the_level() -> None:
    opens = [100.0, 100.0, 100.0]
    highs = [101.0, 101.0, 101.0]
    lows = [99.0, 80.0, 99.0]
    assert fill_price(opens, highs, lows, 100.0, "stop15") == pytest.approx(85.0)


def test_the_whole_book_compounds_the_wet_fill() -> None:
    pick = Pick(100.0, 1.0e12, _stamp(), {"week": 110.0})
    book = compound("near", "all", "1 naam", [[pick]], "vol", "week")
    expected = START * (1.0 + wet_net(100.0, 110.0, 1.0e12, notional=START))
    assert book.equity == pytest.approx(expected)
    assert book.mean_frac == pytest.approx(1.0)


def test_the_cap_leaves_the_rest_in_cash() -> None:
    pick = Pick(100.0, 10_000.0, _stamp(), {"week": 110.0})
    book = compound("size", "obscure", "1 naam", [[pick]], "kap", "week")
    slot = 0.02 * 10_000.0
    gain = slot * wet_net(100.0, 110.0, 10_000.0, notional=slot)
    assert book.equity == pytest.approx(START + gain)
    assert book.mean_frac == pytest.approx(slot / START)


def _passing() -> Book:
    pnl = {year: 1_000.0 for year in YEARS}
    traded = {year: 10 for year in YEARS}
    return Book(
        "near",
        "all",
        "1 naam",
        "vol",
        "week",
        equity=30_000.0,
        gross_equity=40_000.0,
        dd=0.1,
        weeks=40,
        traded=40,
        mean_frac=1.0,
        year_pnl=pnl,
        year_traded=traded,
    )


def test_one_down_year_keeps_the_book_off_the_bar() -> None:
    book = _passing()
    assert clears(book, 10_000.0)
    book.year_pnl[2025] = -1.0
    assert not clears(book, 10_000.0)


def test_holding_the_sleeve_is_not_a_strategy() -> None:
    book = _passing()
    book.score = "hold"
    assert not clears(book, 1.0)

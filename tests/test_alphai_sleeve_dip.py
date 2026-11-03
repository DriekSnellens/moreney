"""7% dip. The day's high and low are never used together. No network."""

from __future__ import annotations

import pytest

from bot.research.alphai_sleeve.book import SLEEVE_EUR
from bot.research.alphai_sleeve.dip import (
    CUSHION,
    MAKER,
    TARGET,
    DayBook,
    PathOut,
    bounce_path,
    simulate,
)


def test_the_offer_is_seven_percent_above_a_one_percent_cushion() -> None:
    assert CUSHION == 0.01
    assert TARGET == 0.07


def test_a_later_bar_sells_seven_percent_above_the_fill() -> None:
    bars = (
        (100.0, 101.0, 100.0, 100.5),
        (102.0, 103.0, 100.5, 102.0),
        (103.0, 109.0, 102.0, 104.0),
    )
    path = bounce_path(bars)
    assert path is not None
    assert path.maker is True
    assert path.buy_px == pytest.approx(100.0 * 1.01)
    assert path.close == 104.0


def test_the_entry_bar_cannot_be_the_sale() -> None:
    bars = (
        (100.0, 101.0, 100.0, 100.5),
        (102.0, 130.0, 100.5, 120.0),
        (120.0, 105.0, 104.0, 104.5),
    )
    path = bounce_path(bars)
    assert path is not None
    assert path.maker is False
    assert path.buy_px == pytest.approx(101.0)
    assert path.close == 104.5


def test_a_bid_already_through_the_open_does_not_fill() -> None:
    bars = (
        (100.0, 101.0, 100.0, 100.5),
        (100.5, 110.0, 99.0, 105.0),
    )
    assert bounce_path(bars) is None


def test_the_entry_does_not_use_the_current_bars_low() -> None:
    bars = (
        (112.0, 112.0, 110.0, 111.0),
        (100.0, 101.0, 90.0, 95.0),
    )
    assert bounce_path(bars) is None


def test_one_daily_bar_is_not_a_round_trip() -> None:
    assert bounce_path(((100.0, 120.0, 80.0, 110.0),)) is None


def test_two_names_split_the_sleeve_and_a_miss_keeps_its_cash() -> None:
    days = [f"2026-09-0{offset}" for offset in range(1, 7)]
    empty = DayBook(200_000.0, 48, None)
    filled = DayBook(200_000.0, 48, PathOut(101.0, 100.0, True))
    books = {
        "PEPE": {day: empty for day in days},
        "WIF": {day: empty for day in days},
    }
    books["PEPE"]["2026-09-06"] = filled
    run = simulate(
        books,
        universe=("PEPE", "WIF"),
        start="2026-09-06",
        end="2026-09-06",
        min_quote=10_000.0,
    )
    notional = SLEEVE_EUR / 2.0
    expected = notional * (1.0 - MAKER) ** 2 * (1.0 + TARGET) - notional
    assert run.fills == 1
    assert run.maker_exits == 1
    assert run.flattens == 0
    assert run.pnl == pytest.approx(expected)
    assert run.names[0][0] == "PEPE"
    assert run.names[0][1] == pytest.approx(expected)
    assert len(run.names) == 1

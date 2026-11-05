"""The close print is visible. The next session's print is not."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bot.research.capital_flow.engine import wet_net
from bot.research.cmc_regime.engine import (
    START,
    Market,
    Order,
    Point,
    View,
    allows,
    compound,
)
from bot.research.week_book.engine import DAY_MS


def _ms(day: int) -> int:
    return int(datetime(2026, 10, day, tzinfo=UTC).timestamp() * 1000)


def _market(asof: int, now: float, prev: float) -> Market:
    points = [Point(asof - age * DAY_MS, prev if age else now, 10.0, 50.0) for age in range(8)]
    points.append(Point(asof + DAY_MS, now * 10.0, 10.0, 50.0))
    return Market(points)


def test_the_next_sessions_print_stays_hidden() -> None:
    asof = _ms(21)
    market = _market(asof, 90.0, 100.0)
    view = market.view(asof - DAY_MS)
    assert view is not None
    assert view.mcap == pytest.approx(-0.10)
    assert market.view(asof) is not None
    later = market.view(asof)
    assert later is not None and later.mcap > 0.0


def test_a_falling_market_stays_in_cash() -> None:
    view = View(-0.02, 1.0, 1.0)
    assert not allows("mcap+", view)
    assert allows("mcap-", view)
    assert not allows("seizoen", view)
    assert allows("altijd", None)


def test_the_book_compounds_the_wet_fill_and_keeps_spare_slots_in_cash() -> None:
    order = Order(100.0, 110.0, 1.0e12, _ms(21))
    book = compound("top5", "altijd", [([order], 5)])
    slot = START / 5
    gain = slot * wet_net(100.0, 110.0, 1.0e12, notional=slot)
    assert book.equity == pytest.approx(START + gain)
    assert book.year_traded[2026] == 1

"""Break-even week. No network."""

from __future__ import annotations

import pytest

from bot.integrations.alphai.parse import AlphaIHeadline
from bot.research.alphai_sleeve.book import TAKER
from bot.research.alphai_sleeve.breakeven import (
    MAKER,
    TOP_N,
    SessionBars,
    bearish_week,
    break_even_price,
    simulate,
    top_names,
)


def test_the_sleeve_has_ten_slots() -> None:
    assert TOP_N == 10


def test_break_even_returns_the_cash_spent() -> None:
    cost = 1_000.0
    qty = cost * (1.0 - TAKER) / 100.0
    price = break_even_price(cost, qty)
    assert price > 100.0
    assert qty * price * (1.0 - MAKER) == pytest.approx(cost)


def test_a_tie_is_not_a_bearish_week() -> None:
    assert bearish_week({"PEPE": (2, 1)}) is False
    assert bearish_week({"PEPE": (1, 1), "WIF": (0, 0)}) is False
    assert bearish_week({"PEPE": (2, 0), "WIF": (0, 3)}) is True


def test_the_tenth_name_wins_the_tie_alphabetically() -> None:
    counts = {
        "BBB": (4, 1),
        "AAA": (4, 1),
        "CCC": (2, 0),
        "DDD": (1, 1),
    }
    assert top_names(counts, ["BBB", "AAA", "CCC", "DDD"], n=2) == ["AAA", "BBB"]
    assert "DDD" not in top_names(counts, list(counts), n=10)


def test_one_bullish_name_gets_one_slot() -> None:
    run = simulate(
        _books({"2026-09-06": (100.0, 100.0, 100.0, 100.0)}),
        {"2026-09-05": [_story("2026-09-05", "PEPE-USD", "positive")]},
        universe=("PEPE",),
        mode="scratch",
        start="2026-09-06",
        end="2026-09-06",
        min_bars=1,
    )
    assert run.buys == 1
    assert -25.0 < run.pnl < 0.0


def test_two_names_each_take_one_slot() -> None:
    books = {
        "PEPE": _rows({"2026-09-06": (100.0, 100.0, 100.0, 100.0)}),
        "WIF": _rows({"2026-09-06": (100.0, 100.0, 100.0, 100.0)}),
    }
    grouped = {
        "2026-09-05": [
            _story("2026-09-05", "PEPE-USD", "positive"),
            _story("2026-09-05", "WIF-USD", "positive"),
        ]
    }
    run = simulate(
        books,
        grouped,
        universe=("PEPE", "WIF"),
        mode="scratch",
        start="2026-09-06",
        end="2026-09-06",
        min_bars=1,
    )
    assert run.buys == 2
    assert -40.0 < run.pnl < 0.0


def test_the_entry_day_high_is_not_the_sale() -> None:
    run = _scratch_path(
        {
            "2026-09-06": (100.0, 200.0, 100.0, 100.0),
            "2026-09-07": (90.0, 96.0, 80.0, 90.0),
            "2026-09-08": (90.0, 130.0, 90.0, 90.0),
        }
    )
    assert run.scratch_maker == 1
    assert run.scratch_cross == 0
    assert run.flattens == 0
    assert run.rotations == 0
    assert run.pnl == pytest.approx(0.0, abs=1e-6)


def test_a_miss_rests_until_a_later_day_and_hold_keeps_the_bag() -> None:
    path = {
        "2026-09-06": (100.0, 101.0, 99.0, 100.0),
        "2026-09-07": (90.0, 96.0, 80.0, 90.0),
        "2026-09-08": (90.0, 130.0, 90.0, 90.0),
    }
    scratch = _scratch_path(path)
    hold = _scratch_path(path, mode="hold")
    assert scratch.pnl == pytest.approx(0.0, abs=1e-6)
    assert scratch.open_into_flatten == 0
    assert hold.flattens == 1
    assert hold.pnl < -50.0


def test_a_day_that_never_reaches_break_even_is_still_open() -> None:
    run = _scratch_path(
        {
            "2026-09-06": (100.0, 101.0, 99.0, 100.0),
            "2026-09-07": (90.0, 96.0, 80.0, 90.0),
            "2026-09-08": (91.0, 97.0, 85.0, 90.0),
        }
    )
    assert run.scratch_maker == 0
    assert run.scratch_cross == 0
    assert run.flattens == 1
    assert run.open_into_flatten == 1
    assert run.pnl < -50.0


def test_an_open_already_through_break_even_crosses() -> None:
    run = _scratch_path(
        {
            "2026-09-06": (100.0, 101.0, 99.0, 100.0),
            "2026-09-07": (120.0, 121.0, 119.0, 120.0),
        }
    )
    assert run.scratch_cross == 1
    assert run.scratch_maker == 0
    assert run.flattens == 0
    assert run.pnl > 100.0


def test_a_bearish_week_buys_nothing() -> None:
    books = {"PEPE": _rows({"2026-09-07": (100.0, 100.0, 100.0, 100.0)})}
    grouped = {
        "2026-09-06": [
            _story("2026-09-06", "PEPE-USD", "positive"),
            _story("2026-09-06", "PEPE-USD", "negative"),
            _story("2026-09-06", "PEPE-USD", "negative"),
        ]
    }
    run = simulate(
        books,
        grouped,
        universe=("PEPE",),
        mode="scratch",
        start="2026-09-07",
        end="2026-09-07",
        min_bars=1,
    )
    assert run.bearish_days == 1
    assert run.buys == 0
    assert run.pnl == 0.0


def _scratch_path(sessions: dict[str, tuple[float, float, float, float]], mode: str = "scratch"):
    grouped = {
        "2026-09-05": [_story("2026-09-05", "PEPE-USD", "positive") for _ in range(3)],
        "2026-09-06": [_story("2026-09-06", "WIF-USD", "negative") for _ in range(5)],
    }
    return simulate(
        {"PEPE": _rows(sessions), "WIF": _rows({})},
        grouped,
        universe=("PEPE", "WIF"),
        mode=mode,
        start="2026-09-06",
        end=max(sessions),
        min_bars=1,
    )


def _books(sessions: dict[str, tuple[float, float, float, float]]) -> dict[str, list[SessionBars]]:
    return {"PEPE": _rows(sessions)}


def _rows(sessions: dict[str, tuple[float, float, float, float]]) -> list[SessionBars]:
    rows = [_bars(f"2026-09-0{offset}", 100.0, 100.0, 100.0, 100.0) for offset in range(1, 6)]
    for day, prices in sessions.items():
        rows.append(_bars(day, *prices))
    return rows


def _bars(day: str, open_: float, high: float, low: float, close: float) -> SessionBars:
    return SessionBars(day, 1_000_000_000.0, 1, ((open_, high, low, close),))


def _story(day: str, ticker: str, sentiment: str) -> AlphaIHeadline:
    return AlphaIHeadline(
        uid=f"{day}-{ticker}-{sentiment}-{id(sentiment)}",
        title="story",
        relevance=6,
        category="crypto",
        tickers=(ticker,),
        sentiments={ticker: sentiment},
        published_at=f"{day}T12:00:00Z",
    )

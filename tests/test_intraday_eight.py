"""Intraday fill and book rules. No network."""

from __future__ import annotations

import numpy as np

from bot.research.intraday_eight.book import run_book
from bot.research.intraday_eight.exits import walk_exit
from bot.research.intraday_eight.indicators import prev, shift_extreme, supertrend
from bot.research.intraday_eight.strategies import Trade, _funding_between


def test_a_stop_fills_before_a_target_on_the_same_bar() -> None:
    opened = np.array([100.0, 100.0])
    high = np.array([100.0, 130.0])
    low = np.array([100.0, 80.0])
    close = np.array([100.0, 120.0])
    index, price, reason = walk_exit(opened, high, low, close, 1, 95.0, 110.0, 2, 1, None)
    assert (index, price, reason) == (1, 95.0, "stop")


def test_a_gap_through_the_stop_fills_at_the_open() -> None:
    opened = np.array([100.0, 70.0])
    high = np.array([100.0, 75.0])
    low = np.array([100.0, 60.0])
    close = np.array([100.0, 72.0])
    index, price, reason = walk_exit(opened, high, low, close, 1, 95.0, None, 2, 1, None)
    assert (index, price, reason) == (1, 70.0, "gap")


def test_the_prior_window_excludes_the_current_bar() -> None:
    values = np.arange(1, 21, dtype=float)
    prior = shift_extreme(values, 16, highest=True)
    assert prior[16] == 16.0


def test_prev_does_not_wrap_the_last_value_to_the_front() -> None:
    shifted = prev(np.array([1.0, 2.0, 3.0]))
    assert np.isnan(shifted[0])
    assert shifted[1] == 1.0
    assert shifted[2] == 2.0


def test_funding_during_the_hold_excludes_the_entry_settlement() -> None:
    stamps = np.array([100, 200, 300])
    rates = np.array([0.1, 0.2, 0.3])
    assert _funding_between(stamps, rates, 100, 300) == 0.5


def test_supertrend_can_flip_down_after_a_decline() -> None:
    close = np.linspace(100.0, 80.0, 40)
    high = close + 0.5
    low = close - 0.5
    direction, line = supertrend(high, low, close, n=5, mult=2.0)
    assert np.isfinite(line[10:]).all()
    assert (direction < 0).any()


def test_the_book_holds_four_names_and_skips_the_fifth() -> None:
    trades = [
        Trade(f"N{i}", 1, 1_000 + i, 5_000, 100.0, 110.0, 1_000_000.0, float(i), "target")
        for i in range(5)
    ]
    result = run_book(trades, mode="spot_long", book=20_000.0)
    assert result.trades == 4
    assert result.pnl > 0

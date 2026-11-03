"""Intraday fill and book rules. No network."""

from __future__ import annotations

import numpy as np

from bot.research.intraday_eight.book import measure, run_book
from bot.research.intraday_eight.exits import resting_limit, walk_exit
from bot.research.intraday_eight.indicators import prev, quote_adv, shift_extreme, supertrend
from bot.research.intraday_eight.strategies import Trade, _funding_between, _prior_day_range


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


def test_a_resting_bid_fills_at_the_limit_not_the_low() -> None:
    opened = np.array([110.0, 100.0])
    high = np.array([110.0, 101.0])
    low = np.array([100.0, 90.0])
    close = np.array([105.0, 96.0])
    result = resting_limit(opened, high, low, close, 1, 2, 2, 95.0, 80.0, 1, None, None)
    assert result is not None
    assert result[0] == 1
    assert result[1] == 95.0
    assert result[4] == "eod"


def test_an_open_through_the_bid_fills_at_the_open() -> None:
    opened = np.array([100.0, 90.0])
    high = np.array([100.0, 92.0])
    low = np.array([100.0, 88.0])
    close = np.array([100.0, 91.0])
    result = resting_limit(opened, high, low, close, 1, 2, 2, 95.0, 80.0, 1, None, None)
    assert result is not None
    assert result[1] == 90.0


def test_the_same_bar_stop_fills_the_stop_not_the_low() -> None:
    opened = np.array([100.0, 100.0])
    high = np.array([100.0, 101.0])
    low = np.array([100.0, 70.0])
    close = np.array([100.0, 90.0])
    result = resting_limit(opened, high, low, close, 1, 2, 3, 95.0, 80.0, 1, None, None)
    assert result == (1, 95.0, 1, 80.0, "stop")


def test_a_bar_that_misses_the_limit_does_not_fill() -> None:
    opened = np.array([100.0, 100.0, 100.0])
    high = np.array([101.0, 101.0, 101.0])
    low = np.array([99.0, 96.0, 90.0])
    close = np.array([100.0, 97.0, 95.0])
    result = resting_limit(opened, high, low, close, 1, 2, 3, 95.0, 80.0, 1, None, None)
    assert result is None


def test_a_signal_on_the_fill_bar_sells_the_next_open() -> None:
    opened = np.array([100.0, 100.0, 90.0])
    high = np.array([100.0, 101.0, 91.0])
    low = np.array([100.0, 94.0, 89.0])
    close = np.array([100.0, 96.0, 90.0])
    signal = np.array([False, True, False])
    result = resting_limit(opened, high, low, close, 1, 2, 3, 95.0, 80.0, 1, None, signal)
    assert result == (1, 95.0, 2, 90.0, "signal")


def test_quote_adv_uses_prior_daily_turnover() -> None:
    day = 86_400_000
    ts = np.array([0, 1, day, day + 1, 2 * day, 2 * day + 1])
    close = np.ones(6)
    volume = np.array([10.0, 30.0, 100.0, 100.0, 0.0, 0.0])
    adv = quote_adv(ts, close, volume, days=2)
    assert np.isnan(adv[0])
    assert np.isnan(adv[2])
    assert adv[4] == 120.0


def test_prior_day_range_is_yesterday_not_today() -> None:
    day = 86_400_000
    ts = np.array([0, 900_000, day, day + 900_000])
    high = np.array([10.0, 12.0, 9.0, 8.0])
    low = np.array([8.0, 9.0, 7.0, 6.0])
    prior_high, prior_low = _prior_day_range(ts, high, low)
    assert np.isnan(prior_high[0])
    assert prior_high[2] == 12.0
    assert prior_low[2] == 8.0
    assert prior_high[3] == 12.0


def test_measure_sums_every_signal_past_one_stake() -> None:
    trades = [
        Trade(f"N{i}", 1, 1_000, 2_000, 100.0, 90.0, 1_000_000.0, 1.0, "stop")
        for i in range(5)
    ]
    edge = measure(trades, mode="spot_long", stake=5_000.0)
    account = run_book(trades, mode="spot_long", book=20_000.0, compound=False)
    assert edge.trades == 5
    assert account.trades == 4
    assert edge.pnl < account.pnl
    assert account.max_dd > 0


def test_the_book_holds_four_names_and_skips_the_fifth() -> None:
    trades = [
        Trade(f"N{i}", 1, 1_000 + i, 5_000, 100.0, 110.0, 1_000_000.0, float(i), "target")
        for i in range(5)
    ]
    result = run_book(trades, mode="spot_long", book=20_000.0)
    assert result.trades == 4
    assert result.pnl > 0

"""AlphaI sentiment hit-rate rules: next open, sign, gaps, ties."""

from __future__ import annotations

import numpy as np

from bot.research.alphai_sentiment_accuracy.score import (
    BAR_MS,
    forward_return,
    is_hit,
    majority_side,
    rate_of,
    side_of,
)


def _tape(n: int = 16, start: int = 1_000_000, px0: float = 100.0, step: float = 1.0):
    ts = np.arange(start, start + n * BAR_MS, BAR_MS, dtype=np.int64)
    opens = np.array([px0 + i * step for i in range(n)], dtype=np.float64)
    return ts, opens


def test_side_of_maps_the_labels_in_the_feed() -> None:
    assert side_of("positive") == 1
    assert side_of("Negative") == -1
    assert side_of("neutral") == 0
    assert side_of("mixed") is None


def test_entry_is_the_next_open_and_the_sign_is_the_hit() -> None:
    ts, opens = _tape()
    # Inside the first bar: entry is the next open, one step higher.
    fwd = forward_return(ts, opens, int(ts[0]) + 60_000, 4)
    assert fwd is not None
    ret, entry_ms, exit_ms = fwd
    assert entry_ms == int(ts[1])
    assert exit_ms == int(ts[5])
    assert ret > 0
    assert is_hit(1, ret)
    assert not is_hit(-1, ret)


def test_gap_and_a_late_bar_drop_the_call() -> None:
    ts, opens = _tape()
    gapped_ts = np.delete(ts, 3)
    gapped_opens = np.delete(opens, 3)
    assert forward_return(gapped_ts, gapped_opens, int(ts[0]), 4) is None
    # The bar that should be the entry was removed, so the next open is too late.
    late_ts = np.concatenate([ts[:1], ts[2:]])
    late_opens = np.concatenate([opens[:1], opens[2:]])
    assert forward_return(late_ts, late_opens, int(ts[0]) + 1, 1) is None


def test_zero_return_is_a_miss_and_the_null_uses_the_mix() -> None:
    rate = rate_of([(1, 0.0), (1, 0.02), (-1, -0.01), (-1, 0.03)])
    assert rate is not None
    assert rate.n == 4
    assert rate.hit_pct == 50.0
    assert rate.bull_hit_pct == 50.0
    assert rate.bear_hit_pct == 50.0


def test_majority_drops_a_tie() -> None:
    assert majority_side([1, 1, -1]) == 1
    assert majority_side([1, -1]) is None
    assert majority_side([]) is None


def _ohlc(n: int = 6):
    ts = np.arange(0, n * BAR_MS, BAR_MS, dtype=np.int64)
    opens = np.full(n, 100.0, dtype=np.float64)
    highs = np.full(n, 100.0, dtype=np.float64)
    lows = np.full(n, 100.0, dtype=np.float64)
    closes = np.full(n, 100.0, dtype=np.float64)
    return ts, opens, highs, lows, closes


def test_stop_fills_at_the_stop() -> None:
    from bot.research.alphai_sentiment_accuracy.payoff import walk_exit

    ts, opens, highs, lows, closes = _ohlc()
    lows[1] = 93.0
    fill = walk_exit(ts, opens, highs, lows, closes, 0, 1, 0.06, 0.0, 4, 0.0)
    assert fill is not None
    assert fill.reason == "stop"
    assert abs(fill.net - (-0.06)) < 1e-12
    # Short mirror: the adverse stop is 6% above the entry.
    highs[1] = 107.0
    short = walk_exit(ts, opens, highs, lows, closes, 0, -1, 0.06, 0.0, 4, 0.0)
    assert short is not None
    assert short.reason == "stop"
    assert abs(short.net - (100.0 / 106.0 - 1.0)) < 1e-12


def test_gap_through_the_stop_fills_at_the_open() -> None:
    from bot.research.alphai_sentiment_accuracy.payoff import walk_exit

    ts, opens, highs, lows, closes = _ohlc()
    opens[1] = 90.0
    highs[1] = 91.0
    lows[1] = 89.0
    closes[1] = 90.0
    fill = walk_exit(ts, opens, highs, lows, closes, 0, 1, 0.06, 0.0, 4, 0.0)
    assert fill is not None
    assert fill.reason == "stop"
    assert abs(fill.net - (-0.10)) < 1e-12


def test_time_exit_when_the_stop_is_not_hit() -> None:
    from bot.research.alphai_sentiment_accuracy.payoff import walk_exit

    ts, opens, highs, lows, closes = _ohlc()
    opens[4] = 105.0
    highs[:] = 102.0
    lows[:] = 99.0
    fill = walk_exit(ts, opens, highs, lows, closes, 0, 1, 0.06, 0.0, 4, 0.0)
    assert fill is not None
    assert fill.reason == "time"
    assert abs(fill.net - 0.05) < 1e-12
    assert fill.exit_ms == int(ts[4])


def test_trail_does_not_cap_a_winner_that_never_gives_back() -> None:
    from bot.research.alphai_sentiment_accuracy.payoff import walk_exit

    ts, opens, highs, lows, closes = _ohlc(4)
    opens[:] = [100.0, 110.0, 120.0, 130.0]
    highs[:] = [110.0, 120.0, 130.0, 140.0]
    lows[:] = [100.0, 109.0, 119.0, 129.0]
    closes[:] = [109.0, 119.0, 129.0, 139.0]
    fill = walk_exit(ts, opens, highs, lows, closes, 0, 1, 0.06, 0.10, 3, 0.0)
    assert fill is not None
    assert fill.reason == "time"
    assert abs(fill.net - 0.30) < 1e-12


def test_trail_fills_the_next_open_after_a_giveback() -> None:
    from bot.research.alphai_sentiment_accuracy.payoff import walk_exit

    ts, opens, highs, lows, closes = _ohlc(5)
    opens[:] = [100.0, 119.0, 100.0, 90.0, 80.0]
    highs[:] = [120.0, 119.0, 101.0, 91.0, 81.0]
    lows[:] = [100.0, 118.0, 99.0, 89.0, 79.0]
    closes[:] = [119.0, 100.0, 100.0, 90.0, 80.0]
    fill = walk_exit(ts, opens, highs, lows, closes, 0, 1, 0.06, 0.10, 4, 0.0)
    assert fill is not None
    assert fill.reason == "trail"
    assert abs(fill.net) < 1e-12
    assert fill.exit_ms == int(ts[2])


def test_payoff_entry_is_the_next_open() -> None:
    from bot.research.alphai_sentiment_accuracy.payoff import entry_index

    ts = np.arange(0, 6 * BAR_MS, BAR_MS, dtype=np.int64)
    assert entry_index(ts, 60_000, 2) == 1
    assert entry_index(ts, 0, 2) == 0


def test_fee_on_an_unchanged_price_is_about_half_a_percent() -> None:
    from bot.research.alphai_sentiment_accuracy.payoff import FEE, net_return

    net = net_return(1, 100.0, 100.0, FEE)
    assert abs(net - ((1.0 - FEE) / (1.0 + FEE) - 1.0)) < 1e-15
    assert abs(net - (-0.0049875)) < 1e-6
    assert abs(net_return(-1, 100.0, 100.0, FEE) - net) < 1e-15


def test_slot_book_skips_an_open_base_and_a_full_book() -> None:
    from bot.research.alphai_sentiment_accuracy.payoff import slot_book

    trades = [
        (0, 10, "A", 0.10),
        (0, 10, "A", 0.50),
        (0, 10, "B", 0.10),
        (0, 10, "C", 0.10),
        (0, 10, "D", 0.10),
        (0, 10, "E", -0.50),
        (10, 20, "E", 0.20),
    ]
    taken, pnl = slot_book(trades)
    assert taken == 5
    assert abs(pnl - (0.10 * 4 + 0.20) * 5_000.0) < 1e-6

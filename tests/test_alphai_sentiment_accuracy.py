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

"""A small peak is confirmed only after it has turned, and news stays in the past."""

from __future__ import annotations

import numpy as np

from bot.research.small_peak.engine import (
    BAR_MS,
    PRIMARY,
    PeakSpec,
    labels_between,
    measure_peak,
    net_long,
    prepare,
    select_peaks,
    state_of,
    volume_bin,
    volume_ratio,
)


def _tape(n: int = 900) -> tuple[np.ndarray, ...]:
    ts = 1_700_000_000_000 + np.arange(n) * BAR_MS
    close = np.full(n, 100.0)
    high = np.full(n, 100.2)
    low = np.full(n, 99.8)
    open_ = np.full(n, 100.0)
    # Climb into a 3% peak at bar 140. The prior 16 lows sit near 100.
    for i, px in enumerate(np.linspace(100.0, 103.0, 12)):
        bar = 129 + i
        open_[bar] = float(px)
        close[bar] = float(px)
        high[bar] = float(px)
        low[bar] = float(px) - 0.05
    high[140] = 103.0
    close[140] = 103.0
    # Confirmation gives back about 1% and does not take the high out.
    open_[141] = 102.6
    high[141] = 102.7
    low[141] = 101.8
    close[141] = 101.97
    # Entry, then a push through the peak.
    for bar in range(142, 160):
        open_[bar] = 102.0
        close[bar] = 107.0
        high[bar] = 108.0
        low[bar] = 101.9
    volume = np.full(n, 500.0)
    return ts, open_, high, low, close, volume


def test_small_peak_is_confirmed_and_measures_the_overshoot() -> None:
    ts, open_, high, low, close, volume = _tape()
    prepared = prepare(high, low, close, volume)
    assert prepared is not None
    idx, rise, pullback = prepared
    chosen = select_peaks(idx, rise, pullback, PRIMARY)
    assert 140 in set(chosen.tolist())
    peak = measure_peak(ts, open_, high, 140, float(high[140]))
    assert peak is not None
    assert peak.entry_ms == int(ts[142])
    assert peak.overshoot[0] > 0.03
    assert peak.net[0] < peak.overshoot[0]


def test_a_large_spike_is_not_a_small_peak() -> None:
    ts, open_, high, low, close, volume = _tape()
    high[140] = 125.0
    close[140] = 125.0
    high[141] = 120.0
    close[141] = 123.0
    prepared = prepare(high, low, close, volume)
    assert prepared is not None
    idx, rise, pullback = prepared
    chosen = select_peaks(idx, rise, pullback, PRIMARY)
    assert 140 not in set(chosen.tolist())
    wide = PeakSpec("wide", 0.10, 0.30)
    assert 140 in set(select_peaks(idx, rise, pullback, wide).tolist())


def test_flat_round_trip_loses_the_taker() -> None:
    assert net_long(100.0, 100.0) < 0.0


def test_volume_ratio_uses_the_prior_day_and_ignores_the_future() -> None:
    quote = np.ones(200)
    assert volume_ratio(quote, np.asarray([199]))[0] == 1.0
    quote[192:200] = 2.0
    assert volume_ratio(quote, np.asarray([199]))[0] == 2.0
    before = volume_ratio(quote, np.asarray([198]))[0]
    quote[199] = 50.0
    assert volume_ratio(quote, np.asarray([198]))[0] == before
    assert volume_bin(0.5) == "onder 1×"
    assert volume_bin(1.5) == "1–2×"
    assert volume_bin(2.0) == "vanaf 2×"


def test_news_at_the_entry_is_not_yet_visible() -> None:
    stamps = np.asarray([1_000, 2_000, 3_000], dtype=np.int64)
    sides = np.asarray([1, 1, -1], dtype=np.int64)
    assert labels_between(stamps, sides, 3_000, lookback_ms=5_000) == [1, 1]
    assert state_of([1, -1], tie="quiet") == "quiet"
    assert state_of([1, -1], tie="mixed") == "mixed"
    assert state_of([], tie="quiet") == "quiet"

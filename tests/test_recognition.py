"""The level sell stops at the first touch and ignores the bars after it."""

from __future__ import annotations

import math

import numpy as np

from bot.research.recognition.engine import (
    discount_bin,
    is_higher_low,
    scan_paths,
    walk_level,
)
from bot.research.small_peak.engine import BAR_MS


def test_level_sell_fills_at_the_peak_and_keeps_the_later_crash_out() -> None:
    open_ = np.asarray([99.0, 98.5, 99.2, 110.0, 90.0])
    high = np.asarray([99.5, 99.0, 100.5, 500.0, 90.0])
    low = np.asarray([98.0, 97.0, 99.0, 50.0, 90.0])
    fill, touched, bars, mae = walk_level(open_, high, low, 0, 4, 100.0)
    assert fill == 100.0
    assert touched == 1.0
    assert bars == 2.0
    assert mae == 97.0 / 99.0 - 1.0
    missed, touched, bars, mae = walk_level(open_, high, low, 0, 3, 200.0)
    assert missed == 110.0
    assert touched == 0.0
    assert math.isnan(bars)
    assert mae == 97.0 / 99.0 - 1.0
    gap_open = np.asarray([101.0, 90.0])
    gap_high = np.asarray([101.0, 90.0])
    gap_low = np.asarray([100.5, 90.0])
    fill, touched, bars, _mae = walk_level(gap_open, gap_high, gap_low, 0, 1, 100.0)
    assert fill == 101.0
    assert touched == 1.0
    assert bars == 0.0


def test_higher_low_uses_the_midpoint_and_distance_bins_are_locked() -> None:
    low = np.full(20, 90.0)
    low[17] = 96.0
    assert is_higher_low(low, 16, 100.0)
    low[17] = 94.0
    assert not is_higher_low(low, 16, 100.0)
    assert discount_bin(-0.01) == "entry boven het niveau"
    assert discount_bin(0.005) == "0–1% eronder"
    assert discount_bin(0.015) == "1–2% eronder"
    assert discount_bin(0.03) == "vanaf 2% eronder"


def test_scan_sells_the_planted_peak_at_the_high_not_at_the_later_spike() -> None:
    n = 400
    ts = 1_700_000_000_000 + np.arange(n) * BAR_MS
    open_ = np.full(n, 100.0)
    close = np.full(n, 100.0)
    high = np.full(n, 100.2)
    low = np.full(n, 99.8)
    for i, px in enumerate(np.linspace(100.0, 103.0, 12)):
        bar = 129 + i
        open_[bar] = float(px)
        close[bar] = float(px)
        high[bar] = float(px)
        low[bar] = float(px) - 0.05
    high[140] = 103.0
    close[140] = 103.0
    open_[141] = 102.6
    high[141] = 102.7
    low[141] = 101.8
    close[141] = 101.97
    open_[142] = 102.0
    high[142] = 103.4
    low[142] = 101.9
    close[142] = 103.0
    high[160] = 150.0
    volume = np.full(n, 500.0)
    marks = scan_paths("AAA", ts, open_, high, low, close, volume, np.asarray([]), start_ms=0)
    peak = next(mark for mark in marks if mark.kind == "piek" and mark.entry_ms == int(ts[142]))
    assert peak.touch_24 == 1.0
    assert peak.bars_24 == 0.0
    assert peak.reclaim_24 > 0.0
    assert peak.mfe_24 > peak.discount

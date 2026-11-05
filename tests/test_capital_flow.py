"""A peg stays out of the large basket, and the fill is the next open."""

from __future__ import annotations

import math

from bot.research.capital_flow.engine import (
    DAY_MS,
    is_peg,
    run_days,
    sell_share,
    wet_net,
)

START = 1_704_067_200_000


def test_sell_share_and_peg_band() -> None:
    assert sell_share(10.0, 4.0) == 0.6
    assert math.isnan(sell_share(10.0, -1.0))
    assert is_peg([1.0, 1.001, 1.002, 0.999, 1.0, 1.001, 1.0])
    assert not is_peg([100.0, 100.0, 100.0, 108.0, 100.0, 100.0, 100.0])


def test_large_selling_skips_the_peg_and_fills_the_open_two_days_later() -> None:
    flow: dict[str, list] = {}
    bars: dict[str, list] = {}
    n_coins = 40
    max_volume = 10.0 + n_coins
    signal = 7
    for i in range(1, n_coins + 1):
        volume = 10.0 + i
        sold = 0.40 + 0.35 * (volume / max_volume)
        rise_px = 100.0 * (1.0 + 0.08 * (1.0 - volume / max_volume))
        flow_rows = []
        bar_rows = []
        for day in range(15):
            ts = START + day * DAY_MS
            vwap = 100.0 * (1.0 + 0.02 * ((day + i) % 5))
            flow_rows.append((ts, volume, vwap * volume, volume * (1.0 - sold)))
            opened = rise_px if day == signal + 2 else 100.0
            high = max(opened, rise_px if day == signal + 1 else opened)
            bar_rows.append((ts, opened, high, 100_000.0))
        flow[f"C{i}"] = flow_rows
        bars[f"C{i}"] = bar_rows
    flow["PEG"] = [
        (START + day * DAY_MS, 1_000_000_000.0, 1_000_000_000.0, 100_000_000.0) for day in range(15)
    ]

    day = next(row for row in run_days(flow, bars) if row.signal_ms == START + signal * DAY_MS)
    assert day.n_peg >= 1
    assert day.n_small >= 8
    assert 0.60 < day.large_sell < 0.85
    assert day.small_net > 0.02
    assert day.small_net > day.large_net
    assert wet_net(100.0, 100.0, 100_000.0) < 0.0

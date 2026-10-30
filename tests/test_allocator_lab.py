"""Allocator footprint: volume, tickets, and the one-slot choice."""

from __future__ import annotations

import pytest

from bot.research.allocator_lab.profile import (
    Print,
    cell_of,
    choose,
    concentration_ratio,
    day_of,
    leader_index,
    magnet_passes,
    passes,
    percentile_rank,
    scan,
    ticket_labels,
)
from bot.research.allocator_lab.sim import replay

DAY = 86_400_000
T0 = 1_704_067_200_000  # 2024-01-01 UTC
VOL = 1_000_000.0


def _bar(i: int, close: float, volume: float = 1_000.0, open_: float | None = None) -> list[float]:
    px = close if open_ is None else open_
    return [T0 + i * DAY, px, max(px, close), min(px, close), close, volume]


def _print(**overrides: object) -> Print:
    row = Print(
        base="AAA",
        day="2024-06-01",
        ret20=0.20,
        day_ret=0.05,
        volx=3.0,
        qvol=1_000_000.0,
        btc_up=True,
        on_stride=False,
        fwd_close=0.01,
        fwd_high=0.02,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_cell_boundaries() -> None:
    assert cell_of(0.10, 0.01, 2.0) == "winner_up_heavy"
    assert cell_of(-0.10, -0.01, 0.99) == "loser_down_light"
    assert cell_of(0.0, 0.01, 1.5) is None
    assert cell_of(0.20, 0.0, 3.0) is None
    assert cell_of(0.05, 0.02, 0.4) == "flat_up_light"


def test_ticket_rank_and_labels() -> None:
    prior = [100.0] * 60
    assert percentile_rank(prior, 101.0) == 1.0
    assert percentile_rank(prior, 100.0) == 0.0
    assert ticket_labels(0.80, 0.52, 0.01, 0.10) == ["large_buy", "large_buy_winner"]
    assert ticket_labels(0.20, 0.48, -0.01, -0.10) == ["small_sell", "small_sell_loser"]
    assert ticket_labels(0.50, 0.60, 0.01, 0.20) == []
    assert ticket_labels(0.90, 0.52, -0.01, 0.20) == []


def test_leaders_are_a_fifth_and_concentration_uses_their_euros() -> None:
    values = [float(i) for i in range(10)]
    leaders = leader_index(values)
    assert leaders == [9, 8]
    qvols = [1.0] * 10
    qvols[9] = 50.0
    qvols[8] = 50.0
    assert concentration_ratio(qvols, leaders) == (100.0 / 108.0) / 0.2


def test_screen_requires_both_the_sign_and_a_real_sample() -> None:
    heavy = {"n": 40, "median": 0.03, "euro": 0.02}
    light = {"n": 40, "median": 0.01, "euro": 0.01}
    assert passes(heavy, light, "up")
    assert not passes({"n": 10, "median": 0.03, "euro": 0.02}, light, "up")
    assert not passes({"n": 40, "median": -0.01, "euro": -0.02}, light, "up")
    sold = {"n": 40, "median": -0.04, "euro": -0.03}
    held = {"n": 40, "median": -0.01, "euro": -0.01}
    assert passes(sold, held, "down")
    assert not passes(held, sold, "down")
    assert magnet_passes({"n": 40, "median": 0.02, "euro": 0.01, "high": 0.02})
    assert not magnet_passes({"n": 40, "median": 0.02, "euro": 0.01, "high": 0.005})


def test_scan_keeps_today_out_of_the_volume_baseline_and_buys_the_next_open() -> None:
    rows = [_bar(i, 100.0, 1_000.0) for i in range(61)]
    rows[50] = _bar(50, 120.0, 10_000.0)
    rows[51] = _bar(51, 100.0, 1_000.0, open_=110.0)
    rows[60] = _bar(60, 132.0, 1_000.0)
    rows[55][2] = 150.0
    tape = {"BTC": [_bar(i, 100.0, 1_000.0) for i in range(61)], "AAA": rows}
    prints, _by_day = scan(tape)
    hit = next(row for row in prints if row.base == "AAA" and row.day == day_of(T0 + 50 * DAY))
    assert hit.ret20 == pytest.approx(0.20)
    assert hit.day_ret == pytest.approx(0.20)
    assert hit.volx == 12.0
    assert hit.fwd_close == pytest.approx(132.0 / 110.0 - 1.0)
    assert hit.fwd_high == pytest.approx(150.0 / 110.0 - 1.0)
    assert hit.btc_up is False
    assert hit.on_stride is True


def test_size_follows_euros_and_price_follows_the_slow_return() -> None:
    rows = [
        _print(base="AAA", ret20=0.30, volx=1.2, qvol=100_000.0, btc_up=True),
        _print(base="BBB", ret20=0.12, volx=3.0, qvol=5_000_000.0, btc_up=False),
        _print(base="CCC", ret20=-0.20, day_ret=-0.05, volx=4.0, qvol=9_000_000.0),
    ]
    assert choose(rows, "price") == "AAA"
    assert choose(rows, "size") == "BBB"
    assert choose(rows, "price_btc") == "AAA"
    assert choose(rows, "size_btc") is None


def _path(path: list[tuple[float, float, float, float]]) -> dict[str, list[list[float]]]:
    rows = [
        [T0 + i * DAY, open_, high, low, close, VOL]
        for i, (open_, high, low, close) in enumerate(path)
    ]
    return {"BTC": rows, "AAA": rows}


def test_trail_sells_inside_the_bar_and_a_gap_sells_the_open() -> None:
    path = [(100, 100, 100, 100), (100, 130, 100, 120), (125, 128, 110, 112)]
    row = replay(
        _path(path),
        {"2024-01-01": "AAA"},
        name="size",
        start="2024-01-01",
        end="2024-01-03",
    )
    assert row["n_trades"] == 1
    assert row["reasons"]["trail"] == 1
    assert row["pnl_eur"] > 0

    gap = [(100, 100, 100, 100), (100, 130, 100, 120), (100, 101, 90, 95)]
    gapped = replay(
        _path(gap),
        {"2024-01-01": "AAA"},
        name="size",
        start="2024-01-01",
        end="2024-01-03",
    )
    assert gapped["reasons"]["trail"] == 1
    assert gapped["pnl_eur"] < 0


def test_hold_sells_the_open_after_the_clock() -> None:
    path = [(100, 101, 99, 100)] * 12
    row = replay(
        _path(path),
        {"2024-01-01": "AAA"},
        name="price",
        start="2024-01-01",
        end="2024-01-12",
        hold_bars=7,
        trail_pct=0.0,
    )
    assert row["n_trades"] == 1
    assert row["reasons"]["time"] == 1
    assert row["pnl_eur"] < 0

"""Depth times signed quantity: the rise that follows."""

from __future__ import annotations

import pytest

from bot.research.depth_flow_lab.profile import (
    Event,
    choose,
    day_of,
    flow_of,
    location_of,
    quantity_multiple,
    scan,
    shape_holds,
    spearman,
)
from bot.research.depth_flow_lab.sim import replay

DAY = 86_400_000
T0 = 1_704_067_200_000  # 2024-01-01 UTC
VOL = 1_000_000.0


def test_location_and_flow_boundaries() -> None:
    assert location_of(0.09) == "near"
    assert location_of(0.10) == "mid"
    assert location_of(0.30) == "deep"
    assert quantity_multiple(100.0, [float(i) for i in range(1, 21)]) == pytest.approx(100 / 10.5)
    assert flow_of(0.52, 2.0, None) == "heavy_buy"
    assert flow_of(0.52, 0.5, None) == "light_buy"
    assert flow_of(0.52, 1.4, None) is None
    assert flow_of(0.48, None, 2.0) == "heavy_sell"
    assert flow_of(0.50, 3.0, 3.0) == "flat"


def test_spearman_of_a_perfect_reversal_is_minus_one() -> None:
    assert spearman([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_shape_requires_a_deeper_high_and_a_real_sample() -> None:
    def cell(high: float, n: float = 40) -> dict[str, float]:
        return {"n": n, "high": high, "close": 0.01}

    summary = {}
    for location, high in (("near", 0.05), ("mid", 0.08), ("deep", 0.12)):
        block = {"pre": cell(high), "2026": cell(high)}
        summary[(location, "heavy_buy")] = block
    summary[("deep", "heavy_sell")] = {"pre": cell(0.05), "2026": cell(0.05)}
    assert shape_holds(summary)
    summary[("deep", "heavy_buy")] = {"pre": cell(0.06), "2026": cell(0.12)}
    assert not shape_holds(summary)


def _bar(
    i: int,
    close: float,
    *,
    high: float | None = None,
    open_: float | None = None,
) -> list[float]:
    px = close if open_ is None else open_
    hi = close if high is None else high
    return [T0 + i * DAY, px, max(px, hi), min(px, close), close, 1_000.0]


def _flow(i: int, quote: float, taker: float = 1.0) -> list[float]:
    return [T0 + i * DAY, 10.0, quote, 10.0, 10.0 * taker]


def test_scan_keeps_today_out_of_the_buy_baseline_and_buys_the_next_open() -> None:
    rows = [_bar(i, 100.0) for i in range(81)]
    rows[70] = _bar(70, 60.0, high=100.0)
    for i in range(71, 81):
        rows[i] = _bar(i, 60.0)
    rows[75] = _bar(75, 60.0, high=90.0)
    rows[80] = _bar(80, 72.0)
    flow = [_flow(i, float((i - 49) if 50 <= i <= 69 else 1)) for i in range(81)]
    flow[70] = _flow(70, 100.0)
    events = scan({"AAA": rows}, {"AAA": flow})
    hit = next(row for row in events if row.day == day_of(T0 + 70 * DAY))
    assert hit.location == "deep"
    assert hit.depth == pytest.approx(0.40)
    assert hit.flow == "heavy_buy"
    assert hit.buy_multiple == pytest.approx(100 / 10.5)
    assert hit.fwd_high == pytest.approx(0.50)
    assert hit.fwd_close == pytest.approx(72.0 / 60.0 - 1.0)


def _event(**overrides: object) -> Event:
    row = Event(
        base="AAA",
        day="2024-06-01",
        depth=0.40,
        location="deep",
        flow="heavy_buy",
        buy_multiple=3.0,
        ticket=None,
        fwd_high=0.10,
        fwd_close=0.01,
        slow_high=None,
        slow_close=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_deep_buy_follows_the_buy_multiple_among_lows() -> None:
    rows = [
        _event(base="AAA", buy_multiple=5.0, depth=0.35),
        _event(base="BBB", buy_multiple=2.0, depth=0.80),
        _event(base="CCC", location="near", depth=0.05, buy_multiple=9.0),
        _event(base="DDD", flow="heavy_sell", buy_multiple=8.0, depth=0.90),
    ]
    assert choose(rows, "deep_buy") == "AAA"
    assert choose(rows, "deep") == "DDD"
    assert choose(rows, "buy") == "CCC"


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
        name="deep_buy",
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
        name="deep_buy",
        start="2024-01-01",
        end="2024-01-03",
    )
    assert gapped["reasons"]["trail"] == 1
    assert gapped["pnl_eur"] < 0

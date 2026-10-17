"""Velocity scan: section membership ignores the burst day, fill is the next minute."""

from __future__ import annotations

from bot.research.volume_velocity.engine import run_book, scan_day, side_cost, trade_return
from bot.research.volume_velocity.membership import member_days

DAY = 86_400_000
MIN = 60_000


def _day(i: int, qvol: float, close: float = 1.0) -> list[float]:
    return [i * DAY, close, close, close, close, qvol / close]


def test_membership_uses_prior_days_only():
    # A €5M day after 30 quiet days does not qualify: the median is the prior
    # days, and the burst itself is not allowed to enter the section.
    quiet = [_day(i, 50_000) for i in range(30)]
    quiet.append(_day(30, 5_000_000))
    burst = member_days(
        {"X": quiet}, start_ms=0, end_ms=40 * DAY, lo=200_000, hi=300_000, window=30
    )
    assert all(r["day_ms"] != 30 * DAY for r in burst)
    steady = [_day(i, 250_000) for i in range(31)]
    inside = member_days(
        {"Y": steady}, start_ms=0, end_ms=40 * DAY, lo=200_000, hi=300_000, window=30
    )
    row = next(r for r in inside if r["day_ms"] == 30 * DAY)
    assert row["baseline_eur"] == 250_000


def test_burst_fills_the_next_minute_not_the_signal_bar():
    # Baseline €144k/day => €100 per minute. One €5k minute is 50x.
    base = 144_000.0
    bars = []
    for i in range(10):
        px = 1.0
        vol = 100.0
        bars.append([i * MIN, px, px, px, px, vol])
    bars[4] = [4 * MIN, 0.99, 1.05, 0.99, 1.0, 5_000.0]  # up burst, €5k quote
    sigs = scan_day(bars, base="X", day_ms=0, baseline_eur=base, mult=20)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.signal_ms == 4 * MIN
    assert sig.entry_ms == 5 * MIN
    assert sig.entry_open == 1.0
    assert sig.up is True
    assert sig.velocity == 50.0


def test_down_burst_is_flagged_and_costs_scale_with_size():
    base = 144_000.0
    bars = [[i * MIN, 1.0, 1.0, 1.0, 1.0, 100.0] for i in range(6)]
    bars[2] = [2 * MIN, 1.0, 1.0, 0.9, 0.92, 4_000.0]
    sigs = scan_day(bars, base="X", day_ms=0, baseline_eur=base, mult=20)
    assert len(sigs) == 1 and sigs[0].up is False
    assert side_cost(2_000, 250_000) == 0.0025 + 0.02 * 2_000 / 250_000
    assert side_cost(20_000, 250_000) > side_cost(2_000, 250_000)


def test_hard_stop_gaps_and_book_skips_a_full_slot():
    bars = [[i * MIN, 1.0, 1.0, 1.0, 1.0, 100.0] for i in range(8)]
    bars[1] = [1 * MIN, 1.0, 1.02, 1.0, 1.01, 5_000.0]
    bars[3] = [3 * MIN, 0.85, 0.86, 0.80, 0.84, 100.0]  # gap through an 8% stop
    sigs = scan_day(bars, base="X", day_ms=0, baseline_eur=144_000, mult=20, cooldown_ms=MIN)
    assert sigs[0].entry_ms == 2 * MIN
    row = trade_return(sigs[0], notional=2_000, hold_ms=60 * MIN, hard_stop=0.08)
    assert row is not None
    assert row["reason"] == "gap_stop"
    assert row["net"] < 0
    # Two names want in at the same minute and only one slot is free.
    other = scan_day(bars, base="Y", day_ms=0, baseline_eur=144_000, mult=20, cooldown_ms=MIN)
    booked = run_book(
        sigs + other,
        book_eur=2_000,
        slot_eur=2_000,
        max_positions=1,
        hold_ms=60 * MIN,
        hard_stop=0.08,
    )
    assert booked["n"] == 1

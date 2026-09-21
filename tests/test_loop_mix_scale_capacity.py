"""Fill/capacity math for the loop-mix scale replay."""

from __future__ import annotations

from artifacts.loop_mix_scale_capacity import (
    bitvavo_taker_fee,
    capped_fill,
    exec_cost,
    half_spread,
    impact_frac,
)


def test_bitvavo_taker_tiers():
    assert bitvavo_taker_fee(0) == 0.0025
    assert bitvavo_taker_fee(99_999) == 0.0025
    assert bitvavo_taker_fee(100_000) == 0.0020
    assert bitvavo_taker_fee(250_000) == 0.0018
    assert bitvavo_taker_fee(10_000_000) == 0.0008


def test_capped_fill_is_12pct_of_adv():
    assert capped_fill(10_000, 50_000, max_part=0.12) == 6_000
    assert capped_fill(1_000, 50_000, max_part=0.12) == 1_000
    assert capped_fill(0, 50_000) == 0


def test_impact_rises_with_clip_and_falls_with_adv():
    sigma = 0.04
    small = impact_frac(4_000, 800_000, sigma)
    large = impact_frac(40_000, 800_000, sigma)
    thin = impact_frac(40_000, 80_000, sigma)
    assert large > small
    assert thin > large
    assert small < 0.01  # ~€4k on NEAR-like ADV is sub-1%


def test_half_spread_widens_on_thin_books():
    liquid = half_spread(1_000_000)
    thin = half_spread(50_000)
    assert liquid == 0.002  # live taker-cross floor
    assert thin > liquid


def test_exec_cost_friday_and_perp_are_not_free():
    spot = exec_cost(8_000, 200_000, 0.05, 0.0025, friday=False, venue="spot")
    fri = exec_cost(8_000, 200_000, 0.05, 0.0025, friday=True, venue="spot")
    perp = exec_cost(8_000, 200_000, 0.05, 0.0005, friday=False, venue="perp")
    assert fri["total"] > spot["total"]
    assert perp["depth"] == 200_000 * 8
    assert perp["part"] < spot["part"]
    assert spot["total"] > spot["fee"]


def test_original_unit_mix_replays_published_28k():
    from pathlib import Path

    from artifacts.loop_mix_scale_capacity import load_aligned, simulate_unit_mix
    from artifacts.multi_strat_20k_allocator import _idx

    cache = Path("artifacts/bear_sim_candle_cache/BTC-EUR-1d.json")
    if not cache.exists():
        return
    data = load_aligned(days=430)
    ts = data["ts"]
    i0 = _idx(ts, "2025-09-20")
    i1 = _idx(ts, "2026-09-19")
    st = simulate_unit_mix(data, book=20_000.0, i0=i0, i1=i1, mode="original")
    assert 27_000 < st["pnl_eur"] < 29_000
    assert abs(st["max_dd_pct"] - -6.5) < 0.2

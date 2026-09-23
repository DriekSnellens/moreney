"""Dry vs wet residual-weekly / clip fill math — synthetic 1d bars, no network."""

from __future__ import annotations

from bot.live.momentum_btc_rs_clip import ClipConfig
from bot.research.residual_wet.engine import (
    DRY,
    IMPACT_CAP,
    WET,
    extra_slip,
    fill_px,
    pick_residual,
    run_clip,
    run_residual,
)


def _bars(
    n: int,
    start: float,
    step: float,
    *,
    vol: float = 20_000.0,
    t0: int = 1_700_000_000_000,
    open_gap: float = 0.0,
) -> list[list[float]]:
    rows: list[list[float]] = []
    px = start
    day = 86_400_000
    for i in range(n):
        o = px * (1.0 + open_gap)
        rows.append([t0 + i * day, o, max(o, px) + 1, min(o, px) - 1, px, vol])
        px += step
    return rows


def _dates(rows: list[list[float]]) -> tuple[str, str]:
    from datetime import UTC, datetime

    a = datetime.fromtimestamp(rows[0][0] / 1000, UTC).strftime("%Y-%m-%d")
    b = datetime.fromtimestamp(rows[-1][0] / 1000, UTC).strftime("%Y-%m-%d")
    return a, b


def test_extra_slip_scales_with_sqrt_adv_and_caps() -> None:
    mid = extra_slip(20_000.0, 80_000.0, WET)
    assert abs(mid - 0.01) < 1e-9
    assert extra_slip(20_000.0, 1.0, WET) == IMPACT_CAP
    assert extra_slip(20_000.0, 80_000.0, DRY) == 0.0


def test_dry_fill_uses_close_plus_10bps() -> None:
    assert fill_px(100.0, "buy", notional=1_000.0, adv=1e9, model=DRY) == 100.1
    assert fill_px(100.0, "sell", notional=1_000.0, adv=1e9, model=DRY) == 99.9


def test_wet_fill_adds_impact_not_base_slip() -> None:
    buy = fill_px(100.0, "buy", notional=20_000.0, adv=80_000.0, model=WET)
    sell = fill_px(100.0, "sell", notional=20_000.0, adv=80_000.0, model=WET)
    assert abs(buy - 101.0) < 1e-9
    assert abs(sell - 99.0) < 1e-9


def test_residual_picks_top_positive_excess_not_thin() -> None:
    btc = _bars(40, 100.0, 0.0)
    eth = _bars(40, 10.0, 0.0)
    for i in range(20, 40):
        eth[i][4] = 10.0 + (i - 19) * 0.8
        eth[i][1] = eth[i][4]
    thin = _bars(40, 5.0, 0.5, vol=0.01)
    ohlc = {"BTC": btc, "ETH": eth, "SOL": thin}
    start, end = _dates(btc)
    pick = pick_residual(ohlc, end, universe=("ETH", "SOL"), min_qvol_eur=80_000.0)
    assert pick["want"] == "ETH"
    assert all(r["base"] != "SOL" for r in pick["ranked"])


def test_residual_falls_back_to_btc_when_excess_not_positive() -> None:
    btc = _bars(40, 100.0, 1.0)
    eth = _bars(40, 10.0, 0.0)
    ohlc = {"BTC": btc, "ETH": eth}
    _, end = _dates(btc)
    pick = pick_residual(ohlc, end, universe=("ETH",), min_qvol_eur=1.0)
    assert pick["want"] == "BTC"


def test_residual_rotates_only_on_name_change() -> None:
    btc = _bars(80, 100.0, 0.0)
    eth = _bars(80, 10.0, 0.0)
    sol = _bars(80, 20.0, 0.0)
    for i in range(25, 50):
        eth[i][4] = 10.0 + (i - 24) * 0.5
        eth[i][1] = eth[i][4]
    for i in range(50, 80):
        sol[i][4] = 20.0 + (i - 49) * 1.2
        sol[i][1] = sol[i][4]
        eth[i][4] = eth[49][4]
        eth[i][1] = eth[i][4]
    ohlc = {"BTC": btc, "ETH": eth, "SOL": sol}
    start, end = _dates(btc)
    dry = run_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=10_000.0,
        model=DRY,
        universe=("ETH", "SOL"),
        min_qvol_eur=1_000.0,
    )
    names = [p["to"] for p in dry["picks"]]
    assert "ETH" in names
    assert names[-1] == "SOL"
    assert dry["n_rotates"] >= 2
    assert dry["n_trades"] == dry["n_rotates"] * 2 - 1  # first buy has no prior sell


def test_wet_residual_fills_next_open_and_pays_taker() -> None:
    btc = _bars(40, 100.0, 0.0, open_gap=0.02)
    eth = _bars(40, 10.0, 0.2, open_gap=0.02)
    ohlc = {"BTC": btc, "ETH": eth}
    start, end = _dates(btc)
    dry = run_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=10_000.0,
        model=DRY,
        universe=("ETH",),
        min_qvol_eur=1.0,
    )
    wet = run_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=10_000.0,
        model=WET,
        universe=("ETH",),
        min_qvol_eur=1.0,
    )
    assert wet["end_eur"] < dry["end_eur"]
    assert wet["n_trades"] >= 1
    buy = next(t for t in wet["trades_tail"] if t["side"] == "buy")
    # Next-open is 2% above that day's close; dry would have bought the close.
    assert buy["date"] > start


def test_clip_wet_flatten_uses_next_session() -> None:
    btc = _bars(70, 100.0, 2.0)
    for i in range(60, 70):
        btc[i][4] = 80.0
        btc[i][1] = 82.0
    eth = _bars(70, 10.0, 0.0, vol=20_000.0)
    ohlc = {"BTC": btc, "ETH": eth}
    start, end = _dates(btc)
    cfg = ClipConfig(universe=("ETH",), min_qvol_eur=1.0, sma_n=50)
    wet = run_clip(ohlc, start=start, end=end, book_eur=20_000.0, model=WET, cfg=cfg)
    dry = run_clip(ohlc, start=start, end=end, book_eur=20_000.0, model=DRY, cfg=cfg)
    assert wet["n_flatten"] >= 1
    assert dry["n_flatten"] >= 1
    assert wet["end_hold"] == "cash" or "BTC" not in wet["end_hold"]


def test_liq_top_n_keeps_highest_quote_volume() -> None:
    btc = _bars(40, 100.0, 0.0)
    liquid = _bars(40, 10.0, 0.2, vol=50_000.0)
    jumpy = _bars(40, 5.0, 0.8, vol=5_000.0)
    ohlc = {"BTC": btc, "ETH": liquid, "SOL": jumpy}
    _, end = _dates(btc)
    wide = pick_residual(ohlc, end, universe=("ETH", "SOL"), min_qvol_eur=1.0)
    liq = pick_residual(ohlc, end, universe=("ETH", "SOL"), min_qvol_eur=1.0, liq_top_n=1)
    assert wide["want"] == "SOL"
    assert liq["want"] == "ETH"


def test_rotate_gap_skips_small_excess_upgrades() -> None:
    btc = _bars(80, 100.0, 0.0)
    eth = _bars(80, 10.0, 0.0)
    sol = _bars(80, 20.0, 0.0)
    for i in range(25, 55):
        eth[i][4] = 10.0 + (i - 24) * 0.6
        eth[i][1] = eth[i][4]
    for i in range(55, 80):
        # SOL only slightly ahead of ETH — below a 0.20 rotate gap.
        sol[i][4] = 20.0 + (i - 54) * 0.15
        sol[i][1] = sol[i][4]
        eth[i][4] = eth[54][4]
        eth[i][1] = eth[i][4]
    ohlc = {"BTC": btc, "ETH": eth, "SOL": sol}
    start, end = _dates(btc)
    kw = dict(
        start=start,
        end=end,
        book_eur=10_000.0,
        model=DRY,
        universe=("ETH", "SOL"),
        min_qvol_eur=1_000.0,
    )
    loose = run_residual(ohlc, **kw)
    sticky = run_residual(ohlc, rotate_gap=0.20, **kw)
    assert "SOL" in [p["to"] for p in loose["picks"]]
    assert sticky["picks"][-1]["to"] == "ETH"


def test_no_per_coin_branch_in_engine() -> None:
    from pathlib import Path

    src = Path("bot/research/residual_wet/engine.py").read_text()
    for needle in ('base == "', "base == '", '== "UNI"', '== "NEAR"', "if base =="):
        assert needle not in src

"""Residual overlay hooks and the factor-scoreboard feature helpers."""

from __future__ import annotations

import pytest

from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import DRY
from bot.research.clip_exit_lab.policies import ExitPolicy
from bot.research.factor_scoreboard.engine import (
    DispersionPercentile,
    funding_z,
    gate_fng_min,
    gate_tsmom,
    realized_vol,
    scale_fng_soft,
    scale_vol_target,
)
from bot.research.factor_scoreboard.external import funding_daily_mean


def _bars(n: int, start: float, step: float, vol: float = 200_000.0) -> list[list[float]]:
    rows: list[list[float]] = []
    px = start
    t0 = 1_704_067_200_000
    for i in range(n):
        hi = px + abs(step) * 1.2
        lo = max(0.01, px - abs(step) * 0.3)
        rows.append([t0 + i * 86_400_000, px, hi, lo, px, vol])
        px = max(0.01, px + step)
    return rows


def _book() -> dict[str, list[list[float]]]:
    return {"BTC": _bars(80, 100.0, 0.4), "ETH": _bars(80, 10.0, 0.4)}


_KW = dict(
    book_eur=2_500.0,
    btc_frac=0.0,
    excess_floor=0.0,
    flatten="all",
    sma_n=20,
    rebalance_days=7,
    lookback_days=10,
    skip_days=1,
    require_alt_sma=False,
    cash_when_no_alt=True,
    model=DRY,
)


def test_entry_gate_false_keeps_the_book_in_cash() -> None:
    ohlc = _book()
    open_run = run_btc_residual(ohlc, start="2024-02-20", end="2024-03-20", **_KW)
    gated = run_btc_residual(
        ohlc, start="2024-02-20", end="2024-03-20", entry_gate=lambda d, b: False, **_KW
    )
    assert open_run["n_trades"] > 0
    assert gated["n_trades"] == 0
    assert gated["end_hold"] == "cash"


def test_alt_size_scale_halves_the_first_buy() -> None:
    ohlc = _book()
    full = run_btc_residual(ohlc, start="2024-02-20", end="2024-03-20", keep_trades=True, **_KW)
    half = run_btc_residual(
        ohlc,
        start="2024-02-20",
        end="2024-03-20",
        keep_trades=True,
        alt_size_scale=lambda d, b: 0.5,
        **_KW,
    )
    first_full = next(t for t in full["trades"] if t["side"] == "buy")
    first_half = next(t for t in half["trades"] if t["side"] == "buy")
    assert first_half["eur"] == pytest.approx(first_full["eur"] * 0.5, rel=0.02)


def test_exit_cooldown_rebuys_sooner_than_the_week_clock() -> None:
    btc = _bars(90, 100.0, 0.2)
    eth = _bars(60, 10.0, 0.5) + _bars(30, 40.0, -2.0, vol=200_000.0)
    for i, row in enumerate(eth):
        row[0] = btc[i][0]
    ohlc = {"BTC": btc, "ETH": eth}
    policy = ExitPolicy(name="trail", alt_trail_pct=0.10)
    kw = {**_KW, "policy": policy, "excess_floor": -1.0}
    slow = run_btc_residual(ohlc, start="2024-02-20", end="2024-03-31", keep_trades=True, **kw)
    fast = run_btc_residual(
        ohlc, start="2024-02-20", end="2024-03-31", keep_trades=True, exit_cooldown_days=1, **kw
    )
    assert any("alt_trail" in str(t.get("reason")) for t in slow["trades"])
    assert fast["n_trades"] >= slow["n_trades"]


def test_feature_helpers_are_causal_and_bounded() -> None:
    ohlc = _book()
    rv = realized_vol(ohlc, "ETH", "2024-03-20")
    assert rv is not None and rv > 0
    assert scale_vol_target(ohlc, 10.0)("2024-03-20", "ETH") == 1.0
    assert 0 < scale_vol_target(ohlc, 0.01)("2024-03-20", "ETH") < 1.0
    assert gate_tsmom(ohlc, 20)("2024-03-20", "ETH") is True
    fng = {"2024-03-19": 10, "2024-03-20": 70}
    assert gate_fng_min(fng, 50)("2024-03-20", "ETH") is True
    assert gate_fng_min(fng, 50)("2024-03-19", "ETH") is False
    assert scale_fng_soft(fng, 20, 60)("2024-03-19", "ETH") == pytest.approx(0.5)
    assert scale_fng_soft(fng, 20, 60)("2024-03-20", "ETH") == pytest.approx(1.0)
    from datetime import date, timedelta

    days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(70)]
    series = dict.fromkeys(days, 0.0001)
    assert funding_z(series, days[-1]) is None  # zero dispersion history
    series[days[20]] = 0.0002
    z = funding_z(series, days[-1])
    assert z is not None and z < 0
    series[days[-1]] = 0.0010
    assert funding_z(series, days[-1]) > 1.0
    daily = funding_daily_mean({"ETH": [(1_704_067_200_000, 0.0001), (1_704_096_000_000, 0.0003)]})
    assert daily["ETH"]["2024-01-01"] == pytest.approx(0.0002)


def test_dispersion_percentile_needs_history() -> None:
    ohlc = {
        "BTC": _bars(80, 100.0, 0.4),
        "ETH": _bars(80, 10.0, 0.4),
        "SOL": _bars(80, 10.0, 0.1),
        "XRP": _bars(80, 10.0, -0.1),
        "ADA": _bars(80, 10.0, 0.2),
        "DOGE": _bars(80, 10.0, 0.0),
        "LINK": _bars(80, 10.0, 0.3),
    }
    dates = [f"2024-01-{d:02d}" for d in range(12, 32)] + [f"2024-02-{d:02d}" for d in range(1, 29)]
    universe = tuple(b for b in ohlc if b != "BTC")
    pct = DispersionPercentile(ohlc, dates, universe, window=30, min_hist=10)
    assert pct("2024-01-15") is None
    late = pct("2024-02-28")
    assert late is not None and 0.0 <= late <= 1.0

"""Forward-week residual entry lab."""

from __future__ import annotations

from bot.research.btc_residual_mix.engine import pick_residual
from bot.research.clip_exit_lab.engine import DRY
from bot.research.weekly_setup_lab.alphai_map import (
    build_alt_allow,
    proxy_alphai_picks,
    real_alphai_daily_picks,
)
from bot.research.weekly_setup_lab.engine import entry_variants, run_weekly_setup_grid


def _bars(n: int, start: float, step: float, vol: float = 200_000.0) -> list[list[float]]:
    out: list[list[float]] = []
    px = start
    t0 = 1_704_067_200_000
    for i in range(n):
        # Local high mid-series then pull back so ATH filters can fire.
        hi = px * 1.08 if i < n - 5 else px * 1.01
        lo = max(0.01, px * 0.97)
        out.append([t0 + i * 86_400_000, px, hi, lo, px, vol])
        px = max(0.01, px + step)
    return out


def test_pick_residual_pullback_rejects_ath() -> None:
    btc = _bars(80, 100.0, 0.1)
    # Strong RS but sitting on highs.
    eth = _bars(80, 10.0, 0.5)
    for r in eth[-5:]:
        r[2] = r[4]  # high == close → at ATH
    ohlc = {"BTC": btc, "ETH": eth}
    open_pick = pick_residual(ohlc, "2024-03-10", rank_by="excess")
    assert open_pick["want"] == "ETH"
    gated = pick_residual(
        ohlc,
        "2024-03-10",
        rank_by="setup",
        max_close_over_high=0.90,
    )
    assert "ETH" not in (gated.get("wants") or [])
    assert gated["want"] == "BTC"


def test_proxy_alphai_returns_subset() -> None:
    btc = _bars(80, 100.0, 0.05)
    eth = _bars(80, 10.0, 0.4)
    sol = _bars(80, 20.0, 0.35)
    ada = _bars(80, 5.0, -0.05)
    ohlc = {"BTC": btc, "ETH": eth, "SOL": sol, "ADA": ada}
    picks = proxy_alphai_picks(ohlc, "2024-03-10", universe=("ETH", "SOL", "ADA"), top_n=2)
    assert picks
    assert picks <= {"ETH", "SOL", "ADA"}


def test_real_alphai_daily_picks_last_session() -> None:
    sessions = [
        {
            "session_id": "2026-09-28T08:00",
            "generated_at": "2026-09-28T06:00:00+00:00",
            "picks": [{"base": "SOL", "score": 10, "rank": 1}],
        },
        {
            "session_id": "2026-09-28T18:00",
            "generated_at": "2026-09-28T16:00:00+00:00",
            "picks": [{"base": "ETH", "score": 12, "rank": 1}, {"base": "AAVE", "rank": 2}],
        },
    ]
    daily = real_alphai_daily_picks(sessions, universe=("ETH", "SOL", "ADA"))
    assert daily["2026-09-28"] == {"ETH"}


def test_build_alt_allow_off_is_none() -> None:
    ohlc = {"BTC": _bars(40, 100.0, 0.1)}
    assert build_alt_allow(ohlc, start="2024-02-01", end="2024-02-20", mode="off") is None


def test_grid_ranks_and_pairs_alphai() -> None:
    btc = _bars(90, 100.0, 0.4)
    ohlc = {
        "BTC": btc,
        "ETH": _bars(90, 10.0, 0.35),
        "SOL": _bars(90, 20.0, 0.3),
    }
    grid = run_weekly_setup_grid(
        ohlc,
        start="2024-02-20",
        end="2024-03-25",
        book_eur=20_000.0,
        window="custom",
        model=DRY,
        entries=entry_variants()[:3],
        books=[
            {
                "name": "res100_hold",
                "btc_frac": 0.0,
                "flatten": "none",
                "trail": 0.0,
                "excess_floor": 0.0,
                "lookback_days": 20,
                "require_alt_sma": False,
                "cash_when_no_alt": False,
            }
        ],
        alphai_modes=["off", "proxy_gate"],
    )
    assert grid["n_packs"] == 6
    assert grid["best"]
    assert "off" in grid["alphai_best"]
    assert "proxy_gate" in grid["alphai_best"]
    calmars = [float(r["calmar"]) for r in grid["ranked"]]
    assert calmars == sorted(calmars, reverse=True)

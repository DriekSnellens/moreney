"""Armed live clip pack maps onto the residual-weekly wet engine."""

from __future__ import annotations

from bot.live.momentum_btc_rs_clip import ClipConfig
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import DRY
from bot.research.clip_live_replay.engine import live_pack_knobs, run_live_pack


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


def test_live_pack_knobs_match_clip_config() -> None:
    cfg = ClipConfig()
    knobs = live_pack_knobs(cfg)
    assert knobs["btc_frac"] == cfg.btc_frac == 0.20
    assert knobs["excess_floor"] == cfg.excess_floor == 0.04
    assert knobs["lookback_days"] == cfg.lookback_days == 10
    assert knobs["skip_days"] == cfg.skip_days == 1
    assert knobs["rebalance_days"] == cfg.rebalance_days == 7
    assert knobs["sma_n"] == cfg.sma_n == 50
    assert knobs["flatten"] == "all"
    assert knobs["n_alts"] == 1
    assert knobs["trail_pct"] == cfg.alt_trail_pct == 0.10
    assert knobs["policy"] is not None
    assert knobs["policy"].alt_trail_pct == 0.10


def test_live_pack_replay_keeps_trades_and_weeks() -> None:
    btc = _bars(80, 100.0, 0.5)
    eth = _bars(80, 10.0, 0.4)
    ohlc = {"BTC": btc, "ETH": eth}
    row = run_live_pack(ohlc, start="2024-02-20", end="2024-03-20", book_eur=2500.0, model=DRY)
    assert row["start_eur"] == 2500.0
    assert row["n_days"] > 0
    assert row["end_eur"] > 0
    assert "weeks" in row
    assert "trades" in row
    assert row["pack"]["btc_frac"] == 0.20


def test_keep_trades_includes_full_list() -> None:
    btc = _bars(80, 100.0, 0.4)
    eth = _bars(80, 10.0, 0.05)
    row = run_btc_residual(
        {"BTC": btc, "ETH": eth},
        start="2024-02-20",
        end="2024-03-20",
        book_eur=2500.0,
        btc_frac=0.2,
        flatten="all",
        model=DRY,
        keep_trades=True,
    )
    assert "trades" in row
    assert len(row["trades"]) == row["n_trades"]

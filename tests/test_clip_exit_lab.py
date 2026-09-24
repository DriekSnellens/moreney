"""Clip exit overlay lab — synthetic 1d tape."""

from __future__ import annotations

from bot.research.clip_exit_lab.engine import DRY, run_clip_exits
from bot.research.clip_exit_lab.policies import POLICIES, ExitPolicy


def _bars(n: int, start: float, step: float, vol: float = 20_000.0) -> list[list[float]]:
    rows: list[list[float]] = []
    px = start
    t0 = 1_704_067_200_000  # 2024-01-01
    for i in range(n):
        rows.append([t0 + i * 86_400_000, px, px + abs(step), px - abs(step) * 0.4, px, vol])
        px += step
    return rows


def _pump_dump_alt() -> dict[str, list[list[float]]]:
    btc = _bars(80, 100.0, 0.4)
    eth = _bars(60, 10.0, 0.02)
    # 20d skip-1 excess vs BTC ≥ 8% then a sharp giveback.
    pump = _bars(12, eth[-1][4], 0.8)
    t0 = eth[-1][0] + 86_400_000
    for i, r in enumerate(pump):
        r[0] = t0 + i * 86_400_000
    dump = _bars(10, pump[-1][4], -1.2)
    t1 = pump[-1][0] + 86_400_000
    for i, r in enumerate(dump):
        r[0] = t1 + i * 86_400_000
    eth = eth + pump + dump
    return {"BTC": btc, "ETH": eth}


def test_policy_grid_unique_names() -> None:
    names = [p.name for p in POLICIES()]
    assert "live" in names
    assert len(names) == len(set(names))


def test_live_beats_nothing_on_up_tape() -> None:
    btc = _bars(80, 100.0, 0.5)
    ohlc = {"BTC": btc, "ETH": _bars(80, 10.0, 0.0, vol=1.0)}
    live = run_clip_exits(
        ohlc,
        ExitPolicy(name="live"),
        start="2024-02-20",
        end="2024-03-20",
        book_eur=20_000.0,
        model=DRY,
    )
    assert live["n_days"] > 0
    assert live["end_eur"] >= 19_000.0


def test_alt_trail_cuts_giveback() -> None:
    ohlc = _pump_dump_alt()
    start, end = "2024-02-15", "2024-03-22"
    live = run_clip_exits(
        ohlc,
        ExitPolicy(name="live"),
        start=start,
        end=end,
        book_eur=20_000.0,
        model=DRY,
    )
    trail = run_clip_exits(
        ohlc,
        ExitPolicy(name="alt_trail_10", alt_trail_pct=0.10),
        start=start,
        end=end,
        book_eur=20_000.0,
        model=DRY,
    )
    assert trail["n_overlay_exits"] >= 1
    assert trail["pnl_eur"] >= live["pnl_eur"] - 1.0

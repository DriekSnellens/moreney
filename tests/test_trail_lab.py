"""Tests for offline trail parameter lab."""

from __future__ import annotations

from pathlib import Path

from bot.research.trail_lab.engine import run_trail_lab
from bot.research.trail_lab.protocol import CURRENT_LIVE, iter_grid
from bot.research.trail_lab.simulator import break_even_price, simulate_bag


def test_break_even_above_cost() -> None:
    be = break_even_price(100.0, fee_rate=0.0025, sell_buffer_bps=10.0)
    assert be > 100.0


def test_soft_partial_realizes_when_above_be() -> None:
    # Climb above soft arm then flat — partial should land.
    marks = [100.0]
    for _ in range(20):
        marks.append(marks[-1] * 1.002)
    while marks[-1] < 101.2:
        marks.append(marks[-1] * 1.001)
    marks.extend([marks[-1]] * 10)
    res = simulate_bag(
        marks,
        qty=10.0,
        cost=100.0,
        soft_arm_pct=0.01,
        soft_partial_pct=0.5,
        soft_drawdown_pct=0.004,
        fee_rate=0.001,
        sell_buffer_bps=5.0,
    )
    assert res.soft_partials >= 1
    assert res.realized_eur > 0


def test_never_loss_blocks_exit_below_be() -> None:
    # Arm then dump below cost — no full exit fill.
    marks = [100.0, 101.2, 101.5, 99.0, 98.0, 97.0]
    res = simulate_bag(
        marks,
        qty=5.0,
        cost=100.0,
        soft_arm_pct=0.01,
        soft_partial_pct=0.0,
        soft_drawdown_pct=0.004,
        fee_rate=0.0025,
        sell_buffer_bps=10.0,
    )
    assert res.trail_exits == 0
    assert res.remaining_qty == 5.0


def test_grid_non_empty() -> None:
    assert len(iter_grid()) == 4 * 4 * 4
    assert "soft_arm_pct" in CURRENT_LIVE


def test_run_trail_lab_writes_report(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    report = run_trail_lab(out_path=out, n_ticks=120, base_seed=7)
    assert out.exists()
    assert report["n_configs"] == 64
    assert report["best_is"]["id"]
    assert "recommendation" in report

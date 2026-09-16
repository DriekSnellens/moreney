"""BTC hold baseline / MTM helpers."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.live.momentum_hold_baseline import ensure_baseline, mtm_snapshot


def test_ensure_baseline_writes_once(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    nl = ZoneInfo("Europe/Amsterdam")
    first = ensure_baseline(
        value_eur=4000.0,
        qty_btc=0.06,
        mark_eur=66666.0,
        by_venue={"bitvavo": 0.03, "okx": 0.03},
        path=path,
        now=datetime(2026, 9, 16, 12, 0, tzinfo=nl),
    )
    assert first["baseline_eur"] == 4000.0
    assert first["day_key"] == "2026-09-16"
    second = ensure_baseline(
        value_eur=4100.0,
        qty_btc=0.06,
        mark_eur=68000.0,
        path=path,
        now=datetime(2026, 9, 17, 12, 0, tzinfo=nl),
    )
    assert second["baseline_eur"] == 4000.0
    assert second["day_key"] == "2026-09-16"


def test_mtm_snapshot_pnl_vs_baseline(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    ensure_baseline(
        value_eur=4000.0,
        qty_btc=0.06,
        mark_eur=66666.0,
        path=path,
    )
    up = mtm_snapshot(
        value_eur=4120.0,
        qty_btc=0.06,
        mark_eur=68666.0,
        path=path,
    )
    assert up["pnl_eur"] == 120.0
    assert up["pnl_pct"] == 0.03
    down = mtm_snapshot(
        value_eur=3900.0,
        qty_btc=0.06,
        mark_eur=65000.0,
        path=path,
    )
    assert down["pnl_eur"] == -100.0

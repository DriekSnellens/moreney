"""Tests for strategy PnL gap analysis."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from bot.research.strategy_pnl_gap.analyze import analyze_gap
from bot.research.strategy_pnl_gap.loaders import load_gap_data
from bot.research.strategy_pnl_gap.runner import run_analysis


def _minimal_paper(name: str, *, cvd_pnl: str, maker: bool = False) -> dict:
    strategies = {
        "cross_venue_dislocation": {
            "strategy": "cross_venue_dislocation",
            "net_pnl": cvd_pnl,
            "trades": 10,
            "executions": 20,
            "opportunities": 1000,
            "fees": "1",
        }
    }
    if maker:
        strategies = {
            "maker_inventory": {
                "strategy": "maker_inventory",
                "net_pnl": cvd_pnl,
                "trades": 1,
                "executions": 1,
                "opportunities": 5,
                "fees": "0.1",
            }
        }
    return {
        "runtime_seconds": 3600,
        "real_orders_placed": 0,
        "portfolio": {"equity": "210", "stats": {"realized_pnl": cvd_pnl}},
        "tracker": {"realized_pnl": cvd_pnl, "strategies": strategies},
    }


def test_analyze_detects_strategy_mismatch(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "paper_200live.json").write_text(
        json.dumps(_minimal_paper("200live", cvd_pnl="50")),
        encoding="utf-8",
    )
    (data_dir / "live_micro_session_status.json").write_text(
        json.dumps({"bridge": {"realized_trade_pnl_eur": "-5", "skips": {}}}),
        encoding="utf-8",
    )
    research = data_dir / "research" / "final_validation"
    research.mkdir(parents=True)
    (research / "results.json").write_text(
        json.dumps({"CANONICAL_REPLAY_NET": "1000", "BASELINE_RESULT": {"signal_count": 100}}),
        encoding="utf-8",
    )
    loaded = load_gap_data(repo=tmp_path)
    report = analyze_gap(loaded)
    ids = {c.component_id for c in report.components}
    assert "STRATEGY_MISMATCH" in ids
    assert "cross_venue_dislocation" in report.comparison_table[2].strategy_id


def test_run_writes_report(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "paper_lab_strategy.json").write_text(
        json.dumps(_minimal_paper("lab", cvd_pnl="0.03", maker=True)),
        encoding="utf-8",
    )
    md = tmp_path / "out.md"
    json_out = tmp_path / "out.json"
    payload = run_analysis(json_out=json_out, md_out=md)
    assert md.is_file()
    assert "maker_inventory" in md.read_text(encoding="utf-8")
    assert payload["analysis"]["verdict"]

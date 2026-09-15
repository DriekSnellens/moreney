"""Tests for execution intelligence ablation replay."""

from __future__ import annotations

import json
from pathlib import Path

from bot.research.execution_intelligence_ablation import (
    compare_verdict,
    load_audit_candidates,
    run_ablation,
    AblationMetrics,
)


def test_load_audit_candidates_from_fixture(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-08-21T08:47:20.685676+00:00",
                        "type": "micro_order_result",
                        "payload": {
                            "venue": "bitvavo",
                            "symbol": "NEAREUR",
                            "side": "buy",
                            "quantity": "10",
                            "notional_eur": "100",
                        },
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-08-21T08:47:22.222674+00:00",
                        "type": "micro_order_result",
                        "payload": {
                            "venue": "bitvavo",
                            "symbol": "NEAREUR",
                            "side": "buy",
                            "quantity": "10",
                            "notional_eur": "101",
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    cands = load_audit_candidates(audit)
    assert len(cands) == 2
    assert cands[1].marks == [cands[0].price]


def test_ablation_runs_on_fixture(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    lines = []
    for i in range(12):
        lines.append(
            json.dumps(
                {
                    "ts": f"2026-08-21T08:47:{20+i}.685676+00:00",
                    "type": "micro_order_result",
                    "payload": {
                        "venue": "bitvavo",
                        "symbol": "SOLEUR",
                        "side": "buy",
                        "quantity": "1",
                        "notional_eur": str(100 + i * 0.1),
                    },
                }
            )
        )
    audit.write_text("\n".join(lines), encoding="utf-8")
    results = run_ablation(load_audit_candidates(audit))
    assert "BASELINE" in results
    assert "PHASE2_FULL" in results
    assert results["BASELINE"].candidates == 12


def test_compare_verdict_structure() -> None:
    results = {
        "BASELINE": AblationMetrics(label="BASELINE"),
        "PHASE2_FULL": AblationMetrics(label="PHASE2_FULL"),
    }
    results["BASELINE"].record(
        decision=__import__(
            "bot.strategies.opportunity_engine", fromlist=["OpportunityDecision"]
        ).OpportunityDecision.REDUCED,
        score=__import__("decimal").Decimal("70"),
        size_mult=__import__("decimal").Decimal("0.5"),
        estimated_net=__import__("decimal").Decimal("10"),
    )
    v = compare_verdict(results)
    assert "recommend_activate_execution" in v

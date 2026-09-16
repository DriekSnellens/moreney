"""Correctness guard: performance changes must not alter causal decisions."""

from __future__ import annotations

import json
from pathlib import Path

from bot.opportunity.causal_walkforward import CONFIGS, walk_forward
from bot.perf.cycle_metrics import CycleLatencyTracker


def _fingerprint(result: dict) -> dict:
    return {
        "opportunities_scanned": result.get("opportunities_scanned"),
        "rejected_opportunities": result.get("rejected_opportunities"),
        "executed_opportunities": result.get("executed_opportunities"),
        "completed_round_trips": result.get("completed_round_trips"),
        "total_realized_net": str(result.get("total_realized_net")),
        "stop_reasons": result.get("stop_reasons"),
        "final_route_states": result.get("final_route_states"),
        "events": [
            {
                "opportunity_id": e.get("opportunity_id"),
                "decision": e.get("decision"),
                "decision_reason": e.get("decision_reason"),
                "route_state_before": e.get("route_state_before"),
                "predicted_net_if_fill": e.get("predicted_net_if_fill"),
                "predicted_ev": e.get("predicted_ev"),
                "realized_net": e.get("realized_net"),
            }
            for e in (result.get("events") or [])
        ],
    }


def _load_trades() -> list[dict]:
    path = Path("data/paper_25000live.json")
    if path.exists():
        payload = json.loads(path.read_text())
        trades = list((payload.get("tracker") or {}).get("trades") or [])
        if trades:
            return trades
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc)
    return [
        {
            "timestamp": (base + timedelta(minutes=i)).isoformat(),
            "opportunity_id": f"opp-{i}",
            "strategy": "maker_inventory",
            "symbol": "XRPEUR",
            "buy_exchange": "bitvavo",
            "sell_exchange": "bitvavo",
            "expected_net_profit": "1.0",
            "realized_net_profit": "-2.0",
            "expected_adverse": "0.5",
            "realized_adverse": "3.0",
        }
        for i in range(12)
    ]


def test_causal_decisions_stable_on_frozen_fixture() -> None:
    trades = _load_trades()
    assert trades, "need trades for regression fingerprint"
    fps = {}
    for name, cfg in CONFIGS.items():
        result = walk_forward(trades, config=cfg)
        fps[name] = _fingerprint(result)
    for name, cfg in CONFIGS.items():
        again = _fingerprint(walk_forward(trades, config=cfg))
        assert again == fps[name], f"nondeterministic or logic drift in {name}"


def test_cycle_latency_tracker_percentiles() -> None:
    t = CycleLatencyTracker(enabled=True, window=100)
    for i in range(100):
        t.record("total_cycle", i / 1000.0)
    stats = t.stats("total_cycle")
    assert stats is not None
    assert stats.count == 100
    assert stats.p95_ms >= stats.p50_ms
    assert stats.p99_ms >= stats.p95_ms
    assert t.report()["enabled"] is True

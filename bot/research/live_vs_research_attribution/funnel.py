"""Opportunity funnel reconstruction."""

from __future__ import annotations

from typing import Any

from bot.research.live_vs_research_attribution.loaders import LoadedData


def build_funnel(data: LoadedData) -> dict[str, Any]:
    bridge = (data.session_status.get("bridge") or data.bridge_state) or {}
    skips = bridge.get("skips") or {}
    total_skips = sum(int(v) for v in skips.values()) if isinstance(skips, dict) else 0

    live_fills = len(data.live_fills)
    audit_submits = sum(
        1 for e in data.audit_events if e.event_type in {"order_submit", "micro_order_result"}
    )
    audit_blocked = len(data.order_blocked)
    audit_exceptions = len(data.order_exceptions)

    fv = data.final_validation or {}
    baseline = fv.get("BASELINE_RESULT") or {}

    phase21 = data.phase21 or {}
    p21_base = phase21.get("baseline") or {}

    funnel = {
        "research_canonical": {
            "signals": baseline.get("signal_count") or baseline.get("candidate_count"),
            "fills_replay": baseline.get("fill_count"),
            "canonical_replay_net_eur": fv.get("CANONICAL_REPLAY_NET")
            or baseline.get("CANONICAL_REPLAY_NET"),
            "strategy": fv.get("STRATEGY", "cross_venue_dislocation"),
        },
        "live_micro_session": {
            "mode": data.session_status.get("mode"),
            "live_fills_audit": live_fills,
            "live_fills_bridge": bridge.get("live_fill_count"),
            "skip_events_total": total_skips,
            "order_submits_audit": audit_submits,
            "order_blocked_audit": audit_blocked,
            "order_exceptions_audit": audit_exceptions,
            "realized_pnl_eur": bridge.get("realized_trade_pnl_eur")
            or bridge.get("session_start_realized_eur"),
        },
        "historical_audit_goe_replay": {
            "candidates": p21_base.get("candidates"),
            "accepted": p21_base.get("accepted"),
            "rejected": p21_base.get("rejected"),
            "reject_rate": p21_base.get("reject_rate"),
            "note": "GOE replay on historical live_audit buys — not live forward path",
        },
        "stages": [
            {"stage": "MARKET_OBSERVED", "count": None, "note": "Not logged as discrete counter"},
            {"stage": "SIGNAL_CREATED", "count": None, "note": "Maker emits not persisted to audit"},
            {
                "stage": "PROFITABILITY_EVALUATED",
                "count": None,
                "note": "INSUFFICIENT_DATA at per-opportunity level live",
            },
            {
                "stage": "GOE_EVALUATED",
                "count": p21_base.get("candidates"),
                "note": "Replay-only on submitted buys",
            },
            {"stage": "RISK_EVALUATED", "count": None, "note": "INSUFFICIENT_DATA"},
            {
                "stage": "SKIP / REJECT",
                "count": total_skips,
                "note": "Bridge skip counters (aggregate)",
            },
            {"stage": "ORDER_SUBMITTED", "count": audit_submits},
            {"stage": "FULL_FILL", "count": live_fills},
            {
                "stage": "ROUND_TRIP_REALIZED",
                "count": None,
                "note": "FIFO pairs not exported to audit",
            },
        ],
    }
    return funnel

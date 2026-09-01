"""Skip reason attribution from bridge state and audit."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.research.live_vs_research_attribution.loaders import LoadedData

_ZERO = Decimal("0")


def _skip_taxonomy_from_bridge(bridge: dict[str, Any]) -> dict[str, int]:
    skips = bridge.get("skips") or {}
    if isinstance(skips, dict):
        return {str(k): int(v) for k, v in skips.items() if v}
    return {}


def _skip_taxonomy_from_session(session: dict[str, Any]) -> dict[str, int]:
    bridge = (session.get("bridge") or {}) if isinstance(session, dict) else {}
    return _skip_taxonomy_from_bridge(bridge)


def _categorize_skip(reason: str) -> str:
    r = reason.lower()
    if "goe" in r or "opportunity" in r or "entry_quality" in r:
        return "GOE / entry quality"
    if "profit" in r or "net" in r:
        return "profitability"
    if "risk" in r or "kill" in r or "drawdown" in r:
        return "risk"
    if "stale" in r or "latency" in r:
        return "stale data / latency"
    if "liquidity" in r or "notional" in r or "clip" in r:
        return "liquidity / size"
    if "exchange" in r or "health" in r:
        return "exchange health"
    if reason in {
        "buy_quality_pause",
        "focus_base_required",
        "time_stop_below_be",
        "trail_hold_rising",
        "momentum_block",
        "corr_sector_momentum_block",
        "underwater_cross_venue_block",
    }:
        return reason
    return "other"


def analyze_skips(data: LoadedData) -> dict[str, Any]:
    skips = _skip_taxonomy_from_session(data.session_status)
    if not skips:
        skips = _skip_taxonomy_from_bridge(data.bridge_state)

    total_skips = sum(skips.values())
    if total_skips == 0:
        return {
            "total_skip_events": 0,
            "by_reason": {},
            "insufficient_data": [
                "Per-skip expected NET not logged at decision time; "
                "cannot compute economic opportunity removed per skip reason."
            ],
        }

    by_reason: dict[str, Any] = {}
    for reason, count in sorted(skips.items(), key=lambda x: -x[1]):
        pct = round(100.0 * count / total_skips, 2)
        by_reason[reason] = {
            "count": count,
            "pct_of_skip_events": pct,
            "category": _categorize_skip(reason),
            "expected_net_total_eur": None,
            "expected_net_mean_eur": None,
            "expected_net_median_eur": None,
            "unique_bases": None,
            "unique_venues": None,
            "note": (
                "Skip counter only — expected NET at skip time not persisted. "
                "INSUFFICIENT_DATA for economic weight."
            ),
        }

    # Audit-level order_blocked reasons
    blocked_counts: dict[str, int] = {}
    for row in data.order_blocked:
        reason = str(row.get("reason", "unknown"))
        blocked_counts[reason] = blocked_counts.get(reason, 0) + 1

    return {
        "total_skip_events": total_skips,
        "by_reason": by_reason,
        "top_5": sorted(by_reason.items(), key=lambda x: -x[1]["count"])[:5],
        "order_blocked_audit": blocked_counts,
        "order_exceptions_count": len(data.order_exceptions),
        "insufficient_data": [
            "Per-skip expected NET requires decision-time economics logging on bridge skips.",
            "Ex-post positive rate of skipped opportunities requires counterfactual replay.",
        ],
    }


def inventory_skip_focus(data: LoadedData) -> dict[str, Any]:
    """Focus skips related to inventory / underwater management."""
    skips = _skip_taxonomy_from_session(data.session_status) or _skip_taxonomy_from_bridge(
        data.bridge_state
    )
    focus_keys = (
        "time_stop_below_be",
        "buy_quality_pause",
        "underwater_cross_venue_block",
        "underwater_base_block",
        "underwater_add_block",
        "holding_base_buy_block",
        "sell_below_break_even",
        "trail_no_trusted_cost",
    )
    focused = {k: skips[k] for k in focus_keys if k in skips}
    bridge = (data.session_status.get("bridge") or data.bridge_state) or {}
    return {
        "inventory_related_skips": focused,
        "total_inventory_skips": sum(focused.values()),
        "locked_notional_eur": bridge.get("locked_notional_eur"),
        "micro_locked_notional_eur": bridge.get("micro_locked_notional_eur"),
        "blocked_sells_session": bridge.get("blocked_sells_session"),
        "realized_trade_pnl_eur": bridge.get("realized_trade_pnl_eur"),
        "session_lots_count": len((data.bridge_state.get("session_lots") or {})),
        "position_opened_at_count": len((data.bridge_state.get("position_opened_at") or {})),
    }

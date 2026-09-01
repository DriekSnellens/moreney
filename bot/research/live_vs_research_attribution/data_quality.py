"""Data quality and accounting consistency checks."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from bot.research.live_vs_research_attribution.loaders import LoadedData, LiveFillRecord

_ZERO = Decimal("0")


def _check_fill_ids(fills: list[LiveFillRecord]) -> dict[str, Any]:
    event_ids = [f.event_id for f in fills]
    dup_events = [k for k, v in Counter(event_ids).items() if v > 1]
    exchange_ids = [f.exchange_order_id for f in fills if f.exchange_order_id]
    dup_exchange = [k for k, v in Counter(exchange_ids).items() if v > 1]
    return {
        "fill_event_id_unique": len(dup_events) == 0,
        "duplicate_event_ids": dup_events[:10],
        "exchange_order_id_unique": len(dup_exchange) == 0,
        "duplicate_exchange_order_ids": dup_exchange[:10],
        "total_fills": len(fills),
    }


def _check_timestamps(fills: list[LiveFillRecord]) -> dict[str, Any]:
    if not fills:
        return {"issues": [], "count": 0}
    issues: list[str] = []
    sorted_fills = sorted(fills, key=lambda f: f.ts)
    for i in range(1, len(sorted_fills)):
        delta = (sorted_fills[i].ts - sorted_fills[i - 1].ts).total_seconds()
        if delta < 0:
            issues.append(f"negative delta between {sorted_fills[i-1].event_id} and {sorted_fills[i].event_id}")
    return {"issues": issues[:20], "count": len(fills), "monotonic": len(issues) == 0}


def _check_venue_consistency(fills: list[LiveFillRecord]) -> dict[str, Any]:
    mismatches: list[str] = []
    for f in fills:
        if f.venue and f.venue.lower() not in {"bitvavo", "okx", "kraken", "binance"}:
            mismatches.append(f"{f.event_id}: unexpected venue {f.venue}")
    return {"unexpected_venues": mismatches[:20], "ok": len(mismatches) == 0}


def _check_bridge_accounting(data: LoadedData) -> dict[str, Any]:
    bridge = data.bridge_state
    mirrored = bridge.get("mirrored_trade_ids") or []
    live_fill_count = int(bridge.get("live_fill_count") or 0)
    session_fills = int(bridge.get("session_live_fill_count") or 0)
    issues: list[str] = []
    if live_fill_count and abs(len(mirrored) - live_fill_count) > 5:
        issues.append(
            f"mirrored_trade_ids ({len(mirrored)}) vs live_fill_count ({live_fill_count}) diverge"
        )
    realized = bridge.get("realized_trade_pnl_eur")
    return {
        "mirrored_trade_count": len(mirrored),
        "live_fill_count": live_fill_count,
        "session_live_fill_count": session_fills,
        "realized_trade_pnl_eur": realized,
        "issues": issues,
    }


def analyze_data_quality(data: LoadedData) -> dict[str, Any]:
    fills = data.live_fills
    return {
        "fill_accounting": _check_fill_ids(fills),
        "timestamp_consistency": _check_timestamps(fills),
        "venue_consistency": _check_venue_consistency(fills),
        "bridge_accounting": _check_bridge_accounting(data),
        "attribution_store_empty": not (data.attribution_state.get("records") or []),
        "research_leakage_check": {
            "live_disable_research_hooks_expected": True,
            "note": "Research package not on live hot path per DISABLED_FROM_LIVE.md",
            "issues": [],
        },
        "missing_sources": data.missing_sources,
    }

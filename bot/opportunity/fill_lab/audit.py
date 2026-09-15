"""Historical data sufficiency audit for experimental fill models."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any


class SupportLevel(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


def audit_dataset(path: Path | str) -> dict[str, Any]:
    """Inspect paper dump (+ optional market_data dir) for fill-model prerequisites."""
    path = Path(path)
    data = json.loads(path.read_text()) if path.exists() else {}
    md_dir = Path("data/market_data")
    md_files = list(md_dir.rglob("*")) if md_dir.exists() else []
    recording_present = any(f.is_file() for f in md_files)

    orders = data.get("orders") or {}
    fills = data.get("fills") or {}
    trades = list((data.get("tracker") or {}).get("trades") or [])
    markout = data.get("markout") or {}

    n_orders = len(orders) if isinstance(orders, dict) else 0
    n_fills = len(fills) if isinstance(fills, dict) else 0
    placed_ms = 0
    fill_created = 0
    if isinstance(orders, dict):
        for o in orders.values():
            if isinstance(o, dict) and (o.get("extra") or {}).get("placed_ms"):
                placed_ms += 1
    if isinstance(fills, dict):
        for f in fills.values():
            if isinstance(f, dict) and f.get("created_at"):
                fill_created += 1

    by_horizon = markout.get("by_horizon") or {}
    horizon_ns = {str(h): len(v or []) for h, v in by_horizon.items()}

    checks = {
        "top_of_book_updates": recording_present,
        "depth_levels": recording_present,
        "trade_prints": False,  # no trade-print store in paper dump
        "timestamps_ms_precision": placed_ms > 0,
        "quote_timestamps": placed_ms > 0,
        "market_timestamps_after_quote": recording_present,
        "fill_timestamps": fill_created > 0 or len(trades) > 0,
        "markout_horizons": bool(horizon_ns),
        "fill_type_labels": n_fills > 0,
    }

    models = {
        "TRADE_THROUGH_ONLY": {
            "support": SupportLevel.SUPPORTED.value,
            "why": (
                "Baseline fills and markouts exist in paper state; "
                "production executor already implements trade-through matching."
            ),
        },
        "TOUCH_ONLY": {
            "support": SupportLevel.UNSUPPORTED.value,
            "why": (
                "No recorded top-of-book / mid path after quote placement "
                f"(data/market_data present={recording_present}). "
                "Cannot observe touch without fabricating market evolution."
            ),
        },
        "TOUCH_PERSISTENCE_100": {
            "support": SupportLevel.UNSUPPORTED.value,
            "why": "Requires continuous book/mid timestamps after quote; recording absent.",
        },
        "TOUCH_PERSISTENCE_250": {
            "support": SupportLevel.UNSUPPORTED.value,
            "why": "Requires continuous book/mid timestamps after quote; recording absent.",
        },
        "TOUCH_PERSISTENCE_500": {
            "support": SupportLevel.UNSUPPORTED.value,
            "why": "Requires continuous book/mid timestamps after quote; recording absent.",
        },
        "TOUCH_PERSISTENCE_1000": {
            "support": SupportLevel.UNSUPPORTED.value,
            "why": "Requires continuous book/mid timestamps after quote; recording absent.",
        },
        "DEPTH_CONSUMPTION": {
            "support": SupportLevel.UNSUPPORTED.value,
            "why": (
                "No historical depth or trade-print volume series; "
                "queue position cannot be estimated honestly."
            ),
        },
    }

    return {
        "source": str(path),
        "counts": {
            "orders": n_orders,
            "fills": n_fills,
            "trades": len(trades),
            "orders_with_placed_ms": placed_ms,
            "fills_with_created_at": fill_created,
            "markout_horizon_counts": horizon_ns,
            "market_data_files": sum(1 for f in md_files if f.is_file()),
        },
        "checks": checks,
        "models": models,
        "conclusion": (
            "TRADE_THROUGH_ONLY is the only supported fill model on this dataset. "
            "Touch/persistence/depth experiments require recorded book/trade history."
        ),
    }

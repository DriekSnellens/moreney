"""Phase definitions for the paper → live path."""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class LivePhase(IntEnum):
    """Ordered readiness phases. Higher = closer to production capital."""

    GO_NO_GO = 0
    OBSERVE = 1
    SCAFFOLDING = 2
    MICRO_LIVE = 3
    SCALE = 4
    HARDENING = 5


PHASE_ORDER: tuple[LivePhase, ...] = tuple(LivePhase)

PHASE_META: dict[LivePhase, dict[str, Any]] = {
    LivePhase.GO_NO_GO: {
        "name": "go_no_go",
        "title": "Phase 0 — Go/no-go checklist",
        "places_orders": False,
        "description": "Paper stability gates before any live capital path.",
    },
    LivePhase.OBSERVE: {
        "name": "observe",
        "title": "Phase 1 — Live observe (read-only)",
        "places_orders": False,
        "description": "Fetch live balances and shadow signals; never place orders.",
    },
    LivePhase.SCAFFOLDING: {
        "name": "scaffolding",
        "title": "Phase 2 — Execution scaffolding",
        "places_orders": False,
        "description": "Multi-venue registry + LiveExecutor wiring; trading still off.",
    },
    LivePhase.MICRO_LIVE: {
        "name": "micro_live",
        "title": "Phase 3 — Micro-live allowlist",
        "places_orders": True,  # only when all flags enabled
        "description": "Tiny notional, venue allowlist, strict daily loss.",
    },
    LivePhase.SCALE: {
        "name": "scale",
        "title": "Phase 4 — Scale inventory & alerts",
        "places_orders": True,
        "description": "Rebalance recommendations + venue health alerts.",
    },
    LivePhase.HARDENING: {
        "name": "hardening",
        "title": "Phase 5 — Production hardening",
        "places_orders": True,
        "description": "Audit log, kill-switch runbooks, secret hygiene.",
    },
}


def phase_public(phase: LivePhase) -> dict[str, Any]:
    meta = PHASE_META[phase]
    return {
        "phase": int(phase),
        "name": meta["name"],
        "title": meta["title"],
        "places_orders": meta["places_orders"],
        "description": meta["description"],
    }

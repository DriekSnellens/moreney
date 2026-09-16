"""Live readiness — phased path from paper to live (fail-closed).

Phases 0–5 are implemented as scaffolding. Real order placement stays disabled
unless every independent gate is explicitly enabled. Withdrawals are never
automatic. PaperRunner never uses LiveMicroEngine.
"""

from bot.live.service import LiveReadinessService, get_live_service, reset_live_service
from bot.live.micro_engine import LiveMicroEngine, get_micro_engine, reset_micro_engine
from bot.live.phases import LivePhase, PHASE_ORDER

__all__ = [
    "LiveReadinessService",
    "get_live_service",
    "reset_live_service",
    "LiveMicroEngine",
    "get_micro_engine",
    "reset_micro_engine",
    "LivePhase",
    "PHASE_ORDER",
]

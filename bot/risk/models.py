"""Risk-layer domain models (exchange-agnostic).

These types are independent of venue SDKs. The risk engine never places orders,
never uses leverage, and never modifies or hides losing trades.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from bot.core.enums import KillSwitchState, RiskRejectReason


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RiskContext(BaseModel):
    """Exchange-agnostic market / execution context for a risk evaluation.

    Populated by upstream market-data / health probes — never by importing
    exchange clients into the risk package.
    """

    exchange_healthy: bool = True
    market_data_age_ms: float | None = None
    estimated_slippage_pct: Decimal = Decimal("0")
    execution_latency_ms: float | None = None
    liquidity_base: Decimal | None = None
    reference_price: Decimal | None = None
    current_price: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskEvent(BaseModel):
    """Audit event for kill-switch transitions and hard risk blocks."""

    id: UUID = Field(default_factory=uuid4)
    event_type: str
    kill_switch_state: KillSwitchState
    reason: str
    reason_code: RiskRejectReason | str | None = None
    symbol: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class KillSwitchStatus(BaseModel):
    """API / observability view of the kill switch."""

    state: KillSwitchState
    reason: str | None = None
    activated_at: datetime | None = None
    consecutive_failures: int = 0
    allows_new_orders: bool = True
    recovery_conditions_met: bool = False


class TradeRateWindow(BaseModel):
    """Snapshot of recent trade-attempt timing (for diagnostics)."""

    trades_last_minute: int
    max_trades_per_minute: int

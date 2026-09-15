"""Paper trading statistics and opportunity tracking models.

All monetary fields use Decimal. Never fabricates values.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from bot.core.enums import OpportunityLifecycleStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


_ZERO = Decimal("0")


class TrackedOpportunity(BaseModel):
    """Every detected opportunity, including rejects and failures."""

    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=_utc_now)
    strategy: str
    symbol: str
    buy_exchange: str = ""
    sell_exchange: str = ""
    quantity: Decimal = Field(default=_ZERO, ge=0)
    gross_profit: Decimal = _ZERO
    fees: Decimal = _ZERO
    slippage: Decimal = _ZERO
    execution_buffer: Decimal = _ZERO
    expected_net_profit: Decimal = _ZERO
    expected_net_return: Decimal = _ZERO
    expected_gross: Decimal = _ZERO
    expected_adverse: Decimal = _ZERO
    expected_inventory: Decimal = _ZERO
    calibrated_expected_value: Decimal | None = None
    realized_fees: Decimal | None = None
    realized_slippage: Decimal | None = None
    realized_adverse: Decimal | None = None
    realized_inventory: Decimal | None = None
    risk_decision: str = ""
    rejection_reason: str | None = None
    execution_result: str | None = None
    status: OpportunityLifecycleStatus = OpportunityLifecycleStatus.DETECTED
    realized_net_profit: Decimal | None = None
    order_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.upper().strip()

    @property
    def exchange_pair(self) -> str:
        if not self.buy_exchange and not self.sell_exchange:
            return ""
        return f"{self.buy_exchange}->{self.sell_exchange}"


class StrategyStats(BaseModel):
    """Per-strategy performance aggregates (extensible to future strategies)."""

    strategy: str
    opportunities: int = 0
    executions: int = 0
    trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    net_pnl: Decimal = _ZERO
    fees: Decimal = _ZERO
    slippage: Decimal = _ZERO
    gross_wins: Decimal = _ZERO
    gross_losses: Decimal = _ZERO

    @property
    def win_rate(self) -> Decimal:
        if self.trades <= 0:
            return _ZERO
        return Decimal(self.winning_trades) / Decimal(self.trades)

    @property
    def profit_factor(self) -> Decimal | None:
        if self.gross_losses == 0:
            # Infinite profit factor is represented as None (no losing trades).
            return None if self.gross_wins > 0 else _ZERO
        return self.gross_wins / abs(self.gross_losses)


class ExchangePairStats(BaseModel):
    """Buy→sell exchange-pair performance."""

    buy_exchange: str
    sell_exchange: str
    opportunities: int = 0
    approved: int = 0
    executed: int = 0
    trades: int = 0
    winning_trades: int = 0
    net_pnl: Decimal = _ZERO
    fees: Decimal = _ZERO
    slippage: Decimal = _ZERO
    execution_failures: int = 0

    @property
    def pair_key(self) -> str:
        return f"{self.buy_exchange}->{self.sell_exchange}"

    @property
    def win_rate(self) -> Decimal:
        if self.trades <= 0:
            return _ZERO
        return Decimal(self.winning_trades) / Decimal(self.trades)


class HourlyStats(BaseModel):
    """Performance bucketed by hour of day (0–23)."""

    hour: int = Field(ge=0, le=23)
    opportunities: int = 0
    trades: int = 0
    winning_trades: int = 0
    net_pnl: Decimal = _ZERO

    @property
    def average_net_pnl(self) -> Decimal:
        if self.trades <= 0:
            return _ZERO
        return self.net_pnl / Decimal(self.trades)

    @property
    def win_rate(self) -> Decimal:
        if self.trades <= 0:
            return _ZERO
        return Decimal(self.winning_trades) / Decimal(self.trades)

    @property
    def label(self) -> str:
        return f"{self.hour:02d}:00"


class DailyStats(BaseModel):
    """Persisted daily rollup."""

    date: str
    starting_equity: Decimal = _ZERO
    ending_equity: Decimal = _ZERO
    gross_pnl: Decimal = _ZERO
    fees: Decimal = _ZERO
    slippage: Decimal = _ZERO
    net_pnl: Decimal = _ZERO
    return_pct: Decimal = _ZERO
    trades: int = 0
    wins: int = 0
    losses: int = 0
    maximum_drawdown: Decimal = _ZERO


class PerformanceSnapshot(BaseModel):
    """Full performance view for API / dashboard."""

    starting_equity: Decimal = _ZERO
    current_equity: Decimal = _ZERO
    realized_pnl: Decimal = _ZERO
    unrealized_pnl: Decimal = _ZERO
    paper_equity_pnl: Decimal = _ZERO
    gross_pnl: Decimal = _ZERO
    fees: Decimal = _ZERO
    slippage: Decimal = _ZERO
    net_pnl: Decimal = _ZERO
    return_pct: Decimal = _ZERO
    trade_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: Decimal = _ZERO
    average_win: Decimal = _ZERO
    average_loss: Decimal = _ZERO
    largest_win: Decimal = _ZERO
    largest_loss: Decimal = _ZERO
    profit_factor: Decimal | None = _ZERO
    current_drawdown: Decimal = _ZERO
    maximum_drawdown: Decimal = _ZERO
    trading_volume: Decimal = _ZERO
    total_opportunities: int = 0
    approved_opportunities: int = 0
    rejected_opportunities: int = 0
    executed_opportunities: int = 0
    execution_failures: int = 0
    # Strategy scan funnel (all calculated checks, not only emitted opps)
    pairs_evaluated: int = 0
    depth_edges_found: int = 0
    scan_rejections: int = 0
    net_eur_per_fill: Decimal = _ZERO
    net_bps_per_fill: Decimal = _ZERO
    ev_capture: Decimal | None = None
    fees_per_fill: Decimal = _ZERO
    slippage_per_fill: Decimal = _ZERO
    capital_velocity: Decimal = _ZERO
    rejection_opportunity_cost: Decimal = _ZERO

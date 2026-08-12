"""Core domain models shared across layers.

Strategies emit TradeOpportunity objects. Downstream layers enrich them with
profitability and risk decisions before execution. No withdrawal-related models
exist by design.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from bot.core.enums import FeeRole, OpportunitySide, OrderStatus, RiskDecisionStatus
from bot.core.exchange_types import OrderBook


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MarketSnapshot(BaseModel):
    """Normalized market state consumed by strategies (not raw exchange payloads)."""

    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume_24h: Decimal | None = None
    funding_rate: Decimal | None = None
    order_book: OrderBook | None = None
    exchange: str | None = None
    latency_ms: float | None = None
    timestamp: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @field_validator("exchange")
    @classmethod
    def _normalize_exchange(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.upper().strip()


class TradeOpportunity(BaseModel):
    """Strategy output. Strategies must not call exchange APIs; they only emit these."""

    id: UUID = Field(default_factory=uuid4)
    strategy_name: str
    symbol: str
    side: OpportunitySide
    quantity: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    expected_exit_price: Decimal | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    market: MarketSnapshot | None = None
    entry_fee_role: FeeRole = FeeRole.TAKER
    exit_fee_role: FeeRole = FeeRole.TAKER
    funding_periods: Decimal = Field(default=Decimal("1"), ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.upper().strip()


class ProfitEstimate(BaseModel):
    """Detailed NET profitability estimate. Never treat gross spread alone as tradeable."""

    gross_profit: Decimal
    buy_fee: Decimal
    sell_fee: Decimal
    slippage: Decimal
    funding_cost: Decimal
    execution_buffer: Decimal
    net_profit: Decimal
    net_return: Decimal
    trade_allowed: bool
    disallow_reasons: list[str] = Field(default_factory=list)
    assumptions: dict[str, Any] = Field(default_factory=dict)

    @property
    def total_fees(self) -> Decimal:
        return self.buy_fee + self.sell_fee


class ProfitabilityResult(BaseModel):
    """Pipeline-facing profitability result derived from a ProfitEstimate."""

    opportunity_id: UUID
    gross_profit_usd: Decimal
    buy_fee_usd: Decimal = Decimal("0")
    sell_fee_usd: Decimal = Decimal("0")
    fees_usd: Decimal
    slippage_usd: Decimal
    funding_usd: Decimal
    execution_buffer_usd: Decimal
    net_profit_usd: Decimal
    net_return: Decimal = Decimal("0")
    is_profitable: bool
    trade_allowed: bool = False
    estimate: ProfitEstimate | None = None
    assumptions: dict[str, Any] = Field(default_factory=dict)
    computed_at: datetime = Field(default_factory=_utc_now)


class RiskDecision(BaseModel):
    """Mandatory risk gate result. Unapproved trades must never reach executors.

    The risk layer never modifies or hides losing trades — it only approves or
    rejects. ``position_size_allowed`` may cap size; PnL figures are untouched.
    """

    opportunity_id: UUID
    status: RiskDecisionStatus
    reasons: list[str] = Field(default_factory=list)
    max_allowed_quantity: Decimal | None = None
    rejection_reason: str | None = None
    risk_score: Decimal = Decimal("0")
    position_size_allowed: Decimal | None = None
    maximum_loss: Decimal | None = None
    warnings: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=_utc_now)

    @property
    def approved(self) -> bool:
        return self.status == RiskDecisionStatus.APPROVED


class OrderRequest(BaseModel):
    """Normalized order intent produced after profitability + risk approval."""

    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    symbol: str
    side: OpportunitySide
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = None
    client_order_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    """Outcome of paper or live execution."""

    order_id: UUID
    opportunity_id: UUID
    status: OrderStatus
    filled_quantity: Decimal = Decimal("0")
    average_price: Decimal | None = None
    fees_usd: Decimal = Decimal("0")
    message: str = ""
    executed_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Position(BaseModel):
    """Open position snapshot (trading only — no withdrawal concepts)."""

    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    unrealized_pnl_usd: Decimal = Decimal("0")
    side: OpportunitySide


class Balance(BaseModel):
    """Account balance for a single asset (trading balances only)."""

    asset: str
    free: Decimal
    locked: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


class PortfolioSnapshot(BaseModel):
    """Portfolio state used by risk and the engine."""

    balances: list[Balance] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)
    equity_usd: Decimal = Decimal("0")
    peak_equity_usd: Decimal | None = None
    daily_realized_pnl_usd: Decimal = Decimal("0")
    open_position_count: int = 0
    as_of: datetime = Field(default_factory=_utc_now)

    @property
    def peak_equity(self) -> Decimal:
        """Peak equity for drawdown; defaults to current equity when unset."""
        if self.peak_equity_usd is not None and self.peak_equity_usd > 0:
            return self.peak_equity_usd
        return self.equity_usd if self.equity_usd > 0 else Decimal("0")

    @property
    def gross_exposure_usd(self) -> Decimal:
        total = Decimal("0")
        for position in self.positions:
            total += abs(position.quantity * position.average_entry_price)
        return total

"""ORM models for persistence (scaffolding).

No withdrawal-related tables or columns exist by design.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


class TradeOpportunityRecord(Base):
    """Persisted trade opportunity emitted by a strategy (includes rejects)."""

    __tablename__ = "trade_opportunities"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    expected_exit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    confidence: Mapped[Decimal] = mapped_column(Numeric(8, 6), default=0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    buy_exchange: Mapped[str | None] = mapped_column(String(32), index=True)
    sell_exchange: Mapped[str | None] = mapped_column(String(32), index=True)
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    fees: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    slippage: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    execution_buffer: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    expected_net_profit: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    expected_net_return: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    realized_net_profit: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    risk_decision: Mapped[str | None] = mapped_column(String(32))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    execution_result: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="detected", index=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExecutionRecord(Base):
    """Persisted execution attempt (paper or live)."""

    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    opportunity_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    average_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    fees_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RiskEventRecord(Base):
    """Persisted kill-switch / risk audit event. No withdrawal concepts."""

    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kill_switch_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(String(32))
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrderRecord(Base):
    """Persisted paper (or future live) order. No withdrawal fields."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    strategy: Mapped[str] = mapped_column(String(128), default="")
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    requested_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    fee: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    slippage: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(32), default="paper")
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    opportunity_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), index=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FillRecord(Base):
    """Persisted fill. Unique id enforces idempotency at the DB layer."""

    __tablename__ = "fills"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    fee_asset: Mapped[str] = mapped_column(String(16), default="EUR")
    slippage: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    exchange: Mapped[str] = mapped_column(String(32), default="paper")
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PortfolioSnapshotRecord(Base):
    """Persisted portfolio snapshot for paper accounting."""

    __tablename__ = "portfolio_snapshots"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    quote_asset: Mapped[str] = mapped_column(String(16), default="EUR")
    equity: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    fees_paid: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    current_drawdown: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    maximum_drawdown: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    balances: Mapped[dict] = mapped_column(JSONB, default=dict)
    positions: Mapped[dict] = mapped_column(JSONB, default=dict)
    processed_fill_ids: Mapped[list] = mapped_column(JSONB, default=list)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DailyStatsRecord(Base):
    """Persisted daily trading statistics."""

    __tablename__ = "daily_statistics"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    day: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    starting_equity: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    ending_equity: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    fees_paid: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    slippage: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    return_pct: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    number_of_trades: Mapped[int] = mapped_column(default=0)
    winning_trades: Mapped[int] = mapped_column(default=0)
    losing_trades: Mapped[int] = mapped_column(default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    total_trading_volume: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    maximum_drawdown: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StrategyStatsRecord(Base):
    """Persisted per-strategy statistics (extensible to future strategies)."""

    __tablename__ = "strategy_statistics"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    strategy: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    opportunities: Mapped[int] = mapped_column(default=0)
    executions: Mapped[int] = mapped_column(default=0)
    trades: Mapped[int] = mapped_column(default=0)
    winning_trades: Mapped[int] = mapped_column(default=0)
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    fees: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    slippage: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExchangePairStatsRecord(Base):
    """Persisted buy→sell exchange-pair statistics."""

    __tablename__ = "exchange_pair_statistics"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    buy_exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sell_exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    opportunities: Mapped[int] = mapped_column(default=0)
    approved: Mapped[int] = mapped_column(default=0)
    executed: Mapped[int] = mapped_column(default=0)
    trades: Mapped[int] = mapped_column(default=0)
    winning_trades: Mapped[int] = mapped_column(default=0)
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    fees: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    slippage: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    execution_failures: Mapped[int] = mapped_column(default=0)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HourlyStatsRecord(Base):
    """Persisted hourly-of-day statistics (0–23)."""

    __tablename__ = "hourly_statistics"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    hour: Mapped[int] = mapped_column(nullable=False, index=True)
    opportunities: Mapped[int] = mapped_column(default=0)
    trades: Mapped[int] = mapped_column(default=0)
    winning_trades: Mapped[int] = mapped_column(default=0)
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=0)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FundingEventRecord(Base):
    """Capital funding / transfer tracking (ORM scaffold).

    Withdrawal rows are records of user actions on the exchange UI only —
    Moreney never executes withdrawals from this table.
    """

    __tablename__ = "funding_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    venue: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="EUR")
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    external_reference: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)

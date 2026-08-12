"""Exchange-facing domain types shared by adapters and core contracts.

Kept in ``bot.core`` so interfaces do not depend on ``bot.exchanges``.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from bot.core.enums import OpportunitySide, OrderStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_symbol(value: str) -> str:
    return value.upper().replace("-", "").replace("_", "").replace("/", "")


class OrderBookLevel(BaseModel):
    """Single price level in an order book."""

    price: Decimal
    amount: Decimal


class OrderBook(BaseModel):
    """Normalized order book snapshot."""

    symbol: str
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utc_now)
    nonce: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)


class TradingFee(BaseModel):
    """Maker/taker trading fee for a market."""

    symbol: str
    maker: Decimal
    taker: Decimal
    percentage: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)


class ExchangeOrder(BaseModel):
    """Normalized open / historical order representation."""

    id: str
    symbol: str
    side: OpportunitySide
    status: OrderStatus
    quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    price: Decimal | None = None
    average_price: Decimal | None = None
    client_order_id: str | None = None
    fee_cost: Decimal | None = None
    fee_currency: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)


class HealthCheckResult(BaseModel):
    """Structured exchange health-check outcome."""

    exchange: str
    healthy: bool
    authenticated: bool = False
    latency_ms: float | None = None
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=_utc_now)

"""Normalized realtime market-data models.

Reuses core ``OrderBook`` / ``OrderBookLevel`` to avoid parallel type systems.
All prices and quantities use Decimal. Public market data only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from bot.core.exchange_types import OrderBook, OrderBookLevel

__all__ = [
    "ConnectionState",
    "ExchangeHealth",
    "MarketDataEvent",
    "MarketTick",
    "OrderBook",
    "OrderBookLevel",
    "OrderBookUpdate",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _norm_symbol(value: str) -> str:
    return value.upper().replace("-", "").replace("_", "").replace("/", "")


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class MarketTick(BaseModel):
    """Top-of-book / ticker snapshot from a public feed."""

    exchange: str
    symbol: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    last: Decimal | None = None
    timestamp: datetime = Field(default_factory=_utc_now)
    received_at: datetime = Field(default_factory=_utc_now)
    sequence: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("exchange")
    @classmethod
    def _norm_exchange(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("symbol")
    @classmethod
    def _norm_sym(cls, value: str) -> str:
        return _norm_symbol(value)

    @property
    def age_ms(self) -> float:
        ts = self.timestamp if self.timestamp.tzinfo else self.timestamp.replace(tzinfo=UTC)
        return max(0.0, (_utc_now() - ts).total_seconds() * 1000.0)

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")


class OrderBookUpdate(BaseModel):
    """Incremental order-book delta or full snapshot payload."""

    exchange: str
    symbol: str
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    is_snapshot: bool = False
    timestamp: datetime = Field(default_factory=_utc_now)
    received_at: datetime = Field(default_factory=_utc_now)
    sequence: int | None = None
    prev_sequence: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("exchange")
    @classmethod
    def _norm_exchange(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("symbol")
    @classmethod
    def _norm_sym(cls, value: str) -> str:
        return _norm_symbol(value)


class MarketDataEvent(BaseModel):
    """Envelope for any normalized market-data message."""

    id: UUID = Field(default_factory=uuid4)
    exchange: str
    symbol: str
    event_type: Literal["tick", "book_snapshot", "book_update", "heartbeat", "error"]
    timestamp: datetime = Field(default_factory=_utc_now)
    received_at: datetime = Field(default_factory=_utc_now)
    sequence: int | None = None
    tick: MarketTick | None = None
    book_update: OrderBookUpdate | None = None
    message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("exchange")
    @classmethod
    def _norm_exchange(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("symbol")
    @classmethod
    def _norm_sym(cls, value: str) -> str:
        return _norm_symbol(value) if value else value


class ExchangeHealth(BaseModel):
    """Per-exchange feed health for API / risk freshness checks."""

    exchange: str
    connected: bool = False
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    stale: bool = True
    synchronized: bool = False
    last_message_age_ms: float | None = None
    last_message_at: datetime | None = None
    last_snapshot_at: datetime | None = None
    last_sequence: int | None = None
    message_rate_per_sec: float = 0.0
    reconnect_count: int = 0
    sequence_gap_count: int = 0
    symbols: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("exchange")
    @classmethod
    def _norm_exchange(cls, value: str) -> str:
        return value.strip().lower()

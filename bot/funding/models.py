"""Domain models for central funding & multi-venue portfolio views."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FundingEventType(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"  # tracking/recording only — never auto-executed
    INTERNAL_TRANSFER = "internal_transfer"
    REBALANCE = "rebalance"


class FundingEventStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FundingEvent(BaseModel):
    """Capital movement record (deposit / tracked withdrawal / planned transfer)."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: FundingEventType
    venue: str
    asset: str
    amount: Decimal
    currency: str = "EUR"
    status: FundingEventStatus = FundingEventStatus.COMPLETED
    external_reference: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_store(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_store(cls, raw: dict[str, Any]) -> FundingEvent:
        return cls.model_validate(raw)


class VenueAssetBalance(BaseModel):
    """Single asset on one venue — actual exchange (or paper) balance."""

    venue: str
    asset: str
    available: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")
    reserved: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    value_eur: Decimal | None = None
    source: str = "paper"  # paper | live | unavailable


class VenueBalanceSnapshot(BaseModel):
    venue: str
    balances: list[VenueAssetBalance] = Field(default_factory=list)
    total_value_eur: Decimal = Decimal("0")
    online: bool = True
    error: str | None = None
    source: str = "paper"
    as_of: datetime = Field(default_factory=_utc_now)


class RebalanceRecommendation(BaseModel):
    """Suggested transfer — never executed by this module."""

    from_venue: str
    to_venue: str
    asset: str
    amount: Decimal
    reason: str
    current_from: Decimal
    current_to: Decimal
    target_to: Decimal
    estimated_fee: Decimal = Decimal("0")
    status: str = "pending_manual"  # never auto


class PortfolioSummary(BaseModel):
    """Aggregated multi-venue portfolio for dashboard / API."""

    mode: str  # paper | live
    main_funding_venue: str
    quote_asset: str = "EUR"
    total_deposited: Decimal = Decimal("0")
    total_withdrawn: Decimal = Decimal("0")
    current_portfolio: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    available_capital: Decimal = Decimal("0")
    reserved_capital: Decimal = Decimal("0")
    pending_transfers: int = 0
    venues: list[VenueBalanceSnapshot] = Field(default_factory=list)
    withdrawals_supported: bool = False
    automatic_withdrawals_enabled: bool = False
    note: str = (
        "Exchanges hold your assets. Moreney monitors and orchestrates; "
        "withdraw via the exchange UI."
    )
    as_of: datetime = Field(default_factory=_utc_now)

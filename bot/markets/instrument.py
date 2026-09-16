"""Normalized instrument definitions for cross-market opportunity comparison."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from bot.core.enums import AssetClass


class NormalizedInstrument(BaseModel):
    """Comparable instrument metadata across asset classes."""

    instrument_id: str
    symbol: str
    asset_class: AssetClass
    base_asset: str = ""
    quote_asset: str = "EUR"
    venue: str = ""
    liquidity_tier: int = Field(default=2, ge=1, le=3)
    correlation_group: str = "general"
    scan_interval_ms: float = 1000.0
    min_notional: Decimal = Decimal("1")
    transferable: bool = True
    session_aware: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def is_crypto(self) -> bool:
        return self.asset_class in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}

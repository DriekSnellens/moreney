"""Paper portfolio and execution domain models.

Monetary fields use Decimal. No withdrawal concepts. No leverage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from bot.core.enums import OrderSide, OrderStatus, OrderType


_ZERO = Decimal("0")
_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "EUR", "GBP")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _infer_quote_suffix(symbol: str, default: str = "EUR") -> str:
    text = symbol.upper().replace("/", "").replace("-", "")
    for suffix in _QUOTE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return suffix
    return default.upper()


def _infer_base_symbol(symbol: str, quote: str = "EUR") -> str:
    text = symbol.upper().replace("/", "").replace("-", "")
    q = _infer_quote_suffix(text, quote)
    if text.endswith(q) and len(text) > len(q):
        return text[: -len(q)]
    return text


class Fill(BaseModel):
    """A single simulated (or future live) fill event."""

    id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    symbol: str
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fee: Decimal = Field(default=Decimal("0"), ge=0)
    fee_asset: str = "EUR"
    slippage: Decimal = Field(default=Decimal("0"), ge=0)
    exchange: str = "paper"
    timestamp: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def gross_value(self) -> Decimal:
        return self.quantity * self.price

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.upper().strip()


class Order(BaseModel):
    """Strongly typed order lifecycle record for paper (and future live) trading."""

    id: UUID = Field(default_factory=uuid4)
    strategy: str = ""
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.LIMIT
    requested_quantity: Decimal = Field(gt=0)
    filled_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    requested_price: Decimal | None = None
    average_fill_price: Decimal | None = None
    fee: Decimal = Field(default=Decimal("0"), ge=0)
    slippage: Decimal = Field(default=Decimal("0"), ge=0)
    status: OrderStatus = OrderStatus.PENDING
    exchange: str = "paper"
    timestamp: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    rejection_reason: str | None = None
    opportunity_id: UUID | None = None
    client_order_id: str | None = None
    fills: list[Fill] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.upper().strip()

    @property
    def remaining_quantity(self) -> Decimal:
        return self.requested_quantity - self.filled_quantity

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.FAILED,
        }


class AssetBalance(BaseModel):
    """Per-asset cash balance with reserved (open-order) amount."""

    asset: str
    available: Decimal = Decimal("0")
    reserved: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return self.available + self.reserved

    @field_validator("asset")
    @classmethod
    def _normalize_asset(cls, value: str) -> str:
        return value.upper().strip()


class PositionState(BaseModel):
    """Open position with average entry for accounting."""

    symbol: str
    quantity: Decimal = Decimal("0")
    average_entry_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.upper().strip()


class PortfolioStats(BaseModel):
    """Aggregate trading statistics derived from fills only."""

    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    total_trading_volume: Decimal = Decimal("0")
    number_of_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    peak_equity: Decimal = Decimal("0")
    current_drawdown: Decimal = Decimal("0")
    maximum_drawdown: Decimal = Decimal("0")

    @property
    def win_rate(self) -> Decimal:
        if self.number_of_trades <= 0:
            return Decimal("0")
        return Decimal(self.winning_trades) / Decimal(self.number_of_trades)


class PortfolioState(BaseModel):
    """Full paper portfolio snapshot."""

    balances: dict[str, AssetBalance] = Field(default_factory=dict)
    positions: dict[str, PositionState] = Field(default_factory=dict)
    stats: PortfolioStats = Field(default_factory=PortfolioStats)
    quote_asset: str = "EUR"
    as_of: datetime = Field(default_factory=_utc_now)
    mark_prices: dict[str, Decimal] = Field(default_factory=dict)

    def cash_to_quote(self, asset: str, amount: Decimal) -> Decimal | None:
        """Convert a cash amount into the portfolio quote (EUR) via marks."""
        asset = (asset or "").upper()
        quote = (self.quote_asset or "EUR").upper()
        if amount == 0:
            return _ZERO
        if asset == quote:
            return amount
        direct = self.mark_prices.get(f"{asset}{quote}")
        if direct is not None and direct > 0:
            return amount * direct
        inverse = self.mark_prices.get(f"{quote}{asset}")
        if inverse is not None and inverse > 0:
            return amount / inverse
        if asset != "USDT" and quote != "USDT":
            usdt_px = self.mark_prices.get(f"{asset}USDT")
            if usdt_px is not None and usdt_px > 0:
                return self.cash_to_quote("USDT", amount * usdt_px)
        return None

    def asset_to_quote(self, asset: str, amount: Decimal) -> Decimal | None:
        """Value ``amount`` of ``asset`` in the portfolio quote currency."""
        converted = self.cash_to_quote(asset, amount)
        if converted is not None:
            return converted
        asset = (asset or "").upper()
        quote = (self.quote_asset or "EUR").upper()
        if amount == 0 or asset == quote:
            return converted
        for symbol, position in self.positions.items():
            if _infer_base_symbol(symbol, quote) != asset:
                continue
            mark = self.mark_prices.get(symbol, position.average_entry_price)
            if mark <= 0:
                continue
            notional = amount * mark
            pos_quote = _infer_quote_suffix(symbol, quote)
            if pos_quote == quote:
                return notional
            via_quote = self.cash_to_quote(pos_quote, notional)
            if via_quote is not None:
                return via_quote
        return None

    def cap_positions_to_balances(self) -> None:
        """Drop lots that were sold on another quote pair (phantom inventory).

        Positions are keyed by symbol (XRPEUR vs XRPUSDT). A sell on USDT used to
        leave the EUR lot untouched, so vermogen counted coins that were already
        sold. Holdings in ``balances`` are the source of truth.
        """
        quote = (self.quote_asset or "EUR").upper()
        held: dict[str, Decimal] = {
            balance.asset.upper(): balance.total for balance in self.balances.values()
        }
        by_base: dict[str, list[PositionState]] = {}
        for position in self.positions.values():
            base = _infer_base_symbol(position.symbol, quote)
            by_base.setdefault(base, []).append(position)
        for base, lots in by_base.items():
            if base == quote:
                continue
            target = held.get(base, _ZERO)
            total_qty = sum((lot.quantity for lot in lots), _ZERO)
            if total_qty <= target:
                continue
            overflow = total_qty - target
            ordered = sorted(
                lots,
                key=lambda lot: (
                    _infer_quote_suffix(lot.symbol, quote) == quote,
                    -lot.quantity,
                ),
            )
            for lot in ordered:
                if overflow <= 0:
                    break
                take = min(lot.quantity, overflow)
                lot.quantity -= take
                overflow -= take
                if lot.quantity <= 0:
                    lot.quantity = _ZERO
                    lot.average_entry_price = _ZERO

    @property
    def total_equity(self) -> Decimal:
        """Mark-to-market of cash + coins actually held, not stale symbol lots."""
        equity = _ZERO
        for balance in self.balances.values():
            if balance.total == 0:
                continue
            converted = self.asset_to_quote(balance.asset, balance.total)
            if converted is not None:
                equity += converted
        return equity


class AccountingResult(BaseModel):
    """Deterministic result of applying one fill."""

    fill_id: UUID
    order_id: UUID
    gross_trade_value: Decimal
    trading_fee: Decimal
    net_cash_movement: Decimal
    realized_pnl: Decimal = Decimal("0")
    remaining_position: Decimal
    average_entry_price: Decimal
    applied: bool = True
    duplicate: bool = False

"""Deterministic fill accounting. Fees included in realized PnL.

Portfolio is updated ONLY from fills — never from mere order submission.
"""

from __future__ import annotations

from decimal import Decimal

from bot.core.enums import OrderSide
from bot.portfolio.models import (
    AccountingResult,
    AssetBalance,
    Fill,
    Order,
    PortfolioState,
    PositionState,
)

_ZERO = Decimal("0")


class AccountingEngine:
    """Apply fills to portfolio state with idempotency via processed fill IDs."""

    def __init__(self) -> None:
        self._processed_fill_ids: set[str] = set()

    @property
    def processed_fill_ids(self) -> set[str]:
        return set(self._processed_fill_ids)

    def reset_idempotency(self) -> None:
        self._processed_fill_ids.clear()

    def load_processed_ids(self, fill_ids: set[str]) -> None:
        self._processed_fill_ids |= {str(fid) for fid in fill_ids}

    def apply_fill(
        self,
        state: PortfolioState,
        order: Order,
        fill: Fill,
        *,
        base_asset: str | None = None,
    ) -> AccountingResult:
        fill_key = str(fill.id)
        if fill_key in self._processed_fill_ids:
            position = state.positions.get(fill.symbol) or PositionState(symbol=fill.symbol)
            return AccountingResult(
                fill_id=fill.id,
                order_id=order.id,
                gross_trade_value=fill.gross_value,
                trading_fee=fill.fee,
                net_cash_movement=_ZERO,
                realized_pnl=_ZERO,
                remaining_position=position.quantity,
                average_entry_price=position.average_entry_price,
                applied=False,
                duplicate=True,
            )

        quote = state.quote_asset
        base = base_asset or _infer_base(fill.symbol, quote)
        gross = fill.gross_value
        fee = fill.fee
        realized = _ZERO

        quote_bal = state.balances.setdefault(
            quote, AssetBalance(asset=quote, available=_ZERO, reserved=_ZERO)
        )
        base_bal = state.balances.setdefault(
            base, AssetBalance(asset=base, available=_ZERO, reserved=_ZERO)
        )
        position = state.positions.setdefault(
            fill.symbol, PositionState(symbol=fill.symbol)
        )

        if fill.side == OrderSide.BUY:
            # Spend quote (price * qty + fee), receive base.
            cost = gross + fee
            if quote_bal.reserved >= cost:
                quote_bal.reserved -= cost
            elif quote_bal.reserved + quote_bal.available >= cost:
                remainder = cost - quote_bal.reserved
                quote_bal.reserved = _ZERO
                quote_bal.available -= remainder
            else:
                # Allow negative available only if already reserved insufficiently —
                # caller should have checked; clamp to available+reserved.
                spend = min(cost, quote_bal.total)
                if quote_bal.reserved >= spend:
                    quote_bal.reserved -= spend
                else:
                    spend_from_reserved = quote_bal.reserved
                    quote_bal.reserved = _ZERO
                    quote_bal.available -= spend - spend_from_reserved

            base_bal.available += fill.quantity
            # Average entry including fees in cost basis.
            new_qty = position.quantity + fill.quantity
            if new_qty > 0:
                prev_cost = position.average_entry_price * position.quantity
                position.average_entry_price = (prev_cost + cost) / new_qty
            position.quantity = new_qty
            net_cash = -(gross + fee)
        else:
            # Sell base, receive quote minus fee.
            proceeds = gross - fee
            if base_bal.reserved >= fill.quantity:
                base_bal.reserved -= fill.quantity
            else:
                from_reserved = min(base_bal.reserved, fill.quantity)
                base_bal.reserved -= from_reserved
                base_bal.available -= fill.quantity - from_reserved

            quote_bal.available += proceeds

            # Realized PnL vs this symbol's average entry; leftover qty may sit
            # on another quote pair (ATOMEUR vs ATOMUSDT) and is closed below.
            close_qty = min(position.quantity, fill.quantity) if position.quantity > 0 else _ZERO
            if close_qty > 0 and position.average_entry_price > 0:
                cost_basis = position.average_entry_price * close_qty
                realized = (proceeds * close_qty / fill.quantity) - cost_basis
            position.quantity -= close_qty
            if position.quantity <= 0:
                position.quantity = _ZERO
                position.average_entry_price = _ZERO
            leftover = fill.quantity - close_qty
            if leftover > 0:
                _drain_same_base_positions(state, base=base, quantity=leftover, skip_symbol=fill.symbol)
            net_cash = proceeds

        position.realized_pnl += realized
        position.fees_paid += fee
        state.stats.realized_pnl += realized
        state.stats.fees_paid += fee
        state.stats.total_trading_volume += gross
        state.stats.number_of_trades += 1
        if realized > 0:
            state.stats.winning_trades += 1
        elif realized < 0:
            state.stats.losing_trades += 1

        self._processed_fill_ids.add(fill_key)
        return AccountingResult(
            fill_id=fill.id,
            order_id=order.id,
            gross_trade_value=gross,
            trading_fee=fee,
            net_cash_movement=net_cash,
            realized_pnl=realized,
            remaining_position=position.quantity,
            average_entry_price=position.average_entry_price,
            applied=True,
            duplicate=False,
        )


_COMMON_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "EUR", "GBP", "BTC")


def _infer_base(symbol: str, quote: str) -> str:
    sym = symbol.upper().replace("/", "").replace("-", "")
    q = quote.upper()
    if sym.endswith(q) and len(sym) > len(q):
        return sym[: -len(q)]
    for suffix in _COMMON_QUOTE_SUFFIXES:
        if sym.endswith(suffix) and len(sym) > len(suffix):
            return sym[: -len(suffix)]
    return sym


def _drain_same_base_positions(
    state: PortfolioState,
    *,
    base: str,
    quantity: Decimal,
    skip_symbol: str,
) -> None:
    """Close leftover coins that still sit on another quote pair's lot."""
    if quantity <= 0:
        return
    quote = state.quote_asset
    remaining = quantity
    skip = skip_symbol.upper()
    lots = sorted(
        (
            pos
            for symbol, pos in state.positions.items()
            if symbol != skip and pos.quantity > 0 and _infer_base(symbol, quote) == base
        ),
        key=lambda pos: pos.quantity,
        reverse=True,
    )
    for pos in lots:
        if remaining <= 0:
            break
        take = min(pos.quantity, remaining)
        pos.quantity -= take
        remaining -= take
        if pos.quantity <= 0:
            pos.quantity = _ZERO
            pos.average_entry_price = _ZERO

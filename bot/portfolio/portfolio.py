"""Paper portfolio: balances, positions, equity, drawdown — fill-driven only."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderSide
from bot.core.models import Balance, PortfolioSnapshot, Position
from bot.portfolio.accounting import AccountingEngine
from bot.portfolio.models import (
    AccountingResult,
    AssetBalance,
    Fill,
    Order,
    PortfolioState,
    PortfolioStats,
    PositionState,
)

_ZERO = Decimal("0")


class PaperPortfolio:
    """In-memory paper portfolio with optional persistence hooks.

    Implements ``PortfolioService.get_snapshot`` for the risk / trading engine.
    Starting capital defaults to ``PAPER_STARTING_EUR`` (EUR only, no crypto).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        accounting: AccountingEngine | None = None,
        starting_eur: Decimal | None = None,
    ) -> None:
        self._settings = settings
        self._quote = settings.paper_quote_asset.upper()
        self._accounting = accounting or AccountingEngine()
        start = (
            starting_eur
            if starting_eur is not None
            else Decimal(str(settings.paper_starting_eur))
        )
        self._state = PortfolioState(
            quote_asset=self._quote,
            balances={
                self._quote: AssetBalance(
                    asset=self._quote, available=start, reserved=_ZERO
                )
            },
            positions={},
            stats=PortfolioStats(peak_equity=start),
            mark_prices={},
        )
        self._update_drawdown()

    @property
    def accounting(self) -> AccountingEngine:
        return self._accounting

    @property
    def state(self) -> PortfolioState:
        return self._state

    def snapshot_state(self) -> PortfolioState:
        return self._state.model_copy(deep=True)

    def load_state(self, state: PortfolioState, *, processed_fill_ids: set[str] | None = None) -> None:
        self._state = state.model_copy(deep=True)
        if processed_fill_ids:
            self._accounting.load_processed_ids(processed_fill_ids)
        self._update_drawdown()

    async def get_snapshot(self) -> PortfolioSnapshot:
        """Risk-engine compatible snapshot (quote treated as equity currency)."""
        self._update_unrealized()
        self._update_drawdown()
        balances = [
            Balance(asset=b.asset, free=b.available, locked=b.reserved)
            for b in self._state.balances.values()
        ]
        positions: list[Position] = []
        for pos in self._state.positions.values():
            if pos.quantity == 0:
                continue
            mark = self._state.mark_prices.get(pos.symbol, pos.average_entry_price)
            unrealized = (mark - pos.average_entry_price) * pos.quantity
            positions.append(
                Position(
                    symbol=pos.symbol,
                    quantity=pos.quantity,
                    average_entry_price=pos.average_entry_price,
                    unrealized_pnl_usd=unrealized,
                    side=OpportunitySide.BUY,
                )
            )
        equity = self._state.total_equity
        return PortfolioSnapshot(
            balances=balances,
            positions=positions,
            equity_usd=equity,
            peak_equity_usd=self._state.stats.peak_equity,
            daily_realized_pnl_usd=self._state.stats.realized_pnl,
            open_position_count=len(positions),
            as_of=datetime.now(UTC),
        )

    def set_mark_price(self, symbol: str, price: Decimal) -> None:
        self._state.mark_prices[symbol.upper()] = price
        self._update_unrealized()
        self._update_drawdown()

    def available(self, asset: str) -> Decimal:
        bal = self._state.balances.get(asset.upper())
        return bal.available if bal else _ZERO

    def reserved(self, asset: str) -> Decimal:
        bal = self._state.balances.get(asset.upper())
        return bal.reserved if bal else _ZERO

    def reserve(self, asset: str, amount: Decimal) -> bool:
        """Move available → reserved for a pending order. Returns False if short."""
        if amount <= 0:
            return True
        key = asset.upper()
        bal = self._state.balances.setdefault(
            key, AssetBalance(asset=key, available=_ZERO, reserved=_ZERO)
        )
        if bal.available < amount:
            return False
        bal.available -= amount
        bal.reserved += amount
        return True

    def release_reservation(self, asset: str, amount: Decimal) -> None:
        if amount <= 0:
            return
        key = asset.upper()
        bal = self._state.balances.setdefault(
            key, AssetBalance(asset=key, available=_ZERO, reserved=_ZERO)
        )
        release = min(amount, bal.reserved)
        bal.reserved -= release
        bal.available += release

    def apply_fill(self, order: Order, fill: Fill) -> AccountingResult:
        """Update portfolio from a fill only (idempotent)."""
        result = self._accounting.apply_fill(self._state, order, fill)
        if result.applied:
            self._update_unrealized()
            self._update_drawdown()
            self._state.as_of = datetime.now(UTC)
        return result

    def base_asset_for(self, symbol: str) -> str:
        from bot.portfolio.accounting import _infer_base

        return _infer_base(symbol, self._quote)

    def _update_unrealized(self) -> None:
        unrealized = _ZERO
        for symbol, pos in self._state.positions.items():
            if pos.quantity == 0:
                continue
            mark = self._state.mark_prices.get(symbol, pos.average_entry_price)
            unrealized += (mark - pos.average_entry_price) * pos.quantity
        self._state.stats.unrealized_pnl = unrealized

    def _update_drawdown(self) -> None:
        equity = self._state.total_equity
        stats = self._state.stats
        if equity > stats.peak_equity:
            stats.peak_equity = equity
        peak = stats.peak_equity
        if peak > 0:
            dd = (peak - equity) / peak
            stats.current_drawdown = max(dd, _ZERO)
            if stats.current_drawdown > stats.maximum_drawdown:
                stats.maximum_drawdown = stats.current_drawdown
        else:
            stats.current_drawdown = _ZERO

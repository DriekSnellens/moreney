"""Persistence helpers for paper orders, fills, and portfolio snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from bot.portfolio.models import Fill, Order, PortfolioState
from bot.portfolio.portfolio import PaperPortfolio
from database.models import (
    DailyStatsRecord,
    FillRecord,
    OrderRecord,
    PortfolioSnapshotRecord,
)


def order_to_record(order: Order) -> OrderRecord:
    return OrderRecord(
        id=order.id,
        strategy=order.strategy,
        symbol=order.symbol,
        side=order.side.value,
        order_type=order.order_type.value,
        requested_quantity=order.requested_quantity,
        filled_quantity=order.filled_quantity,
        requested_price=order.requested_price,
        average_fill_price=order.average_fill_price,
        fee=order.fee,
        slippage=order.slippage,
        status=order.status.value,
        exchange=order.exchange,
        rejection_reason=order.rejection_reason,
        opportunity_id=order.opportunity_id,
        extra=order.metadata,
    )


def fill_to_record(fill: Fill) -> FillRecord:
    return FillRecord(
        id=fill.id,
        order_id=fill.order_id,
        symbol=fill.symbol,
        side=fill.side.value,
        quantity=fill.quantity,
        price=fill.price,
        fee=fill.fee,
        fee_asset=fill.fee_asset,
        slippage=fill.slippage,
        exchange=fill.exchange,
        extra=fill.metadata,
    )


def portfolio_to_record(portfolio: PaperPortfolio) -> PortfolioSnapshotRecord:
    state = portfolio.state
    return PortfolioSnapshotRecord(
        quote_asset=state.quote_asset,
        equity=state.total_equity,
        realized_pnl=state.stats.realized_pnl,
        unrealized_pnl=state.stats.unrealized_pnl,
        fees_paid=state.stats.fees_paid,
        current_drawdown=state.stats.current_drawdown,
        maximum_drawdown=state.stats.maximum_drawdown,
        balances={
            k: {"available": str(v.available), "reserved": str(v.reserved)}
            for k, v in state.balances.items()
        },
        positions={
            k: {
                "quantity": str(v.quantity),
                "average_entry_price": str(v.average_entry_price),
                "realized_pnl": str(v.realized_pnl),
                "fees_paid": str(v.fees_paid),
            }
            for k, v in state.positions.items()
        },
        processed_fill_ids=sorted(portfolio.accounting.processed_fill_ids),
        extra={},
    )


def daily_stats_to_record(portfolio: PaperPortfolio, day: str | None = None) -> DailyStatsRecord:
    stats = portfolio.state.stats
    equity = portfolio.state.total_equity
    return DailyStatsRecord(
        day=day or datetime.now(UTC).strftime("%Y-%m-%d"),
        starting_equity=stats.peak_equity,  # best available without separate day start
        ending_equity=equity,
        gross_pnl=stats.realized_pnl + stats.fees_paid,
        fees_paid=stats.fees_paid,
        slippage=Decimal("0"),
        net_pnl=stats.realized_pnl,
        return_pct=Decimal("0"),
        number_of_trades=stats.number_of_trades,
        winning_trades=stats.winning_trades,
        losing_trades=stats.losing_trades,
        realized_pnl=stats.realized_pnl,
        total_trading_volume=stats.total_trading_volume,
        maximum_drawdown=stats.maximum_drawdown,
    )


class InMemoryPaperStore:
    """Test-friendly persistence that mirrors SQL tables without a DB."""

    def __init__(self) -> None:
        self.orders: dict[UUID, OrderRecord] = {}
        self.fills: dict[UUID, FillRecord] = {}
        self.snapshots: list[PortfolioSnapshotRecord] = []
        self.daily_stats: list[DailyStatsRecord] = []

    def save_order(self, order: Order) -> None:
        self.orders[order.id] = order_to_record(order)

    def save_fill(self, fill: Fill) -> None:
        # Idempotent: same fill id overwrites / ignored
        self.fills[fill.id] = fill_to_record(fill)

    def save_portfolio(self, portfolio: PaperPortfolio) -> PortfolioSnapshotRecord:
        row = portfolio_to_record(portfolio)
        self.snapshots.append(row)
        return row

    def save_daily_stats(self, portfolio: PaperPortfolio) -> DailyStatsRecord:
        row = daily_stats_to_record(portfolio)
        self.daily_stats.append(row)
        return row

    def reload_portfolio(
        self,
        settings,
        *,
        snapshot: PortfolioSnapshotRecord | None = None,
    ) -> PaperPortfolio:
        from bot.portfolio.models import AssetBalance, PortfolioState, PortfolioStats, PositionState
        from bot.portfolio.portfolio import PaperPortfolio

        row = snapshot or (self.snapshots[-1] if self.snapshots else None)
        portfolio = PaperPortfolio(settings)
        if row is None:
            return portfolio

        balances = {
            asset: AssetBalance(
                asset=asset,
                available=Decimal(data["available"]),
                reserved=Decimal(data["reserved"]),
            )
            for asset, data in row.balances.items()
        }
        positions = {
            symbol: PositionState(
                symbol=symbol,
                quantity=Decimal(data["quantity"]),
                average_entry_price=Decimal(data["average_entry_price"]),
                realized_pnl=Decimal(data["realized_pnl"]),
                fees_paid=Decimal(data["fees_paid"]),
            )
            for symbol, data in row.positions.items()
        }
        state = PortfolioState(
            balances=balances,
            positions=positions,
            quote_asset=row.quote_asset,
            stats=PortfolioStats(
                realized_pnl=row.realized_pnl,
                unrealized_pnl=row.unrealized_pnl,
                fees_paid=row.fees_paid,
                peak_equity=row.equity,
                current_drawdown=row.current_drawdown,
                maximum_drawdown=row.maximum_drawdown,
            ),
        )
        processed = {str(x) for x in (row.processed_fill_ids or [])}
        portfolio.load_state(state, processed_fill_ids=processed)
        return portfolio

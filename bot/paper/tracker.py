"""Performance and opportunity tracking for paper trading.

Uses Decimal for all financial calculations. Records every opportunity,
including rejects. Never fabricates values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from bot.core.enums import OpportunityLifecycleStatus, OrderSide, OrderStatus
from bot.core.models import (
    ExecutionResult,
    ProfitabilityResult,
    RiskDecision,
    TradeOpportunity,
)
from bot.paper.models import (
    DailyStats,
    ExchangePairStats,
    HourlyStats,
    PerformanceSnapshot,
    StrategyStats,
    TrackedOpportunity,
)
from bot.portfolio.models import Fill, Order
from bot.portfolio.portfolio import PaperPortfolio

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def _d(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return _ZERO
    return Decimal(str(value))


class PerformanceTracker:
    """Tracks opportunities, performance, and multi-dimensional statistics."""

    def __init__(self, *, starting_equity: Decimal) -> None:
        self._starting_equity = starting_equity
        self._peak_equity = starting_equity
        self._current_equity = starting_equity
        self._realized_pnl = _ZERO
        self._unrealized_pnl = _ZERO
        self._gross_pnl = _ZERO
        self._fees = _ZERO
        self._slippage = _ZERO
        self._trading_volume = _ZERO
        self._trade_pnls: list[Decimal] = []
        self._current_drawdown = _ZERO
        self._maximum_drawdown = _ZERO

        self._opportunities: list[TrackedOpportunity] = []
        self._by_id: dict[UUID, TrackedOpportunity] = {}
        self._trades: list[dict[str, Any]] = []
        self._calibration_queue: list[dict[str, Any]] = []

        self._strategies: dict[str, StrategyStats] = {}
        self._pairs: dict[str, ExchangePairStats] = {}
        self._hourly: dict[int, HourlyStats] = {h: HourlyStats(hour=h) for h in range(24)}
        self._daily: dict[str, DailyStats] = {}
        self._day_starting_equity = starting_equity

        self._approved = 0
        self._rejected = 0
        self._executed = 0
        self._execution_failures = 0
        self._pairs_evaluated = 0
        self._depth_edges_found = 0
        self._scan_rejections = 0
        self._reject_counts: dict[str, int] = {}
        self._counted_fill_ids: set[UUID] = set()

    # ------------------------------------------------------------------
    # Opportunity lifecycle
    # ------------------------------------------------------------------

    def record_detected(
        self,
        opportunity: TradeOpportunity,
        profitability: ProfitabilityResult | None = None,
    ) -> TrackedOpportunity:
        meta = opportunity.metadata or {}
        fees = _ZERO
        slippage = _ZERO
        buffer = _ZERO
        gross = _ZERO
        net = _ZERO
        net_ret = _ZERO
        if profitability is not None:
            fees = profitability.fees_usd
            slippage = profitability.slippage_usd
            buffer = profitability.execution_buffer_usd
            gross = profitability.gross_profit_usd
            net = profitability.net_profit_usd
            net_ret = profitability.net_return
        else:
            gross = _d(meta.get("gross_profit_eur", meta.get("gross_profit", 0)))
            net = _d(meta.get("net_profit_eur", meta.get("net_profit", 0)))
            fees = _d(meta.get("fees_eur", meta.get("fees", 0)))
            slippage = _d(meta.get("slippage_eur", meta.get("slippage", 0)))
            buffer = _d(meta.get("execution_buffer_eur", meta.get("execution_buffer", 0)))
            net_ret = _d(meta.get("net_return", 0))

        tracked = TrackedOpportunity(
            id=opportunity.id,
            timestamp=opportunity.created_at if opportunity.created_at.tzinfo else datetime.now(UTC),
            strategy=opportunity.strategy_name,
            symbol=opportunity.symbol,
            buy_exchange=str(meta.get("buy_exchange") or ""),
            sell_exchange=str(meta.get("sell_exchange") or ""),
            quantity=opportunity.quantity,
            gross_profit=gross,
            fees=fees,
            slippage=slippage,
            execution_buffer=buffer,
            expected_net_profit=net,
            expected_net_return=net_ret,
            expected_gross=gross,
            expected_adverse=_d(meta.get("expected_adverse_eur", meta.get("execution_buffer_eur", buffer))),
            expected_inventory=_d(meta.get("inventory_relief_eur", 0)),
            calibrated_expected_value=(
                _d(meta.get("calibrated_expected_value"))
                if meta.get("calibrated_expected_value") is not None
                else None
            ),
            status=OpportunityLifecycleStatus.DETECTED,
            metadata=dict(meta),
        )
        self._opportunities.append(tracked)
        self._by_id[tracked.id] = tracked
        self._strategy(tracked.strategy).opportunities += 1
        pair = self._pair(tracked.buy_exchange, tracked.sell_exchange)
        pair.opportunities += 1
        hour = tracked.timestamp.astimezone(UTC).hour
        self._hourly[hour].opportunities += 1
        return tracked

    def record_scan_stats(self, stats: dict[str, object]) -> None:
        """Replace cumulative strategy scan funnel counters (absolute totals)."""
        self._pairs_evaluated = int(stats.get("pairs_evaluated", self._pairs_evaluated) or 0)
        self._depth_edges_found = int(stats.get("depth_edges_found", self._depth_edges_found) or 0)
        self._scan_rejections = int(stats.get("scan_rejections", self._scan_rejections) or 0)
        raw_counts = stats.get("reject_counts") or {}
        if isinstance(raw_counts, dict):
            self._reject_counts = {str(k): int(v) for k, v in raw_counts.items()}

    def record_risk(
        self,
        opportunity_id: UUID,
        decision: RiskDecision,
    ) -> TrackedOpportunity | None:
        tracked = self._by_id.get(opportunity_id)
        if tracked is None:
            return None
        tracked.risk_decision = decision.status.value
        if decision.approved:
            tracked.status = OpportunityLifecycleStatus.APPROVED
            tracked.rejection_reason = None
            self._approved += 1
            self._pair(tracked.buy_exchange, tracked.sell_exchange).approved += 1
        else:
            tracked.status = OpportunityLifecycleStatus.REJECTED
            tracked.rejection_reason = decision.rejection_reason or "; ".join(decision.reasons)
            self._rejected += 1
        return tracked

    def record_execution(
        self,
        opportunity_id: UUID,
        execution: ExecutionResult,
        *,
        orders: list[Order] | None = None,
        fills: list[Fill] | None = None,
        equity_before: Decimal | None = None,
        equity_after: Decimal | None = None,
    ) -> TrackedOpportunity | None:
        tracked = self._by_id.get(opportunity_id)
        if tracked is None:
            return None

        first = tracked.execution_result is None
        tracked.execution_result = execution.status.value
        tracked.order_id = execution.order_id
        pair = self._pair(tracked.buy_exchange, tracked.sell_exchange)
        if first:
            self._executed += 1
            self._strategy(tracked.strategy).executions += 1
            pair.executed += 1

        failed = execution.status in {
            OrderStatus.REJECTED,
            OrderStatus.FAILED,
        }
        cancelled_empty = (
            execution.status == OrderStatus.CANCELLED and execution.filled_quantity <= 0
        )
        if failed or cancelled_empty:
            tracked.status = OpportunityLifecycleStatus.EXECUTED
            if failed and first:
                self._execution_failures += 1
                pair.execution_failures += 1
            return tracked

        if execution.status == OrderStatus.OPEN and execution.filled_quantity <= 0:
            tracked.status = OpportunityLifecycleStatus.EXECUTED
            return tracked

        if (not fills) and execution.filled_quantity <= 0:
            tracked.status = OpportunityLifecycleStatus.EXECUTED
            return tracked

        if tracked.realized_net_profit is not None:
            return tracked

        tracked.status = OpportunityLifecycleStatus.EXECUTED
        new_fills = []
        for fill in fills or []:
            if fill.id in self._counted_fill_ids:
                continue
            self._counted_fill_ids.add(fill.id)
            new_fills.append(fill)
        fill_fees = sum((f.fee for f in new_fills), _ZERO)
        fill_slip = sum((f.slippage for f in new_fills), _ZERO)
        fill_volume = sum((f.gross_value for f in new_fills), _ZERO)
        if fills:
            tracked.fees = sum((f.fee for f in fills), _ZERO)
            tracked.slippage = sum((f.slippage for f in fills), _ZERO)

        self._fees += fill_fees
        self._slippage += fill_slip
        self._trading_volume += fill_volume

        # Realized PnL requires a completed round-trip (buy + sell fills).
        realized = self._estimate_realized(tracked, execution, fills or [])
        if equity_before is not None and equity_after is not None and realized is None:
            # Only use equity delta when not cross-exchange arb (no sell leg expected).
            if not tracked.sell_exchange:
                realized = equity_after - equity_before

        tracked.status = OpportunityLifecycleStatus.FILLED
        if realized is not None:
            tracked.realized_net_profit = realized
            self._register_trade(tracked, realized)
        elif tracked.sell_exchange:
            # Buy leg filled but round-trip incomplete — inventory risk, not locked profit.
            tracked.status = OpportunityLifecycleStatus.EXECUTED

        return tracked

    def finalize_triangle_pnl(
        self,
        tracked: TrackedOpportunity,
        fills: list[Fill],
        *,
        fx_refill_cost_eur: Decimal = _ZERO,
    ) -> Decimal | None:
        """Lock EUR PnL after FX refill (or skipped refill with zero cost)."""
        if tracked.realized_net_profit is not None:
            return tracked.realized_net_profit
        meta = dict(tracked.metadata or {})
        meta["fx_refilled"] = True
        meta["fx_refill_cost_eur"] = str(fx_refill_cost_eur)
        tracked.metadata = meta
        from bot.core.models import ExecutionResult as _ER

        order_id = tracked.order_id
        if order_id is None and fills:
            order_id = fills[0].order_id
        if order_id is None:
            order_id = tracked.id
        dummy = _ER(
            order_id=order_id,
            opportunity_id=tracked.id,
            status=OrderStatus.FILLED,
            filled_quantity=sum((f.quantity for f in fills), _ZERO),
            average_price=fills[0].price if fills else None,
            fees_usd=_ZERO,
            message="fx_refill_finalize",
        )
        realized = self._estimate_realized(tracked, dummy, fills)
        if realized is not None:
            tracked.realized_net_profit = realized
            self._register_trade(tracked, realized)
        return realized

    def _estimate_realized(
        self,
        tracked: TrackedOpportunity,
        execution: ExecutionResult,
        fills: list[Fill],
    ) -> Decimal | None:
        if not fills:
            return None

        buy_fills = [f for f in fills if f.side == OrderSide.BUY]
        sell_fills = [f for f in fills if f.side == OrderSide.SELL]

        if buy_fills and sell_fills:
            buy_qty = sum((f.quantity for f in buy_fills), _ZERO)
            sell_qty = sum((f.quantity for f in sell_fills), _ZERO)
            matched_qty = min(buy_qty, sell_qty)
            if matched_qty <= 0:
                return None
            # Triangle / cross-quote: convert USDT legs to EUR via stored FX mid.
            if bool((tracked.metadata or {}).get("triangle")) and not bool(
                (tracked.metadata or {}).get("fx_refilled")
            ):
                # Wait for FX refill so EUR PnL includes conversion cost.
                return None
            buy_cost = self._fills_notional_eur(tracked, buy_fills, leg="buy")
            sell_proceeds = self._fills_notional_eur(tracked, sell_fills, leg="sell")
            # Scale to matched size: leftover coins are inventory, not a locked loss.
            if buy_qty > 0:
                buy_cost = buy_cost * (matched_qty / buy_qty)
            if sell_qty > 0:
                sell_proceeds = sell_proceeds * (matched_qty / sell_qty)
            # Optional FX refill cost recorded on the opportunity metadata.
            fx_cost = _d((tracked.metadata or {}).get("fx_refill_cost_eur", 0))
            if buy_cost <= 0 or sell_proceeds <= 0:
                return None
            realized = sell_proceeds - buy_cost - fx_cost
            self._attach_attribution(tracked, buy_fills, sell_fills, realized, fx_cost)
            return realized

        # Cross-exchange arb without a sell fill is not a completed trade.
        if tracked.sell_exchange:
            return None

        # Non-arb strategies: single-leg realized from equity or expected path.
        sell_vwap = tracked.metadata.get("sell_vwap") or tracked.metadata.get("sell_price")
        if sell_vwap is None:
            return None
        qty = sum((f.quantity for f in fills), _ZERO)
        if qty <= 0:
            return None
        buy_cost = self._fills_notional_eur(tracked, fills, leg="buy")
        sell_proceeds = _d(sell_vwap) * qty
        return sell_proceeds - buy_cost

    def _fills_notional_eur(
        self,
        tracked: TrackedOpportunity,
        fills: list[Fill],
        *,
        leg: str,
    ) -> Decimal:
        """Sum fill cashflows in EUR (convert USDT via opportunity FX mid)."""
        meta = tracked.metadata or {}
        fx_mid = _d(meta.get("fx_mid") or 0)
        total = _ZERO
        for fill in fills:
            quote = str(getattr(fill, "fee_asset", "") or "").upper()
            # Infer quote from order symbol when fee_asset is missing/EUR default.
            symbol = str(getattr(fill, "symbol", "") or tracked.symbol).upper()
            if symbol.endswith("USDT"):
                quote = "USDT"
            elif symbol.endswith("EUR"):
                quote = "EUR"
            gross = fill.quantity * fill.price
            if leg == "buy":
                cash = gross + fill.fee + fill.slippage
            else:
                cash = gross - fill.fee - fill.slippage
            if quote == "USDT":
                if fx_mid <= 0:
                    # Without FX we cannot lock EUR PnL yet.
                    continue
                cash = cash / fx_mid
            total += cash
        return total

    def _attach_attribution(
        self,
        tracked: TrackedOpportunity,
        buy_fills: list[Fill],
        sell_fills: list[Fill],
        realized: Decimal,
        fx_cost: Decimal,
    ) -> None:
        """Decompose realized NET via waterfall identity (not EV-gap as adverse)."""
        from bot.opportunity.waterfall import realized_waterfall

        buy_fee = sum((f.fee for f in buy_fills), _ZERO)
        sell_fee = sum((f.fee for f in sell_fills), _ZERO)
        buy_slip = sum((f.slippage for f in buy_fills), _ZERO)
        sell_slip = sum((f.slippage for f in sell_fills), _ZERO)
        tracked.realized_fees = buy_fee + sell_fee + fx_cost
        tracked.realized_slippage = buy_slip + sell_slip
        # Adverse = opportunity gross − known cash costs − realized NET.
        # Never use (expected_net − realized) here: that is the EV gap, which
        # mixes fee/slippage model error with true adverse selection.
        wf = realized_waterfall(
            gross=tracked.expected_gross or tracked.gross_profit,
            buy_fee=buy_fee + fx_cost,
            sell_fee=sell_fee,
            slippage=buy_slip + sell_slip,
            realized_net=realized,
        )
        tracked.realized_adverse = wf.adverse_selection
        tracked.realized_inventory = wf.inventory_effect
        tracked.metadata = {
            **(tracked.metadata or {}),
            "pnl_waterfall_realized": wf.as_dict(),
            "ev_gap": str(realized - tracked.expected_net_profit),
        }

    def calibration_observations(self) -> list[dict[str, Any]]:
        """Completed fills for EV calibrator (past data only)."""
        rows: list[dict[str, Any]] = []
        for tracked in self._opportunities:
            if tracked.realized_net_profit is None:
                continue
            rows.append(
                {
                    "key": (
                        f"{tracked.strategy}|{tracked.symbol}|"
                        f"{tracked.buy_exchange}->{tracked.sell_exchange}|buy"
                    ),
                    "route": f"{tracked.buy_exchange}->{tracked.sell_exchange}",
                    "strategy": tracked.strategy,
                    "expected_net": tracked.expected_net_profit,
                    "realized_net": tracked.realized_net_profit,
                }
            )
        return rows

    def _register_trade(self, tracked: TrackedOpportunity, realized: Decimal) -> None:
        self._trade_pnls.append(realized)
        self._realized_pnl += realized
        self._gross_pnl += tracked.gross_profit
        self._net_from_trades()

        if realized > 0:
            tracked.status = OpportunityLifecycleStatus.PROFITABLE
        else:
            tracked.status = OpportunityLifecycleStatus.UNPROFITABLE

        strat = self._strategy(tracked.strategy)
        strat.trades += 1
        strat.net_pnl += realized
        strat.fees += tracked.fees
        strat.slippage += tracked.slippage
        if realized > 0:
            strat.winning_trades += 1
            strat.gross_wins += realized
        elif realized < 0:
            strat.losing_trades += 1
            strat.gross_losses += abs(realized)

        pair = self._pair(tracked.buy_exchange, tracked.sell_exchange)
        pair.trades += 1
        pair.net_pnl += realized
        pair.fees += tracked.fees
        pair.slippage += tracked.slippage
        if realized > 0:
            pair.winning_trades += 1

        hour = tracked.timestamp.astimezone(UTC).hour
        bucket = self._hourly[hour]
        bucket.trades += 1
        bucket.net_pnl += realized
        if realized > 0:
            bucket.winning_trades += 1

        self._trades.append(
            {
                "opportunity_id": str(tracked.id),
                "timestamp": tracked.timestamp.isoformat(),
                "strategy": tracked.strategy,
                "symbol": tracked.symbol,
                "buy_exchange": tracked.buy_exchange,
                "sell_exchange": tracked.sell_exchange,
                "quantity": str(tracked.quantity),
                "fees": str(tracked.fees),
                "slippage": str(tracked.slippage),
                "expected_net_profit": str(tracked.expected_net_profit),
                "realized_net_profit": str(realized),
                "expected_gross": str(tracked.expected_gross or tracked.gross_profit),
                "expected_fees": str(tracked.fees),
                "expected_slippage": str(tracked.slippage),
                "expected_adverse": str(tracked.expected_adverse or tracked.execution_buffer),
                "expected_inventory": str(tracked.expected_inventory or _ZERO),
                "realized_fees": str(tracked.realized_fees) if tracked.realized_fees is not None else None,
                "realized_slippage": str(tracked.realized_slippage) if tracked.realized_slippage is not None else None,
                "realized_adverse": str(tracked.realized_adverse) if tracked.realized_adverse is not None else None,
                "ev_gap": str(realized - tracked.expected_net_profit),
                "status": tracked.status.value,
            }
        )
        self._calibration_queue.append(
            {
                "key": (
                    f"{tracked.strategy}|{tracked.symbol}|"
                    f"{tracked.buy_exchange}->{tracked.sell_exchange}|buy"
                ),
                "route": f"{tracked.buy_exchange}->{tracked.sell_exchange}",
                "strategy": tracked.strategy,
                "expected_net": tracked.expected_net_profit,
                "realized_net": realized,
                "opportunity_id": str(tracked.id),
            }
        )

    def drain_calibration_observations(self) -> list[dict[str, Any]]:
        """Return newly completed round-trips since last drain (for live calibrate)."""
        rows = list(self._calibration_queue)
        self._calibration_queue.clear()
        return rows

    def sync_portfolio(self, portfolio: PaperPortfolio) -> None:
        """Refresh equity / drawdown / unrealized from the live paper portfolio."""
        state = portfolio.state
        equity = state.total_equity
        self._current_equity = equity
        self._unrealized_pnl = state.stats.unrealized_pnl
        # Prefer portfolio accounting for fees/volume when available.
        if state.stats.fees_paid > self._fees:
            self._fees = state.stats.fees_paid
        if state.stats.total_trading_volume > self._trading_volume:
            self._trading_volume = state.stats.total_trading_volume

        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity > 0:
            self._current_drawdown = (self._peak_equity - equity) / self._peak_equity
        else:
            self._current_drawdown = _ZERO
        if self._current_drawdown > self._maximum_drawdown:
            self._maximum_drawdown = self._current_drawdown
        if state.stats.maximum_drawdown > self._maximum_drawdown:
            self._maximum_drawdown = state.stats.maximum_drawdown

        # USDT seed used to vanish from EUR equity (~20% fake drawdown, 0 trades).
        # Once cash is marked in quote, rewind that accounting hole.
        if not self._trade_pnls and self._starting_equity > 0:
            gap = abs(equity - self._starting_equity) / self._starting_equity
            if gap < Decimal("0.05"):
                self._peak_equity = max(self._starting_equity, equity)
                if self._peak_equity > 0:
                    self._current_drawdown = (self._peak_equity - equity) / self._peak_equity
                else:
                    self._current_drawdown = _ZERO
                self._maximum_drawdown = self._current_drawdown
                state.stats.peak_equity = self._peak_equity
                state.stats.current_drawdown = self._current_drawdown
                state.stats.maximum_drawdown = self._maximum_drawdown
                self._day_starting_equity = self._starting_equity
                for day in self._daily.values():
                    day.starting_equity = self._starting_equity
                    day.ending_equity = equity
                    day.net_pnl = equity - self._starting_equity
                    if day.starting_equity > 0:
                        day.return_pct = (day.net_pnl / day.starting_equity) * _HUNDRED
                    day.maximum_drawdown = self._maximum_drawdown

        self._roll_daily(equity)

    def _roll_daily(self, equity: Decimal) -> None:
        today = date.today().isoformat()
        if today not in self._daily:
            # New day: seed from current equity.
            prev_days = sorted(self._daily.keys())
            start = self._daily[prev_days[-1]].ending_equity if prev_days else self._starting_equity
            self._day_starting_equity = start if start else equity
            self._daily[today] = DailyStats(
                date=today,
                starting_equity=self._day_starting_equity,
                ending_equity=equity,
            )
        day = self._daily[today]
        day.ending_equity = equity
        day.fees = self._fees
        day.slippage = self._slippage
        day.gross_pnl = self._gross_pnl
        day.net_pnl = equity - day.starting_equity
        if day.starting_equity > 0:
            day.return_pct = (day.net_pnl / day.starting_equity) * _HUNDRED
        day.trades = len(self._trade_pnls)
        day.wins = sum(1 for p in self._trade_pnls if p > 0)
        day.losses = sum(1 for p in self._trade_pnls if p < 0)
        day.maximum_drawdown = self._maximum_drawdown

    def _net_from_trades(self) -> None:
        pass  # net_pnl derived from equity - starting in snapshot

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def snapshot(self) -> PerformanceSnapshot:
        wins = [p for p in self._trade_pnls if p > 0]
        losses = [p for p in self._trade_pnls if p < 0]
        trade_count = len(self._trade_pnls)
        win_rate = (
            Decimal(len(wins)) / Decimal(trade_count) if trade_count else _ZERO
        )
        avg_win = sum(wins, _ZERO) / Decimal(len(wins)) if wins else _ZERO
        avg_loss = sum(losses, _ZERO) / Decimal(len(losses)) if losses else _ZERO
        gross_wins = sum(wins, _ZERO)
        gross_losses = abs(sum(losses, _ZERO))
        if gross_losses == 0:
            profit_factor: Decimal | None = None if gross_wins > 0 else _ZERO
        else:
            profit_factor = gross_wins / gross_losses

        paper_equity_pnl = self._current_equity - self._starting_equity
        # Live-equivalent: completed round-trips after fill fees, not mark-to-market inventory.
        net_pnl = self._realized_pnl
        return_pct = (
            (net_pnl / self._starting_equity) * _HUNDRED
            if self._starting_equity > 0
            else _ZERO
        )
        completed = [o for o in self._opportunities if o.realized_net_profit is not None]
        n_fills = Decimal(trade_count) if trade_count else _ZERO
        net_per_fill = net_pnl / n_fills if n_fills else _ZERO
        volume = self._trading_volume if self._trading_volume > 0 else _ZERO
        net_bps = (net_pnl / volume * _HUNDRED * Decimal("100")) if volume > 0 else _ZERO
        sum_expected = sum((o.expected_net_profit for o in completed), _ZERO)
        ev_capture = (
            (net_pnl / sum_expected) if sum_expected != 0 and completed else None
        )
        fees_per = self._fees / n_fills if n_fills else _ZERO
        slip_per = self._slippage / n_fills if n_fills else _ZERO
        # Capital velocity: NET euro per euro of starting capital per second of
        # session is computed by the runner (has runtime). Here: NET / volume.
        velocity = net_pnl / volume if volume > 0 else _ZERO
        return PerformanceSnapshot(
            starting_equity=self._starting_equity,
            current_equity=self._current_equity,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=self._unrealized_pnl,
            paper_equity_pnl=paper_equity_pnl,
            gross_pnl=self._gross_pnl if self._gross_pnl else (net_pnl + self._fees + self._slippage),
            fees=self._fees,
            slippage=self._slippage,
            net_pnl=net_pnl,
            return_pct=return_pct,
            trade_count=trade_count,
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=win_rate,
            average_win=avg_win,
            average_loss=avg_loss,
            largest_win=max(wins) if wins else _ZERO,
            largest_loss=min(losses) if losses else _ZERO,
            profit_factor=profit_factor,
            current_drawdown=self._current_drawdown,
            maximum_drawdown=self._maximum_drawdown,
            trading_volume=self._trading_volume,
            total_opportunities=len(self._opportunities),
            approved_opportunities=self._approved,
            rejected_opportunities=self._rejected,
            executed_opportunities=self._executed,
            execution_failures=self._execution_failures,
            pairs_evaluated=self._pairs_evaluated,
            depth_edges_found=self._depth_edges_found,
            scan_rejections=self._scan_rejections,
            net_eur_per_fill=net_per_fill,
            net_bps_per_fill=net_bps,
            ev_capture=ev_capture,
            fees_per_fill=fees_per,
            slippage_per_fill=slip_per,
            capital_velocity=velocity,
        )

    def opportunities(
        self,
        *,
        limit: int = 100,
        status: OpportunityLifecycleStatus | None = None,
    ) -> list[TrackedOpportunity]:
        items = list(reversed(self._opportunities))
        if status is not None:
            items = [o for o in items if o.status == status]
        return items[:limit]

    def trades(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return list(reversed(self._trades))[:limit]

    def strategy_stats(self) -> list[StrategyStats]:
        return sorted(self._strategies.values(), key=lambda s: s.strategy)

    def exchange_pair_stats(self) -> list[ExchangePairStats]:
        return sorted(self._pairs.values(), key=lambda p: p.pair_key)

    def hourly_stats(self) -> list[HourlyStats]:
        return [self._hourly[h] for h in range(24)]

    def daily_stats(self) -> list[DailyStats]:
        return [self._daily[k] for k in sorted(self._daily.keys())]

    def reset(self, *, starting_equity: Decimal) -> None:
        self.__init__(starting_equity=starting_equity)  # type: ignore[misc]

    def export_state(self) -> dict[str, Any]:
        return {
            "starting_equity": str(self._starting_equity),
            "peak_equity": str(self._peak_equity),
            "current_equity": str(self._current_equity),
            "realized_pnl": str(self._realized_pnl),
            "unrealized_pnl": str(self._unrealized_pnl),
            "gross_pnl": str(self._gross_pnl),
            "fees": str(self._fees),
            "slippage": str(self._slippage),
            "trading_volume": str(self._trading_volume),
            "trade_pnls": [str(p) for p in self._trade_pnls],
            "current_drawdown": str(self._current_drawdown),
            "maximum_drawdown": str(self._maximum_drawdown),
            "approved": self._approved,
            "rejected": self._rejected,
            "executed": self._executed,
            "execution_failures": self._execution_failures,
            "pairs_evaluated": self._pairs_evaluated,
            "depth_edges_found": self._depth_edges_found,
            "scan_rejections": self._scan_rejections,
            "reject_counts": dict(self._reject_counts),
            "counted_fill_ids": [str(fid) for fid in self._counted_fill_ids],
            "opportunities": [o.model_dump(mode="json") for o in self._opportunities],
            "trades": list(self._trades),
            "strategies": {k: v.model_dump(mode="json") for k, v in self._strategies.items()},
            "pairs": {k: v.model_dump(mode="json") for k, v in self._pairs.items()},
            "hourly": {str(k): v.model_dump(mode="json") for k, v in self._hourly.items()},
            "daily": {k: v.model_dump(mode="json") for k, v in self._daily.items()},
            "day_starting_equity": str(self._day_starting_equity),
        }

    def import_state(self, data: dict[str, Any]) -> None:
        self._starting_equity = _d(data.get("starting_equity", self._starting_equity))
        self._peak_equity = _d(data.get("peak_equity", self._starting_equity))
        self._current_equity = _d(data.get("current_equity", self._starting_equity))
        self._realized_pnl = _d(data.get("realized_pnl", 0))
        self._unrealized_pnl = _d(data.get("unrealized_pnl", 0))
        self._gross_pnl = _d(data.get("gross_pnl", 0))
        self._fees = _d(data.get("fees", 0))
        self._slippage = _d(data.get("slippage", 0))
        self._trading_volume = _d(data.get("trading_volume", 0))
        self._trade_pnls = [_d(p) for p in data.get("trade_pnls", [])]
        self._current_drawdown = _d(data.get("current_drawdown", 0))
        self._maximum_drawdown = _d(data.get("maximum_drawdown", 0))
        self._approved = int(data.get("approved", 0))
        self._rejected = int(data.get("rejected", 0))
        self._executed = int(data.get("executed", 0))
        self._execution_failures = int(data.get("execution_failures", 0))
        self._pairs_evaluated = int(data.get("pairs_evaluated", 0))
        self._depth_edges_found = int(data.get("depth_edges_found", 0))
        self._scan_rejections = int(data.get("scan_rejections", 0))
        raw_counts = data.get("reject_counts") or {}
        self._reject_counts = (
            {str(k): int(v) for k, v in raw_counts.items()}
            if isinstance(raw_counts, dict)
            else {}
        )
        self._counted_fill_ids = {
            UUID(str(fid)) for fid in data.get("counted_fill_ids", []) if fid
        }
        self._opportunities = [
            TrackedOpportunity.model_validate(o) for o in data.get("opportunities", [])
        ]
        self._by_id = {o.id: o for o in self._opportunities}
        self._trades = list(data.get("trades", []))
        self._strategies = {
            k: StrategyStats.model_validate(v)
            for k, v in data.get("strategies", {}).items()
        }
        self._pairs = {
            k: ExchangePairStats.model_validate(v) for k, v in data.get("pairs", {}).items()
        }
        hourly_raw = data.get("hourly", {})
        self._hourly = {h: HourlyStats(hour=h) for h in range(24)}
        for k, v in hourly_raw.items():
            stats = HourlyStats.model_validate(v)
            self._hourly[stats.hour] = stats
        self._daily = {
            k: DailyStats.model_validate(v) for k, v in data.get("daily", {}).items()
        }
        self._day_starting_equity = _d(
            data.get("day_starting_equity", self._starting_equity)
        )

    def _strategy(self, name: str) -> StrategyStats:
        if name not in self._strategies:
            self._strategies[name] = StrategyStats(strategy=name)
        return self._strategies[name]

    def _pair(self, buy: str, sell: str) -> ExchangePairStats:
        key = f"{buy}->{sell}"
        if key not in self._pairs:
            self._pairs[key] = ExchangePairStats(buy_exchange=buy, sell_exchange=sell)
        return self._pairs[key]

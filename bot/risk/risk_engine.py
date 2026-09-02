"""Risk engine: mandatory gate between profitability and execution.

Architecture:
  Market Data → Strategy → Profitability → RiskEngine → Executor

Exchange-agnostic: consumes ``RiskContext`` / portfolio / profitability inputs only.
Never places orders, never uses leverage, never modifies or hides losing trades.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import KillSwitchState, OpportunitySide, RiskDecisionStatus, RiskRejectReason
from bot.core.models import (
    PortfolioSnapshot,
    ProfitabilityResult,
    RiskDecision,
    TradeOpportunity,
)
from bot.risk.kill_switch import KillSwitch
from bot.risk.models import RiskContext, RiskEvent
from bot.risk.position_limits import PositionLimitCalculator

logger = logging.getLogger(__name__)

_HUNDRED = Decimal("100")
_ZERO = Decimal("0")


class RiskEngine:
    """Evaluates every TradeOpportunity before execution (fail-closed)."""

    def __init__(
        self,
        settings: Settings,
        *,
        kill_switch: KillSwitch | None = None,
        position_limits: PositionLimitCalculator | None = None,
    ) -> None:
        self._settings = settings
        self._limits = position_limits or PositionLimitCalculator(settings)
        self._kill_switch = kill_switch or KillSwitch(settings)
        self._trade_timestamps: deque[float] = deque()
        self._min_net_profit = Decimal(str(settings.risk_min_net_profit_usd))
        self._max_slippage_pct = Decimal(str(settings.max_slippage_percent))
        self._max_data_age_ms = settings.max_market_data_age_ms
        self._max_latency_ms = settings.max_execution_latency_ms
        self._max_price_move_pct = Decimal(str(settings.max_abnormal_price_move_percent))
        self._min_liquidity = Decimal(str(settings.min_liquidity_base))
        self._max_daily_loss_pct = Decimal(str(settings.max_daily_loss_percent))
        self._max_drawdown_pct = Decimal(str(settings.max_drawdown_percent)) / _HUNDRED
        self._warn_daily_loss_pct = Decimal(str(settings.risk_warning_daily_loss_percent))
        self._warn_drawdown_pct = (
            Decimal(str(settings.risk_warning_drawdown_percent)) / _HUNDRED
        )
        self._max_trades_per_minute = settings.max_trades_per_minute
        self._allow_partial = bool(getattr(settings, "risk_allow_partial_sizing", True))
        self._partial_min_pct = Decimal(str(getattr(settings, "risk_partial_min_notional_pct", 10))) / _HUNDRED

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    async def evaluate(
        self,
        opportunity: TradeOpportunity,
        profitability: ProfitabilityResult,
        portfolio: PortfolioSnapshot,
        *,
        context: RiskContext | None = None,
    ) -> RiskDecision:
        ctx = context or self._context_from_opportunity(opportunity)
        warnings: list[str] = []
        score = Decimal("0")

        # --- Kill switch (hard block on PAUSED / EMERGENCY_STOP) ---
        await self._maybe_activate_kill_switch(portfolio, ctx)
        if not self._kill_switch.allows_new_orders:
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.KILL_SWITCH,
                message=(
                    f"Kill switch state={self._kill_switch.state.value} "
                    f"reason={self._kill_switch.reason}"
                ),
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("100"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Leverage forbidden ---
        leverage = opportunity.metadata.get("leverage")
        if leverage is not None and Decimal(str(leverage)) > 1:
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.LEVERAGE_FORBIDDEN,
                message="Leverage is not allowed in this version",
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("100"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Profitability gate (does not rewrite PnL) ---
        meta = opportunity.metadata or {}
        alphai_inv = bool(meta.get("alphai_inventory_build")) or (
            meta.get("buy_only") and meta.get("alphai_bullish_buy")
        )
        if not alphai_inv and not (profitability.trade_allowed or profitability.is_profitable):
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.NOT_PROFITABLE,
                message="Opportunity is not profitable after costs",
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("40"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )
        if not alphai_inv and profitability.net_profit_usd < self._min_net_profit:
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.NOT_PROFITABLE,
                message=(
                    f"Net profit {profitability.net_profit_usd} below "
                    f"min {self._min_net_profit}"
                ),
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("35"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Exchange health ---
        if not ctx.exchange_healthy:
            await self._kill_switch.pause(
                "Exchange connectivity unhealthy",
                code=RiskRejectReason.EXCHANGE_UNHEALTHY,
            )
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.EXCHANGE_UNHEALTHY,
                message="Exchange unavailable / unhealthy",
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("90"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Stale market data ---
        if (
            ctx.market_data_age_ms is not None
            and ctx.market_data_age_ms > self._max_data_age_ms
        ):
            await self._kill_switch.pause(
                f"Stale market data age_ms={ctx.market_data_age_ms}",
                code=RiskRejectReason.STALE_MARKET_DATA,
            )
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.STALE_MARKET_DATA,
                message=(
                    f"Market data age {ctx.market_data_age_ms}ms exceeds "
                    f"max {self._max_data_age_ms}ms"
                ),
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("85"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Execution latency ---
        if (
            ctx.execution_latency_ms is not None
            and ctx.execution_latency_ms > self._max_latency_ms
        ):
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.EXECUTION_LATENCY,
                message=(
                    f"Execution latency {ctx.execution_latency_ms}ms exceeds "
                    f"max {self._max_latency_ms}ms"
                ),
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("70"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Slippage ---
        if ctx.estimated_slippage_pct > self._max_slippage_pct:
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.EXCESSIVE_SLIPPAGE,
                message=(
                    f"Estimated slippage {ctx.estimated_slippage_pct}% exceeds "
                    f"max {self._max_slippage_pct}%"
                ),
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("65"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Liquidity ---
        if ctx.liquidity_base is not None and ctx.liquidity_base < self._min_liquidity:
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.INSUFFICIENT_LIQUIDITY,
                message=(
                    f"Liquidity {ctx.liquidity_base} below min {self._min_liquidity}"
                ),
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("60"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )
        if (
            ctx.liquidity_base is not None
            and opportunity.quantity > ctx.liquidity_base
        ):
            allowed_qty = ctx.liquidity_base
            if self._allow_partial and self._partial_allowed(opportunity, allowed_qty):
                warnings.append(
                    f"Partial fill: qty capped to liquidity {allowed_qty}"
                )
                return await self._approve_partial(
                    opportunity,
                    portfolio,
                    ctx,
                    allowed_qty=allowed_qty,
                    warnings=warnings,
                    score=score,
                )
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.INSUFFICIENT_LIQUIDITY,
                message=(
                    f"Requested qty {opportunity.quantity} exceeds "
                    f"available liquidity {ctx.liquidity_base}"
                ),
                allowed_qty=ctx.liquidity_base,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("60"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=ctx.liquidity_base * opportunity.entry_price,
            )

        # --- Abnormal price movement ---
        if ctx.reference_price and ctx.current_price and ctx.reference_price > 0:
            move_pct = (
                abs(ctx.current_price - ctx.reference_price)
                / ctx.reference_price
                * _HUNDRED
            )
            if move_pct > self._max_price_move_pct:
                return await self._reject(
                    opportunity,
                    reason_code=RiskRejectReason.ABNORMAL_PRICE_MOVEMENT,
                    message=(
                        f"Abnormal price move {move_pct}% exceeds "
                        f"max {self._max_price_move_pct}%"
                    ),
                    allowed_qty=_ZERO,
                    maximum_loss=_ZERO,
                    warnings=warnings,
                    risk_score=Decimal("80"),
                    requested_size=opportunity.quantity * opportunity.entry_price,
                    allowed_size=_ZERO,
                )

        # --- Daily loss ---
        # Live micro uses exchange-true PnL elsewhere; paper sync can invent
        # huge "daily losses" (e.g. €1 entry fallback) that must not pause trading.
        if bool(getattr(self._settings, "live_micro_ignore_paper_daily_loss", False)):
            daily_loss = _ZERO
            daily_limit = _ZERO
        else:
            daily_loss = max(-portfolio.daily_realized_pnl_usd, _ZERO)
            daily_limit = self._limits.daily_loss_limit(portfolio.equity_usd, self._settings)
        if daily_loss >= daily_limit and daily_limit > 0:
            await self._kill_switch.pause(
                f"Daily loss {daily_loss} reached limit {daily_limit}",
                code=RiskRejectReason.MAX_DAILY_LOSS,
            )
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.MAX_DAILY_LOSS,
                message=f"Daily loss {daily_loss} exceeds limit {daily_limit}",
                allowed_qty=_ZERO,
                maximum_loss=daily_limit,
                warnings=warnings,
                risk_score=Decimal("95"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Drawdown ---
        drawdown = self._limits.drawdown_fraction(portfolio)
        if drawdown >= self._max_drawdown_pct and self._max_drawdown_pct > 0:
            await self._kill_switch.pause(
                f"Drawdown {drawdown} reached max {self._max_drawdown_pct}",
                code=RiskRejectReason.MAX_DRAWDOWN,
            )
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.MAX_DRAWDOWN,
                message=(
                    f"Drawdown {drawdown * _HUNDRED}% exceeds "
                    f"max {self._max_drawdown_pct * _HUNDRED}%"
                ),
                allowed_qty=_ZERO,
                maximum_loss=portfolio.peak_equity * self._max_drawdown_pct,
                warnings=warnings,
                risk_score=Decimal("95"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Simultaneous positions ---
        # Sells / reduce-only must still pass — otherwise inventory can never exit.
        # Buys that ADD to an already-open symbol are allowed at the cap; only a
        # brand-new symbol is blocked (otherwise pre-session bags freeze the book).
        meta = opportunity.metadata or {}
        is_reduce = (
            opportunity.side == OpportunitySide.SELL
            or bool(meta.get("sell_only"))
            or bool(meta.get("reduce_only"))
            or str(meta.get("exit_reason") or "").strip() != ""
        )
        if not is_reduce:
            max_pos = self._limits.max_simultaneous_positions
            if portfolio.open_position_count >= max_pos:
                open_syms = {
                    str(p.symbol or "").upper().replace("/", "").replace("-", "")
                    for p in (portfolio.positions or [])
                    if p.quantity and p.quantity > 0
                }
                opp_sym = str(opportunity.symbol or "").upper().replace("/", "").replace(
                    "-", ""
                )
                if opp_sym not in open_syms:
                    return await self._reject(
                        opportunity,
                        reason_code=RiskRejectReason.MAX_SIMULTANEOUS_POSITIONS,
                        message=(
                            f"Open positions {portfolio.open_position_count} >= "
                            f"max {max_pos}; cannot open new symbol {opp_sym}"
                        ),
                        allowed_qty=_ZERO,
                        maximum_loss=_ZERO,
                        warnings=warnings,
                        risk_score=Decimal("55"),
                        requested_size=opportunity.quantity * opportunity.entry_price,
                        allowed_size=_ZERO,
                    )

        # --- Trades per minute ---
        self._prune_trade_window()
        if len(self._trade_timestamps) >= self._max_trades_per_minute:
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.MAX_TRADES_PER_MINUTE,
                message=(
                    f"Trades in last minute {len(self._trade_timestamps)} >= "
                    f"max {self._max_trades_per_minute}"
                ),
                allowed_qty=_ZERO,
                maximum_loss=_ZERO,
                warnings=warnings,
                risk_score=Decimal("50"),
                requested_size=opportunity.quantity * opportunity.entry_price,
                allowed_size=_ZERO,
            )

        # --- Position / exposure limits ---
        limits = self._limits.evaluate(opportunity, portfolio)
        partial_codes = {"MAX_POSITION_SIZE", "MAX_POSITION_PERCENT", "MAX_TOTAL_EXPOSURE"}
        if set(limits.breached_codes) & partial_codes and self._allow_partial:
            if self._partial_allowed(opportunity, limits.allowed_quantity):
                warnings.append(
                    f"Partial size: {limits.allowed_quantity} of {opportunity.quantity}"
                )
                return await self._approve_partial(
                    opportunity,
                    portfolio,
                    ctx,
                    allowed_qty=limits.allowed_quantity,
                    warnings=warnings,
                    score=score,
                )

        if "MAX_POSITION_SIZE" in limits.breached_codes:
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.MAX_POSITION_SIZE,
                message=(
                    f"Position notional {limits.requested_notional} exceeds "
                    f"max {limits.max_by_absolute}"
                ),
                allowed_qty=limits.allowed_quantity,
                maximum_loss=limits.allowed_notional,
                warnings=warnings,
                risk_score=Decimal("75"),
                requested_size=limits.requested_notional,
                allowed_size=limits.allowed_notional,
            )
        if "MAX_POSITION_PERCENT" in limits.breached_codes:
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.MAX_POSITION_PERCENT,
                message=(
                    f"Position {limits.requested_notional} exceeds "
                    f"{self._settings.max_position_percent}% of portfolio "
                    f"({limits.max_by_percent})"
                ),
                allowed_qty=limits.allowed_quantity,
                maximum_loss=limits.allowed_notional,
                warnings=warnings,
                risk_score=Decimal("75"),
                requested_size=limits.requested_notional,
                allowed_size=limits.allowed_notional,
            )
        if "MAX_TOTAL_EXPOSURE" in limits.breached_codes:
            return await self._reject(
                opportunity,
                reason_code=RiskRejectReason.MAX_TOTAL_EXPOSURE,
                message=(
                    f"Total exposure capacity remaining "
                    f"{limits.remaining_exposure_capacity} below requested "
                    f"{limits.requested_notional}"
                ),
                allowed_qty=limits.allowed_quantity,
                maximum_loss=limits.remaining_exposure_capacity,
                warnings=warnings,
                risk_score=Decimal("75"),
                requested_size=limits.requested_notional,
                allowed_size=limits.allowed_notional,
            )

        # Soft warnings (do not reject)
        if portfolio.equity_usd > 0:
            daily_loss_pct = daily_loss / portfolio.equity_usd * _HUNDRED
            if daily_loss_pct >= self._warn_daily_loss_pct:
                warnings.append(
                    f"Daily loss {daily_loss_pct}% approaching limit"
                )
                await self._kill_switch.warn(
                    warnings[-1],
                    code=RiskRejectReason.MAX_DAILY_LOSS,
                )
                score += Decimal("15")
        if drawdown >= self._warn_drawdown_pct:
            warnings.append(f"Drawdown {drawdown * _HUNDRED}% approaching limit")
            await self._kill_switch.warn(
                warnings[-1],
                code=RiskRejectReason.MAX_DRAWDOWN,
            )
            score += Decimal("15")

        allowed_qty = limits.allowed_quantity
        max_loss = min(limits.allowed_notional, daily_limit)
        score += Decimal(str(min(40, int(ctx.estimated_slippage_pct * 10))))

        self._trade_timestamps.append(time.monotonic())
        return RiskDecision(
            opportunity_id=opportunity.id,
            status=RiskDecisionStatus.APPROVED,
            reasons=["All risk checks passed"],
            max_allowed_quantity=allowed_qty,
            rejection_reason=None,
            risk_score=score,
            position_size_allowed=allowed_qty,
            maximum_loss=max_loss,
            warnings=warnings,
        )

    async def _maybe_activate_kill_switch(
        self,
        portfolio: PortfolioSnapshot,
        ctx: RiskContext,
    ) -> None:
        daily_loss = max(-portfolio.daily_realized_pnl_usd, _ZERO)
        daily_limit = self._limits.daily_loss_limit(portfolio.equity_usd, self._settings)
        drawdown = self._limits.drawdown_fraction(portfolio)

        conditions = {
            "daily_loss_ok": daily_loss < daily_limit if daily_limit > 0 else True,
            "drawdown_ok": drawdown < self._max_drawdown_pct,
            "market_data_fresh": (
                ctx.market_data_age_ms is None
                or ctx.market_data_age_ms <= self._max_data_age_ms
            ),
            "exchange_healthy": ctx.exchange_healthy,
            "execution_stable": self._kill_switch.consecutive_failures
            < self._settings.risk_consecutive_failure_limit,
        }
        self._kill_switch.update_conditions(conditions)

        # Do not auto-resume here — only escalate toward pause/stop.

    def _partial_allowed(self, opportunity: TradeOpportunity, allowed_qty: Decimal) -> bool:
        if allowed_qty <= 0:
            return False
        requested = opportunity.quantity
        if allowed_qty >= requested:
            return False
        min_qty = requested * self._partial_min_pct
        return allowed_qty >= min_qty

    async def _approve_partial(
        self,
        opportunity: TradeOpportunity,
        portfolio: PortfolioSnapshot,
        ctx: RiskContext,
        *,
        allowed_qty: Decimal,
        warnings: list[str],
        score: Decimal,
    ) -> RiskDecision:
        limits = self._limits.evaluate(
            opportunity.model_copy(update={"quantity": allowed_qty}),
            portfolio,
        )
        daily_limit = self._limits.daily_loss_limit(portfolio.equity_usd, self._settings)
        max_loss = min(limits.allowed_notional, daily_limit)
        score += Decimal(str(min(40, int(ctx.estimated_slippage_pct * 10))))
        self._trade_timestamps.append(time.monotonic())
        return RiskDecision(
            opportunity_id=opportunity.id,
            status=RiskDecisionStatus.APPROVED,
            reasons=["Partial approval: size reduced to fit limits"],
            max_allowed_quantity=allowed_qty,
            rejection_reason=None,
            risk_score=score,
            position_size_allowed=allowed_qty,
            maximum_loss=max_loss,
            warnings=warnings,
        )

    async def _reject(
        self,
        opportunity: TradeOpportunity,
        *,
        reason_code: RiskRejectReason,
        message: str,
        allowed_qty: Decimal,
        maximum_loss: Decimal,
        warnings: list[str],
        risk_score: Decimal,
        requested_size: Decimal,
        allowed_size: Decimal,
    ) -> RiskDecision:
        logger.info(
            "RISK_REJECTED reason=%s symbol=%s requested_size=%s allowed_size=%s "
            "message=%s opportunity_id=%s",
            reason_code.value,
            opportunity.symbol,
            f"{requested_size:.2f}",
            f"{allowed_size:.2f}",
            message,
            opportunity.id,
        )
        return RiskDecision(
            opportunity_id=opportunity.id,
            status=RiskDecisionStatus.REJECTED,
            reasons=[message],
            rejection_reason=reason_code.value,
            max_allowed_quantity=allowed_qty if allowed_qty > 0 else None,
            position_size_allowed=allowed_qty if allowed_qty > 0 else Decimal("0"),
            maximum_loss=maximum_loss,
            risk_score=risk_score,
            warnings=warnings,
        )

    def _prune_trade_window(self) -> None:
        cutoff = time.monotonic() - 60.0
        while self._trade_timestamps and self._trade_timestamps[0] < cutoff:
            self._trade_timestamps.popleft()

    @staticmethod
    def _context_from_opportunity(opportunity: TradeOpportunity) -> RiskContext:
        market = opportunity.market
        age_ms: float | None = None
        liquidity: Decimal | None = None
        ref: Decimal | None = None
        current: Decimal | None = None
        if market is not None:
            from datetime import UTC, datetime

            ts = market.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            age_ms = max(0.0, (datetime.now(UTC) - ts).total_seconds() * 1000.0)
            ref = market.mid
            current = market.last
            if market.order_book is not None:
                liquidity = sum(
                    (level.amount for level in market.order_book.asks),
                    Decimal("0"),
                )
            if market.latency_ms is not None:
                pass
        slippage = Decimal(str(opportunity.metadata.get("estimated_slippage_pct", "0")))
        return RiskContext(
            exchange_healthy=bool(opportunity.metadata.get("exchange_healthy", True)),
            market_data_age_ms=age_ms,
            estimated_slippage_pct=slippage,
            execution_latency_ms=(
                float(opportunity.metadata["execution_latency_ms"])
                if "execution_latency_ms" in opportunity.metadata
                else (market.latency_ms if market is not None else None)
            ),
            liquidity_base=liquidity,
            reference_price=ref,
            current_price=current,
        )


# Backward-compatible alias used by older imports / TradingEngine wiring.
DefaultRiskEngine = RiskEngine

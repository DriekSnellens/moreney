"""Common NET economics for all research strategies.

ONE source of truth: NetProfitCalculator + FillEconomics / waterfall shapes.
Strategies may have different gross structures but must not invent optimistic PnL.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import TradeOpportunity
from bot.core.venue_fees import venue_maker_fee, venue_taker_fee
from bot.opportunity.economics import FillEconomics, build_fill_economics
from bot.opportunity.waterfall import expected_waterfall
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.profitability.net_profit import NetProfitCalculator
from bot.strategies.arbitrage import walk_book
from bot.core.exchange_types import OrderBookLevel
from bot.strategy_lab.types import CostBreakdown

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def levels_from_pairs(
    pairs: tuple[tuple[Decimal, Decimal], ...],
) -> list[OrderBookLevel]:
    return [OrderBookLevel(price=p, amount=q) for p, q in pairs]


def executable_vwap(
    side: str,
    *,
    bids: tuple[tuple[Decimal, Decimal], ...],
    asks: tuple[tuple[Decimal, Decimal], ...],
    quantity: Decimal,
) -> tuple[Decimal, Decimal, bool, Decimal]:
    """Executable VWAP from depth. Never uses midpoint when depth exists."""
    if side == "buy":
        if not asks:
            return _ZERO, _ZERO, False, _ZERO
        fill = walk_book(levels_from_pairs(asks), quantity)
    else:
        if not bids:
            return _ZERO, _ZERO, False, _ZERO
        fill = walk_book(levels_from_pairs(bids), quantity)
    return fill.vwap, fill.filled_quantity, fill.sufficient, fill.depth_available


def refuse_midpoint_execution(
    *,
    bids: tuple[tuple[Decimal, Decimal], ...],
    asks: tuple[tuple[Decimal, Decimal], ...],
) -> bool:
    """True when depth exists — caller must not substitute mid."""
    return bool(bids or asks)


class CommonEconomics:
    """Wraps NetProfitCalculator for research adapters."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._engine = DefaultProfitabilityEngine(settings)
        self._calc = NetProfitCalculator(settings)

    @property
    def calculator(self) -> NetProfitCalculator:
        return self._calc

    def estimate_opportunity(
        self,
        opportunity: TradeOpportunity,
        *,
        buy_fee_rate: Decimal | None = None,
        sell_fee_rate: Decimal | None = None,
    ) -> CostBreakdown:
        est = self._engine.estimate_sync(
            opportunity,
            buy_fee_rate=buy_fee_rate,
            sell_fee_rate=sell_fee_rate,
        )
        # Conservative = NET after all modeled costs (already includes buffer).
        return CostBreakdown(
            gross_edge_eur=est.gross_profit,
            fees_eur=est.buy_fee + est.sell_fee,
            slippage_eur=est.slippage,
            adverse_latency_eur=est.execution_buffer,
            funding_eur=est.funding_cost,
            hedge_other_eur=_ZERO,
            net_eur=est.net_profit,
            conservative_net_eur=est.net_profit,
        )

    def from_legs(
        self,
        *,
        quantity: Decimal,
        buy_vwap: Decimal,
        sell_vwap: Decimal,
        buy_fee_rate: Decimal,
        sell_fee_rate: Decimal,
        slippage_eur: Decimal = _ZERO,
        adverse_eur: Decimal = _ZERO,
        funding_eur: Decimal = _ZERO,
        hedge_other_eur: Decimal = _ZERO,
        safety_margin_eur: Decimal = _ZERO,
        buffer_bps: Decimal | None = None,
    ) -> CostBreakdown:
        """Cross-venue / taker-style NET from executable VWAPs (no mid)."""
        if quantity <= 0 or buy_vwap <= 0 or sell_vwap <= 0:
            return CostBreakdown()
        gross = (sell_vwap - buy_vwap) * quantity
        buy_fee = buy_vwap * quantity * buy_fee_rate
        sell_fee = sell_vwap * quantity * sell_fee_rate
        notional = buy_vwap * quantity
        buf_bps = (
            buffer_bps
            if buffer_bps is not None
            else Decimal(str(self._settings.profitability_execution_buffer_bps))
        )
        buffer = notional * buf_bps / _BPS
        fees = buy_fee + sell_fee
        net = (
            gross
            - fees
            - slippage_eur
            - buffer
            - adverse_eur
            - funding_eur
            - hedge_other_eur
            - safety_margin_eur
        )
        return CostBreakdown(
            gross_edge_eur=gross,
            fees_eur=fees,
            slippage_eur=slippage_eur,
            adverse_latency_eur=buffer + adverse_eur,
            funding_eur=funding_eur,
            hedge_other_eur=hedge_other_eur + safety_margin_eur,
            net_eur=net,
            conservative_net_eur=net,
        )

    def waterfall_dict(self, costs: CostBreakdown) -> dict[str, str]:
        wf = expected_waterfall(
            gross=costs.gross_edge_eur,
            buy_fee=costs.fees_eur / 2,
            sell_fee=costs.fees_eur / 2,
            slippage=costs.slippage_eur,
            funding=costs.funding_eur,
            execution_buffer=_ZERO,
            extra_adverse=costs.adverse_latency_eur,
            transfer_fx=costs.hedge_other_eur,
            net=costs.conservative_net_eur,
        )
        return wf.as_dict()

    def maker_fee(self, venue: str | None) -> Decimal:
        return venue_maker_fee(venue)

    def taker_fee(self, venue: str | None) -> Decimal:
        return venue_taker_fee(venue)

    def fill_economics(
        self,
        opportunity: TradeOpportunity,
        *,
        buy_fee_rate: Decimal | None = None,
        sell_fee_rate: Decimal | None = None,
    ) -> FillEconomics:
        from bot.core.models import ProfitabilityResult

        est = self._engine.estimate_sync(
            opportunity, buy_fee_rate=buy_fee_rate, sell_fee_rate=sell_fee_rate
        )
        # Build a minimal ProfitabilityResult-compatible object via engine.evaluate sync path
        result = ProfitabilityResult(
            opportunity_id=opportunity.id,
            gross_profit_usd=est.gross_profit,
            buy_fee_usd=est.buy_fee,
            sell_fee_usd=est.sell_fee,
            fees_usd=est.buy_fee + est.sell_fee,
            slippage_usd=est.slippage,
            funding_usd=est.funding_cost,
            execution_buffer_usd=est.execution_buffer,
            net_profit_usd=est.net_profit,
            net_return=est.net_return,
            is_profitable=est.trade_allowed,
            trade_allowed=est.trade_allowed,
            estimate=est,
            assumptions=est.assumptions,
        )
        return build_fill_economics(opportunity, result)


def draft_opportunity(
    *,
    strategy_name: str,
    symbol: str,
    side: OpportunitySide,
    quantity: Decimal,
    entry_price: Decimal,
    exit_price: Decimal,
    entry_role: FeeRole = FeeRole.TAKER,
    exit_role: FeeRole = FeeRole.TAKER,
    funding_periods: Decimal = _ZERO,
    metadata: dict[str, Any] | None = None,
) -> TradeOpportunity:
    return TradeOpportunity(
        strategy_name=strategy_name,
        symbol=symbol,
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        expected_exit_price=exit_price,
        entry_fee_role=entry_role,
        exit_fee_role=exit_role,
        funding_periods=funding_periods,
        metadata=metadata or {},
    )

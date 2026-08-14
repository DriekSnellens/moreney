"""Cross-exchange transfer and stranded-capital cost model."""

from __future__ import annotations

from decimal import Decimal

from bot.core.config import Settings
from bot.core.models import TradeOpportunity

_BPS = Decimal("10000")


class CrossExchangeTransferCost:
    """Models withdrawal/deposit/latency costs for cross-venue arb."""

    def __init__(self, settings: Settings) -> None:
        self._fee_bps = Decimal(str(getattr(settings, "global_transfer_fee_bps", 10) or 10))
        self._latency_penalty_bps = Decimal(
            str(getattr(settings, "global_transfer_latency_bps", 5) or 5)
        )

    def estimate(self, opportunity: TradeOpportunity) -> Decimal:
        meta = opportunity.metadata or {}
        buy = str(meta.get("buy_exchange") or "")
        sell = str(meta.get("sell_exchange") or "")
        if not buy or not sell or buy == sell:
            return Decimal("0")
        if opportunity.strategy_name not in {
            "cross_exchange_arbitrage",
            "triangle_bridge",
            "desk_composite",
            "global_composite",
        }:
            return Decimal("0")
        notional = opportunity.quantity * opportunity.entry_price
        if notional <= 0:
            return Decimal("0")
        return notional * (self._fee_bps + self._latency_penalty_bps) / _BPS

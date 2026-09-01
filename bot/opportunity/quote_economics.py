"""Canonical quote vs fill economics separation.

QuoteEconomics  — known at decision time (no fill conditioning)
RouteBelief     — empirical / prior beliefs about execution
ExecutionEconomics — decision-stage combination:

    NET_IF_FILL = deterministic_NET − E[extra adverse | fill, context]
    EV_PER_QUOTE = P(fill | context) × NET_IF_FILL
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from bot.core.enums import FillType
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.opportunity.economics import FillEconomics, build_fill_economics

_ZERO = Decimal("0")
_ONE = Decimal("1")
_BPS = Decimal("10000")


class QuoteEconomics(BaseModel):
    """Ex-ante deterministic economics (no future / fill conditioning)."""

    gross_eur: Decimal = _ZERO
    buy_fee_eur: Decimal = _ZERO
    sell_fee_eur: Decimal = _ZERO
    deterministic_slippage_eur: Decimal = _ZERO
    funding_eur: Decimal = _ZERO
    extra_cost_eur: Decimal = _ZERO
    base_execution_buffer_eur: Decimal = _ZERO
    deterministic_net_eur: Decimal = _ZERO
    capital_locked_eur: Decimal = _ZERO
    expected_capital_lock_seconds: Decimal = _ZERO
    inventory_relief_eur: Decimal = _ZERO

    def as_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.model_dump().items()}


class RouteBelief(BaseModel):
    """Beliefs about execution quality for a route / fill context."""

    p_fill: Decimal = _ONE
    expected_adverse_bps_if_fill: Decimal = _ZERO
    fill_type: FillType = FillType.UNKNOWN
    raw_capture: Decimal | None = None
    shrunk_capture: Decimal = _ONE
    sample_count: int = 0
    toxicity_bps: Decimal | None = None
    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "p_fill": str(self.p_fill),
            "expected_adverse_bps_if_fill": str(self.expected_adverse_bps_if_fill),
            "fill_type": self.fill_type.value,
            "raw_capture": str(self.raw_capture) if self.raw_capture is not None else None,
            "shrunk_capture": str(self.shrunk_capture),
            "sample_count": self.sample_count,
            "toxicity_bps": str(self.toxicity_bps) if self.toxicity_bps is not None else None,
            "confidence": self.confidence,
            "notes": list(self.notes),
        }


class ExecutionEconomics(BaseModel):
    """Decision-stage combination of quote economics and route belief."""

    quote: QuoteEconomics
    belief: RouteBelief
    net_if_fill_eur: Decimal = _ZERO
    ev_per_quote_eur: Decimal = _ZERO
    fill_conditioned: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "quote": self.quote.as_dict(),
            "belief": self.belief.as_dict(),
            "net_if_fill_eur": str(self.net_if_fill_eur),
            "ev_per_quote_eur": str(self.ev_per_quote_eur),
            "fill_conditioned": self.fill_conditioned,
        }


def quote_economics_from_profitability(
    opportunity: TradeOpportunity,
    profitability: ProfitabilityResult,
    *,
    fill_economics: FillEconomics | None = None,
) -> QuoteEconomics:
    """Lift deterministic numbers from profitability (+ optional FillEconomics)."""
    eco = fill_economics or build_fill_economics(opportunity, profitability)
    fees = profitability.fees_usd
    # Fee split unknown at this layer → attribute all to buy for auditability.
    return QuoteEconomics(
        gross_eur=profitability.gross_profit_usd,
        buy_fee_eur=fees,
        sell_fee_eur=_ZERO,
        deterministic_slippage_eur=profitability.slippage_usd,
        funding_eur=profitability.funding_usd,
        extra_cost_eur=eco.extra_cost_eur,
        base_execution_buffer_eur=profitability.execution_buffer_usd,
        deterministic_net_eur=profitability.net_profit_usd,
        capital_locked_eur=eco.capital_required_eur,
        expected_capital_lock_seconds=eco.expected_capital_time,
        inventory_relief_eur=eco.inventory_relief_eur,
    )


def combine_execution_economics(
    quote: QuoteEconomics,
    belief: RouteBelief,
    *,
    already_buffered_bps: Decimal,
    notional_eur: Decimal,
    regime_weight: Decimal = _ONE,
    transfer_cost: Decimal = _ZERO,
) -> ExecutionEconomics:
    """EV_PER_QUOTE = P(fill) × NET_IF_FILL (fill-conditioned when applicable)."""
    fill_conditioned = belief.fill_type == FillType.TRADE_THROUGH
    extra_bps = _ZERO
    if fill_conditioned and notional_eur > 0:
        extra_bps = max(_ZERO, belief.expected_adverse_bps_if_fill - already_buffered_bps)
    net_if_fill = quote.deterministic_net_eur - (notional_eur * extra_bps / _BPS)
    # Inventory relief may improve an already-positive NET, never rescue ≤0.
    if quote.deterministic_net_eur > 0 and quote.inventory_relief_eur > 0:
        net_if_fill = net_if_fill + quote.inventory_relief_eur
    if quote.deterministic_net_eur <= 0:
        net_if_fill = min(net_if_fill, quote.deterministic_net_eur)
    ev = (belief.p_fill * net_if_fill - transfer_cost) * regime_weight
    return ExecutionEconomics(
        quote=quote,
        belief=belief,
        net_if_fill_eur=net_if_fill,
        ev_per_quote_eur=ev,
        fill_conditioned=fill_conditioned,
    )


QUOTE_AGE_BUCKETS_MS: tuple[tuple[str, float | None, float | None], ...] = (
    ("0_250ms", None, 250.0),
    ("250ms_1s", 250.0, 1000.0),
    ("1s_4s", 1000.0, 4000.0),
    ("4s_10s", 4000.0, 10000.0),
    ("10s_60s", 10000.0, 60000.0),
    ("60s_plus", 60000.0, None),
)


def quote_age_bucket(age_ms: float | None) -> str:
    if age_ms is None or age_ms < 0:
        return "unknown"
    for name, lo, hi in QUOTE_AGE_BUCKETS_MS:
        if lo is not None and age_ms < lo:
            continue
        if hi is not None and age_ms >= hi:
            continue
        return name
    return "unknown"

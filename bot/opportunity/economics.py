"""Per-fill NET economics used for ranking and attribution.

Primary question: which available fill has the best expected NET euro
per bound euro and per unit time?
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from bot.core.enums import MarketRegime
from bot.core.models import ProfitabilityResult, TradeOpportunity

_ZERO = Decimal("0")
_BPS = Decimal("10000")
_ONE = Decimal("1")

# Discrete regime haircuts for required adverse edge. Not fitted continuously.
_REGIME_ADVERSE_MULT: dict[MarketRegime, Decimal] = {
    MarketRegime.LOW_VOLATILITY: Decimal("0.90"),
    MarketRegime.NORMAL: Decimal("1.00"),
    MarketRegime.RANGE_BOUND: Decimal("1.00"),
    MarketRegime.HIGH_VOLATILITY: Decimal("1.30"),
    MarketRegime.MOMENTUM: Decimal("1.20"),
    MarketRegime.LIQUIDITY_STRESSED: Decimal("1.80"),
    MarketRegime.RISK_OFF: Decimal("1.40"),
}


class FillEconomics(BaseModel):
    """Complete expected NET breakdown for one candidate fill."""

    gross_edge_eur: Decimal = _ZERO
    expected_fee_eur: Decimal = _ZERO
    expected_slippage_eur: Decimal = _ZERO
    expected_adverse_selection_eur: Decimal = _ZERO
    expected_inventory_cost_eur: Decimal = _ZERO
    expected_execution_cost_eur: Decimal = _ZERO
    expected_net_eur: Decimal = _ZERO
    expected_net_bps: Decimal = _ZERO
    capital_required_eur: Decimal = _ZERO
    expected_capital_time: Decimal = _ZERO
    expected_net_eur_per_capital_second: Decimal = _ZERO
    inventory_relief_eur: Decimal = _ZERO
    extra_cost_eur: Decimal = _ZERO

    def as_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.model_dump().items()}


def build_fill_economics(
    opportunity: TradeOpportunity,
    profitability: ProfitabilityResult,
    *,
    regime: MarketRegime = MarketRegime.NORMAL,
    transfer_cost: Decimal = _ZERO,
    venue_adverse_bps: Decimal | None = None,
    quote_max_age_ms: float = 2500.0,
) -> FillEconomics:
    """Build NET economics from already-computed profitability (no look-ahead)."""
    meta = opportunity.metadata or {}
    notional = opportunity.quantity * opportunity.entry_price
    if notional <= 0:
        return FillEconomics()

    gross = profitability.gross_profit_usd
    fees = profitability.fees_usd
    slippage = profitability.slippage_usd
    buffer = profitability.execution_buffer_usd
    extra = _d(meta.get("extra_cost_eur", meta.get("expected_fx_cost_eur", 0)))

    raw_adverse_bps = _d(meta.get("adverse_bps", 0))
    if venue_adverse_bps is not None and venue_adverse_bps > raw_adverse_bps:
        raw_adverse_bps = venue_adverse_bps
    regime_mult = _REGIME_ADVERSE_MULT.get(regime, _ONE)
    adverse_bps = raw_adverse_bps * regime_mult
    # Buffer already contains a global adverse haircut; only charge the *extra*
    # venue/regime component so the same risk is not subtracted twice.
    extra_adverse_bps = max(_ZERO, adverse_bps - raw_adverse_bps)
    extra_adverse = notional * extra_adverse_bps / _BPS

    relief = _inventory_relief(meta, profitability.net_profit_usd)
    # Inventory relief may improve ranking of a positive-NET quote that also
    # unloads stock, but must never flip a losing trade to positive.
    inventory_cost = -relief

    execution_cost = buffer + transfer_cost + extra
    net = (
        gross
        - fees
        - slippage
        - profitability.funding_usd
        - execution_cost
        - extra_adverse
        + relief
    )
    if profitability.net_profit_usd <= 0:
        net = min(net, profitability.net_profit_usd - extra_adverse - extra)

    net_bps = net / notional * _BPS if notional > 0 else _ZERO
    capital_time = _capital_time_seconds(meta, quote_max_age_ms)
    denom = notional * capital_time
    velocity = net / denom if denom > 0 else _ZERO

    return FillEconomics(
        gross_edge_eur=gross,
        expected_fee_eur=fees,
        expected_slippage_eur=slippage,
        expected_adverse_selection_eur=extra_adverse + buffer,
        expected_inventory_cost_eur=inventory_cost,
        expected_execution_cost_eur=execution_cost,
        expected_net_eur=net,
        expected_net_bps=net_bps,
        capital_required_eur=notional,
        expected_capital_time=capital_time,
        expected_net_eur_per_capital_second=velocity,
        inventory_relief_eur=relief,
        extra_cost_eur=extra,
    )


def _inventory_relief(meta: dict[str, Any], raw_net: Decimal) -> Decimal:
    """Positive euro value of reducing overweight inventory.

    Capped at 50% of raw NET and zero when raw NET is not strictly positive.
    """
    if raw_net <= 0:
        return _ZERO
    raw_score = _d(meta.get("inventory_skew_score", 0))
    if raw_score <= 0:
        return _ZERO
    # Skew score is notional-like (coins*price + cash). Convert to a small
    # euro bonus: 1% of 1% of that notional, then cap.
    relief = raw_score * Decimal("0.0001")
    cap = raw_net * Decimal("0.5")
    return min(max(_ZERO, relief), cap)


def _capital_time_seconds(meta: dict[str, Any], quote_max_age_ms: float) -> Decimal:
    if meta.get("post_only") or meta.get("triangle"):
        ms = _d(meta.get("quote_max_age_ms", quote_max_age_ms))
        return max(Decimal("0.2"), ms / Decimal("1000"))
    # Taker / arb: capital is bound for the two-leg latency window.
    return Decimal("2")


def _d(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return _ZERO
    try:
        return Decimal(str(value))
    except Exception:
        return _ZERO

"""Hedge helpers — FULLY_HEDGED default; never assume hedge succeeds."""

from __future__ import annotations

from bot.opportunity.lead_lag.states import LeadLagState
from bot.opportunity.lead_lag.types import HedgeLeg, LeadLagOpportunity


def require_feasible_hedge(opportunity: LeadLagOpportunity) -> LeadLagOpportunity:
    """Reject opportunities that claim FULLY_HEDGED without a feasible hedge leg."""
    if opportunity.hedge_mode != "FULLY_HEDGED":
        return opportunity
    if opportunity.hedge is None or not opportunity.hedge.feasible:
        return LeadLagOpportunity(
            signal=opportunity.signal,
            entry_side=opportunity.entry_side,
            entry_venue=opportunity.entry_venue,
            executable_entry_price=opportunity.executable_entry_price,
            executable_quantity=opportunity.executable_quantity,
            hedge=opportunity.hedge,
            gross_predicted_edge_eur=opportunity.gross_predicted_edge_eur,
            fees_eur=opportunity.fees_eur,
            slippage_eur=opportunity.slippage_eur,
            latency_haircut_eur=opportunity.latency_haircut_eur,
            hedge_haircut_eur=opportunity.hedge_haircut_eur,
            other_costs_eur=opportunity.other_costs_eur,
            expected_net_eur=opportunity.expected_net_eur,
            conservative_net_eur=opportunity.conservative_net_eur,
            capital_required_eur=opportunity.capital_required_eur,
            estimated_capital_lock_ms=opportunity.estimated_capital_lock_ms,
            hedge_mode=opportunity.hedge_mode,
            state=LeadLagState.HEDGE_UNAVAILABLE.value,
            first_gate="hedge_unavailable",
            latency_scenario_ms=opportunity.latency_scenario_ms,
            observational=True,
        )
    return opportunity


def hedge_leg_ok(hedge: HedgeLeg | None) -> bool:
    return hedge is not None and hedge.feasible and hedge.executable_price > 0

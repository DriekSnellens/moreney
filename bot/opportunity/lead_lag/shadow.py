"""Shadow admission — never alters production execution or PnL."""

from __future__ import annotations

from typing import Any

from bot.opportunity.lead_lag.states import LeadLagState
from bot.opportunity.lead_lag.types import LeadLagOpportunity


def shadow_admit(opportunity: LeadLagOpportunity) -> dict[str, Any]:
    """Admit only when state is SHADOW_ADMITTED and conservative_net > 0."""
    accepted = (
        opportunity.state == LeadLagState.SHADOW_ADMITTED.value
        and opportunity.conservative_net_eur > 0
    )
    return {
        "accept": accepted,
        "state": opportunity.state,
        "first_gate": opportunity.first_gate,
        "conservative_net_eur": str(opportunity.conservative_net_eur),
        "expected_net_eur": str(opportunity.expected_net_eur),
        "observational": True,
        "alters_execution": False,
        "production_pnl_impact": False,
        "label": "SHADOW_COUNTERFACTUAL",
    }


def execution_allowed(*, lead_lag_execution_enabled: bool, shadow_only: bool) -> bool:
    """Phase D gate — default false."""
    if shadow_only:
        return False
    return bool(lead_lag_execution_enabled)

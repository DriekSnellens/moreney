"""Lead-lag hedged dislocation research (shadow-only).

Production PnL / fills / fees are untouched.
Default: LEAD_LAG_EXECUTION_ENABLED=false.
"""

from bot.opportunity.lead_lag.types import (
    LeadLagObservation,
    LeadLagOpportunity,
    LeadLagOutcome,
    LeadLagSignal,
)

__all__ = [
    "LeadLagObservation",
    "LeadLagSignal",
    "LeadLagOpportunity",
    "LeadLagOutcome",
]

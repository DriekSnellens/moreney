"""Pre-trade toxicity model (shadow mode).

Predicts E(adverse | fill, state) before quote admission.
Does not change fills, fees, thresholds, or live execution.
"""

from bot.opportunity.toxicity.types import (
    PreTradeFeatures,
    ShadowDecision,
    ToxicityPrediction,
)
from bot.opportunity.toxicity.shrinkage import HierarchicalToxicityModel

__all__ = [
    "PreTradeFeatures",
    "ShadowDecision",
    "ToxicityPrediction",
    "HierarchicalToxicityModel",
]

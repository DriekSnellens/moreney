"""Fill mechanism sensitivity lab (experimental / observational).

Production PnL remains TRADE_THROUGH_ONLY.
Alternative models never alter live execution.
"""

from bot.opportunity.fill_lab.events import FillEvent, QuoteEvent
from bot.opportunity.fill_lab.models import FillModelId
from bot.opportunity.fill_lab.runner import run_fill_mechanism_study
from bot.opportunity.fill_lab.study import build_study

__all__ = [
    "QuoteEvent",
    "FillEvent",
    "FillModelId",
    "build_study",
    "run_fill_mechanism_study",
]

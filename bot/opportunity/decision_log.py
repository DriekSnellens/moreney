"""Structured opportunity decision logging."""

from __future__ import annotations

import logging
from collections import deque

from bot.core.enums import OpportunityDecisionAction
from bot.opportunity.models import OpportunityDecision, ScoredOpportunity

logger = logging.getLogger(__name__)


class OpportunityDecisionLogger:
    """Ring buffer + structured logs for every opportunity decision."""

    def __init__(self, *, max_entries: int = 500) -> None:
        self._entries: deque[OpportunityDecision] = deque(maxlen=max_entries)

    def log(
        self,
        scored: ScoredOpportunity,
        *,
        action: OpportunityDecisionAction,
        reason: str,
        stage: str,
        portfolio_exposure: dict | None = None,
    ) -> OpportunityDecision:
        decision = OpportunityDecision.from_scored(
            scored,
            action=action,
            reason=reason,
            stage=stage,
            portfolio_exposure=portfolio_exposure,
        )
        self._entries.append(decision)
        logger.info(
            "OPPORTUNITY_DECISION action=%s stage=%s strategy=%s symbol=%s "
            "ev=%s score=%s net=%s p_win=%s reason=%s",
            action.value,
            stage,
            decision.strategy,
            decision.symbol,
            decision.expected_value,
            decision.score,
            decision.expected_net_return,
            decision.probability_profit,
            reason[:120],
        )
        return decision

    def recent(self, limit: int = 50) -> list[OpportunityDecision]:
        return list(self._entries)[-limit:]

    def export(self) -> list[dict]:
        return [d.model_dump(mode="json") for d in self._entries]

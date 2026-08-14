"""Rank opportunities by risk-adjusted expected value."""

from __future__ import annotations

from decimal import Decimal

from bot.core.config import Settings
from bot.opportunity.models import ScoredOpportunity

_ZERO = Decimal("0")


class OpportunityRanker:
    """Sort scored opportunities: higher EV and execution quality first."""

    def __init__(self, settings: Settings) -> None:
        self._min_ev = Decimal(str(getattr(settings, "opportunity_min_expected_value", 0)))
        self._min_score = Decimal(str(getattr(settings, "opportunity_min_score", 0)))

    def rank(self, candidates: list[ScoredOpportunity]) -> list[ScoredOpportunity]:
        filtered = [
            c
            for c in candidates
            if c.expected_value >= self._min_ev and c.score >= self._min_score
        ]
        ordered = sorted(
            filtered,
            key=lambda c: (
                c.score,
                c.expected_value,
                Decimal(str(c.execution_quality)),
            ),
            reverse=True,
        )
        for idx, item in enumerate(ordered, start=1):
            item.rank = idx
        return ordered

    @staticmethod
    def compute_score(scored: ScoredOpportunity) -> Decimal:
        """Composite score: EV × regime × liquidity × execution quality."""
        ev = scored.expected_value
        if ev <= 0:
            return _ZERO
        liq = Decimal(str(max(0.1, min(1.0, scored.liquidity_score))))
        exec_q = Decimal(str(max(0.1, min(1.0, scored.execution_quality))))
        rr_bonus = min(scored.risk_reward, Decimal("5")) / Decimal("5")
        return ev * scored.regime_weight * liq * exec_q * (Decimal("1") + rr_bonus * Decimal("0.1"))

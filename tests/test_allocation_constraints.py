"""Allocation constraint tests."""

from __future__ import annotations

from decimal import Decimal

from bot.intelligence.dynamic_capital_allocator import (
    CandidateConstraints,
    DynamicCapitalAllocatorConfig,
    allocate_portfolio_dynamic,
)
from bot.strategies.opportunity_engine import OpportunityAssessment, OpportunityDecision, VolatilityRegime

_ONE = Decimal("1")


def _assessment(symbol: str, capital: Decimal = Decimal("500")) -> OpportunityAssessment:
    return OpportunityAssessment(
        symbol=symbol,
        venue="bitvavo",
        direction="buy",
        expected_net_profit_eur=Decimal("2"),
        expected_net_profit_pct=Decimal("0.008"),
        expected_hold_seconds=Decimal("600"),
        expected_net_eur_per_hour=Decimal("12"),
        expected_net_eur_per_capital_hour=Decimal("0.005"),
        momentum_score=Decimal("70"),
        continuity_score=Decimal("0.6"),
        volatility_regime=VolatilityRegime.NORMAL,
        extension_pct=Decimal("0.01"),
        headroom_pct=Decimal("0.02"),
        headroom_score=Decimal("70"),
        capital_required_eur=capital,
        recommended_size_multiplier=_ONE,
        opportunity_score=Decimal("80"),
        decision=OpportunityDecision.HIGH_QUALITY,
        fill_probability=Decimal("0.7"),
    )


def test_symbol_cap_enforced() -> None:
    a = _assessment("ARBEUR")

    def constraints_for(_: OpportunityAssessment) -> CandidateConstraints:
        return CandidateConstraints(
            strategy_size_eur=Decimal("500"),
            risk_size_eur=Decimal("500"),
            venue_limit_eur=Decimal("2000"),
            symbol_limit_eur=Decimal("300"),
            sector_limit_eur=Decimal("600"),
        )

    selected, _ = allocate_portfolio_dynamic(
        [a],
        deployable_capital_eur=Decimal("2000"),
        constraints_for=constraints_for,
        config=DynamicCapitalAllocatorConfig(allocation_multiplier=_ONE),
    )
    _, r = selected[0]
    assert r.allocated_eur <= Decimal("300")

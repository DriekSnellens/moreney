"""Tests for dynamic capital allocator."""

from __future__ import annotations

from decimal import Decimal

from bot.intelligence.dynamic_capital_allocator import (
    AllocationDecision,
    CapitalReservationStore,
    DynamicCapitalAllocatorConfig,
    allocate_portfolio_dynamic,
    apply_dynamic_allocation_to_assessment,
    compute_capital_velocity,
    compute_score_components,
    run_portfolio_allocation,
)
from bot.strategies.opportunity_engine import (
    OpportunityAssessment,
    OpportunityDecision,
    VolatilityRegime,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _assessment(
    symbol: str = "ARBEUR",
    *,
    score: Decimal = Decimal("80"),
    net: Decimal = Decimal("2.0"),
    capital: Decimal = Decimal("400"),
    hold_sec: Decimal = Decimal("600"),
    mult: Decimal = _ONE,
    decision: OpportunityDecision = OpportunityDecision.HIGH_QUALITY,
    fill_prob: Decimal = Decimal("0.72"),
) -> OpportunityAssessment:
    vel = compute_capital_velocity(
        expected_net=net, capital_eur=capital, expected_hold_seconds=hold_sec
    )
    return OpportunityAssessment(
        symbol=symbol,
        venue="bitvavo",
        direction="buy",
        expected_net_profit_eur=net,
        expected_net_profit_pct=Decimal("0.008"),
        expected_hold_seconds=hold_sec,
        expected_net_eur_per_hour=net / (hold_sec / Decimal("3600")),
        expected_net_eur_per_capital_hour=vel,
        momentum_score=Decimal("70"),
        continuity_score=Decimal("0.6"),
        volatility_regime=VolatilityRegime.NORMAL,
        extension_pct=Decimal("0.01"),
        headroom_pct=Decimal("0.02"),
        headroom_score=Decimal("70"),
        capital_required_eur=capital,
        recommended_size_multiplier=mult,
        opportunity_score=score,
        decision=decision,
        fill_probability=fill_prob,
        liquidity_score=Decimal("0.85"),
    )


class TestDynamicCapitalAllocator:
    def test_zero_capital(self) -> None:
        selected, skipped = allocate_portfolio_dynamic(
            [_assessment()],
            deployable_capital_eur=_ZERO,
        )
        assert selected == []

    def test_one_opportunity(self) -> None:
        selected, _ = allocate_portfolio_dynamic(
            [_assessment()],
            deployable_capital_eur=Decimal("500"),
            config=DynamicCapitalAllocatorConfig(allocation_multiplier=_ONE),
        )
        assert len(selected) == 1
        _, result = selected[0]
        assert result.allocated_eur > 0
        assert result.allocated_eur <= Decimal("400")

    def test_many_opportunities(self) -> None:
        assessments = [_assessment(f"SYM{i}EUR", score=Decimal(str(70 + i))) for i in range(5)]
        selected, _ = allocate_portfolio_dynamic(
            assessments,
            deployable_capital_eur=Decimal("1500"),
            config=DynamicCapitalAllocatorConfig(allocation_multiplier=_ONE),
        )
        total = sum(r.allocated_eur for _, r in selected)
        assert total <= Decimal("1500")
        assert len(selected) >= 2

    def test_all_bad_opportunities(self) -> None:
        bad = _assessment(decision=OpportunityDecision.REJECT, score=Decimal("30"))
        selected, skipped = allocate_portfolio_dynamic(
            [bad],
            deployable_capital_eur=Decimal("1000"),
        )
        assert selected == []
        assert bad in skipped

    def test_never_exceeds_strategy_size(self) -> None:
        a = _assessment(capital=Decimal("200"), mult=Decimal("0.5"))
        selected, _ = allocate_portfolio_dynamic(
            [a],
            deployable_capital_eur=Decimal("1000"),
            config=DynamicCapitalAllocatorConfig(allocation_multiplier=_ONE),
        )
        _, r = selected[0]
        assert r.allocated_eur <= Decimal("100")

    def test_apply_downward_only(self) -> None:
        a = _assessment(mult=_ONE)
        from bot.intelligence.dynamic_capital_allocator import AllocationResult

        alloc = AllocationResult(
            symbol="ARBEUR",
            venue="bitvavo",
            requested_eur=Decimal("400"),
            allocated_eur=Decimal("200"),
            baseline_eur=Decimal("400"),
            decision=AllocationDecision.REDUCED,
            reason="reserve",
            capital_score=Decimal("0.8"),
            components=compute_score_components(a, config=DynamicCapitalAllocatorConfig()),
            constraints_applied=("reserve",),
            explanation="test",
        )
        out = apply_dynamic_allocation_to_assessment(a, alloc)
        assert out.recommended_size_multiplier <= _ONE
        assert out.recommended_size_multiplier == Decimal("0.5")

    def test_no_lookahead_score_uses_only_assessment_fields(self) -> None:
        a = _assessment()
        comp = compute_score_components(a, config=DynamicCapitalAllocatorConfig())
        assert comp.expected_net == Decimal("2.0")
        assert comp.execution_probability == Decimal("0.72")


class TestPortfolioRun:
    def test_run_portfolio_allocation(self) -> None:
        snap = run_portfolio_allocation(
            [_assessment(), _assessment("APTEUR")],
            total_equity_eur=Decimal("2000"),
            free_eur=Decimal("1800"),
        )
        assert snap.deployable_capital_eur >= _ZERO
        assert snap.reserve_mode.value in {"DEFENSIVE", "NORMAL", "OPPORTUNITY_BURST"}


class TestReservationStore:
    def test_reservation_expiry(self) -> None:
        store = CapitalReservationStore()
        store.reserve(symbol="ARB", venue="bitvavo", amount_eur=Decimal("100"), ttl_seconds=0.01)
        import time

        time.sleep(0.02)
        assert store.reserved_total() == _ZERO

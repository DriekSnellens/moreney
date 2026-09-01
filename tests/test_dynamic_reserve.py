"""Dynamic reserve mode tests."""

from __future__ import annotations

from decimal import Decimal

from bot.intelligence.dynamic_capital_allocator import (
    DynamicCapitalAllocatorConfig,
    ReserveMode,
    compute_deployable_capital,
    determine_reserve_mode,
)


def test_defensive_on_dead_market() -> None:
    mode = determine_reserve_mode(
        high_quality_count=5,
        is_dead_market=True,
        is_opportunity_burst=False,
        config=DynamicCapitalAllocatorConfig(),
    )
    assert mode == ReserveMode.DEFENSIVE


def test_burst_on_many_high_quality() -> None:
    mode = determine_reserve_mode(
        high_quality_count=10,
        is_dead_market=False,
        is_opportunity_burst=True,
        config=DynamicCapitalAllocatorConfig(scarcity_medium_max=7),
    )
    assert mode == ReserveMode.OPPORTUNITY_BURST


def test_deployable_respects_underwater() -> None:
    _, mode, reserve, deployable = compute_deployable_capital(
        total_equity_eur=Decimal("2000"),
        free_eur=Decimal("1500"),
        locked_notional_eur=Decimal("200"),
        underwater_capital_eur=Decimal("300"),
        resting_reserved_eur=Decimal("100"),
        high_quality_count=3,
        is_dead_market=False,
        is_opportunity_burst=False,
    )
    assert mode in ReserveMode
    assert reserve >= Decimal("0")
    assert deployable <= Decimal("1500")

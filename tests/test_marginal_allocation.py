"""Marginal allocation diminishing returns tests."""

from __future__ import annotations

from decimal import Decimal

from bot.intelligence.dynamic_capital_allocator import (
    DynamicCapitalAllocatorConfig,
    compute_marginal_allocation_factor,
)


def test_marginal_factor_decreases_with_size() -> None:
    cfg = DynamicCapitalAllocatorConfig(marginal_decay_per_100_eur=Decimal("0.05"))
    f100 = compute_marginal_allocation_factor(Decimal("100"), cfg)
    f200 = compute_marginal_allocation_factor(Decimal("200"), cfg)
    f500 = compute_marginal_allocation_factor(Decimal("500"), cfg)
    assert f100 == Decimal("1")
    assert f200 < f100
    assert f500 < f200
    assert f500 >= Decimal("0.40")

"""Capital velocity metric tests."""

from __future__ import annotations

from decimal import Decimal

from bot.intelligence.dynamic_capital_allocator import compute_capital_velocity


def test_fast_trade_higher_velocity() -> None:
    vel_a = compute_capital_velocity(
        expected_net=Decimal("2.0"),
        capital_eur=Decimal("400"),
        expected_hold_seconds=Decimal("600"),
    )
    vel_b = compute_capital_velocity(
        expected_net=Decimal("3.0"),
        capital_eur=Decimal("400"),
        expected_hold_seconds=Decimal("3600"),
    )
    assert vel_a is not None and vel_b is not None
    assert vel_a > vel_b


def test_zero_inputs_return_none() -> None:
    assert (
        compute_capital_velocity(
            expected_net=Decimal("0"),
            capital_eur=Decimal("400"),
            expected_hold_seconds=Decimal("600"),
        )
        is None
    )

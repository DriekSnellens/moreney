"""Capital intelligence AlphaI soft fusion tests."""

from __future__ import annotations

from decimal import Decimal

from bot.intelligence.capital_intelligence import assess_capital_state


def test_alphai_macro_increases_reserve() -> None:
    base = assess_capital_state(
        total_budget_eur=Decimal("2000"),
        deployed_eur=Decimal("0"),
        locked_eur=Decimal("0"),
        avg_opportunity_score=Decimal("70"),
    )
    macro = assess_capital_state(
        total_budget_eur=Decimal("2000"),
        deployed_eur=Decimal("0"),
        locked_eur=Decimal("0"),
        avg_opportunity_score=Decimal("70"),
        alphai_macro_active=True,
    )
    assert macro.reserve_need_pct >= base.reserve_need_pct
    assert "alphai_defensive_reserve" in macro.reasons


def test_alphai_burst_can_reduce_reserve() -> None:
    out = assess_capital_state(
        total_budget_eur=Decimal("2000"),
        deployed_eur=Decimal("0"),
        locked_eur=Decimal("0"),
        avg_opportunity_score=Decimal("80"),
        is_opportunity_burst=True,
        alphai_bullish_cluster=True,
    )
    assert "alphai_burst_deploy" in out.reasons or "opportunity_burst" in out.reasons

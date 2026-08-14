"""Capacity model: retail maker path to €300/day on €25k."""

from decimal import Decimal

from bot.paper.capacity import (
    TARGET_DAILY_EUR,
    hits_daily_target,
    project_daily_pnl,
    scale_through_fill,
)


def test_observed_realistic_fills_hit_300_with_price_priority_through() -> None:
    """Two live Realistic fills were NET+ after retail fees.

    Window ~5 minutes, €0.8888 realized, through fill was 20% of size.
    Price-priority through (100% when the book prints through the resting
    quote) scales that window above the €300/day target. Queue fills stay 0.
    """
    realized = Decimal("0.4191514955876406760427879") + Decimal(
        "0.4696583563774650197910971"
    )
    window_s = 5 * 60
    haircut = project_daily_pnl(realized, window_s)
    full = scale_through_fill(
        haircut, from_pct=Decimal("0.20"), to_pct=Decimal("1.0")
    )
    assert haircut > 0
    assert full > haircut
    assert hits_daily_target(full)
    assert full >= TARGET_DAILY_EUR


def test_capacity_zero_window() -> None:
    assert project_daily_pnl(Decimal("10"), 0) == 0

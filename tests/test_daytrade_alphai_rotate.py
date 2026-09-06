"""Daytrade path: free non-AlphaI bags so the AlphaI satellite can rotate."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from bot.live.capital_playbook import CapitalPlaybook, PLAYBOOK_OVERLAYS
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor


def test_flat_playbook_recycles_non_alphai_faster_for_daytrade() -> None:
    flat = PLAYBOOK_OVERLAYS[CapitalPlaybook.FLAT]
    assert flat["uw_non_alphai_min_age_sec"] <= 300.0
    assert flat["uw_near_min_age_sec"] <= 180.0
    assert flat["be_harvest_min_gain_pct"] <= 0.004


def test_daytrade_rotate_requires_capital_split_and_pressure() -> None:
    bridge = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    bridge._capital_split_enabled = False
    bridge._capital_playbook = CapitalPlaybook.FLAT
    bridge._sleeve_deploy_targets = lambda top_n=2: ["ETH", "UNI"]  # type: ignore[method-assign]
    assert bridge._daytrade_rotate_non_picks() is False

    bridge._capital_split_enabled = True
    assert bridge._daytrade_rotate_non_picks() is True

    bridge._capital_playbook = CapitalPlaybook.TREND
    bridge._sleeve_deploy_targets = lambda top_n=2: []  # type: ignore[method-assign]
    assert bridge._daytrade_rotate_non_picks() is False

    bridge._sleeve_deploy_targets = lambda top_n=2: ["ETH"]  # type: ignore[method-assign]
    assert bridge._daytrade_rotate_non_picks() is True


def test_non_pick_harvest_scale_shrinks_under_daytrade_pressure() -> None:
    bridge = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    bridge._capital_split_enabled = True
    bridge._capital_playbook = CapitalPlaybook.FLAT
    bridge._sleeve_deploy_targets = lambda top_n=2: ["ETH"]  # type: ignore[method-assign]
    bridge._alphai_bullish_buy = lambda base: str(base).upper() == "ETH"  # type: ignore[method-assign]
    bridge._alphai_feature_for = lambda base: SimpleNamespace(  # type: ignore[method-assign]
        be_harvest_gain_scale=Decimal("1.0")
    )
    bridge._desk_lessons = SimpleNamespace(
        applied_feedback=lambda: SimpleNamespace(harvest_floor_scale=1.0)
    )

    assert bridge._alphai_be_harvest_gain_scale("XRP") == Decimal("0.40")
    assert bridge._alphai_be_harvest_gain_scale("ETH") == Decimal("1.0")

"""Daytrade path: free non-AlphaI bags so the AlphaI satellite can rotate."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from bot.live.capital_playbook import CapitalPlaybook, PLAYBOOK_OVERLAYS
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor


def test_flat_playbook_respects_fee_floor_for_daytrade() -> None:
    # 14d forensics: sub-1% harvests and <10-min recycles were pure fee bleed.
    flat = PLAYBOOK_OVERLAYS[CapitalPlaybook.FLAT]
    assert flat["uw_non_alphai_min_age_sec"] >= 600.0
    assert flat["uw_near_min_age_sec"] >= 600.0
    assert flat["be_harvest_min_gain_pct"] >= 0.010


def test_daytrade_rotate_requires_capital_split() -> None:
    bridge = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    bridge._capital_split_enabled = False
    bridge._alphai_daytrader_enabled = False
    bridge._capital_playbook = CapitalPlaybook.FLAT
    bridge._sleeve_deploy_targets = lambda top_n=2: ["ETH", "UNI"]  # type: ignore[method-assign]
    assert bridge._daytrade_rotate_non_picks() is False

    bridge._capital_split_enabled = True
    assert bridge._daytrade_rotate_non_picks() is True


def test_daytrader_mode_always_rotates_non_picks_under_split() -> None:
    """Sharp AlphaI daytrader: rotate non-picks even on TREND with empty targets."""
    bridge = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    bridge._capital_split_enabled = True
    bridge._alphai_daytrader_enabled = True
    bridge._capital_playbook = CapitalPlaybook.TREND
    bridge._sleeve_deploy_targets = lambda top_n=2: []  # type: ignore[method-assign]
    assert bridge._daytrade_rotate_non_picks() is True


def test_without_daytrader_still_needs_flat_or_targets() -> None:
    bridge = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    bridge._capital_split_enabled = True
    bridge._alphai_daytrader_enabled = False
    bridge._capital_playbook = CapitalPlaybook.TREND
    bridge._sleeve_deploy_targets = lambda top_n=2: []  # type: ignore[method-assign]
    assert bridge._daytrade_rotate_non_picks() is False

    bridge._sleeve_deploy_targets = lambda top_n=2: ["ETH"]  # type: ignore[method-assign]
    assert bridge._daytrade_rotate_non_picks() is True

    bridge._sleeve_deploy_targets = lambda top_n=2: []  # type: ignore[method-assign]
    bridge._capital_playbook = CapitalPlaybook.FLAT
    assert bridge._daytrade_rotate_non_picks() is True


def test_non_pick_harvest_scale_shrinks_under_daytrade_pressure() -> None:
    bridge = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    bridge._capital_split_enabled = True
    bridge._alphai_daytrader_enabled = True
    bridge._capital_playbook = CapitalPlaybook.TREND
    bridge._sleeve_deploy_targets = lambda top_n=2: []  # type: ignore[method-assign]
    bridge._alphai_bullish_buy = lambda base: str(base).upper() == "ETH"  # type: ignore[method-assign]
    bridge._alphai_feature_for = lambda base: SimpleNamespace(  # type: ignore[method-assign]
        be_harvest_gain_scale=Decimal("1.0")
    )
    bridge._desk_lessons = SimpleNamespace(
        applied_feedback=lambda: SimpleNamespace(harvest_floor_scale=1.0)
    )

    assert bridge._alphai_be_harvest_gain_scale("XRP") == Decimal("0.40")
    assert bridge._alphai_be_harvest_gain_scale("ETH") == Decimal("1.0")


def test_session_enables_alphai_daytrader_with_capital_split(tmp_path) -> None:
    from bot.core.config import Settings
    from bot.live.micro_session import _session_settings

    cfg = _session_settings(
        Settings(live_micro_execute_venues="bitvavo,okx"),
        budget_eur=Decimal("2000"),
        symbols=["SOLEUR"],
        persist_path=tmp_path / "state.json",
    )
    assert cfg.live_micro_capital_split_enabled is True
    assert cfg.live_micro_alphai_daytrader_enabled is True
    assert cfg.live_micro_daytrader_min_confirm_scale >= 0.55
    assert cfg.live_micro_daytrader_non_alphai_min_age_sec <= 120.0
    assert cfg.live_micro_daytrader_sleeve_min_confirm_scale >= 0.55
    assert cfg.live_micro_daytrader_min_conviction >= 0.25
    assert cfg.live_micro_daytrader_sleeve_urgency_enabled is False
    assert cfg.alphai_require_bullish_new_buys is True
    assert cfg.alphai_intraday_gate_enabled is True
    assert cfg.alphai_price_confirm_enabled is True
    assert cfg.live_micro_entry_quality_min_score >= 65.0

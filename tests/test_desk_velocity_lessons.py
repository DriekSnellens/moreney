"""Desk velocity lessons: avoid recycle, provisional BE exit, sleeve capacity, spike filter."""

from __future__ import annotations

import time
from decimal import Decimal
from types import SimpleNamespace

import bot.live.dashboard_history as dash_hist
from bot.live.capital_playbook import PLAYBOOK_OVERLAYS, CapitalPlaybook
from bot.live.dashboard_history import _filter_portfolio_mtm_spike
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor


class _AvoidSig:
    avoid_bases = frozenset({"ETH", "XRP"})
    blocked_bases = frozenset()
    bullish_bases = frozenset({"BNB", "ADA"})
    daily_pick_bases = frozenset({"BNB", "ADA", "AVAX"})
    daily_pick_scores = {"BNB": 100.0, "ADA": 90.0, "AVAX": 80.0}

    def is_bearish(self, base: str) -> bool:
        return str(base).upper() in self.avoid_bases

    def exit_urgency(self, base: str) -> bool:
        return self.is_bearish(base)

    def is_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return str(base).upper() in self.bullish_bases

    def is_slot_priority_buy(self, base: str, *, top_n: int = 2) -> bool:
        ranked = sorted(
            self.daily_pick_scores, key=self.daily_pick_scores.get, reverse=True
        )
        return str(base).upper() in set(ranked[:top_n])

    def unheld_priority_buys(self, held, *, top_n: int = 2):
        held_u = {str(b).upper() for b in held}
        ranked = sorted(
            self.daily_pick_scores, key=self.daily_pick_scores.get, reverse=True
        )
        return frozenset(b for b in ranked[:top_n] if b not in held_u)


def _bridge() -> MicroBudgetLiveExecutor:
    b = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    b._alphai_signals = _AvoidSig()
    b._settings = SimpleNamespace(
        alphai_bullish_buy_enabled=True,
        alphai_require_bullish_new_buys=False,
        live_micro_provisional_be_exit_min_age_sec=1800.0,
    )
    b._trusted_cost_keys = set()
    b._cost_lots = {"bitvavo:ETH": [[Decimal("0.1"), Decimal("2000")]]}
    b._position_opened_at = {}
    b._quote = "EUR"
    b._execute_venues = ("bitvavo",)
    b._long_hold_bases = frozenset()
    b._uw_recycle_enabled = True
    b._sleeve_paused = False
    b._daily_kill_active = False
    b._uw_dust_max_notional = Decimal("25")
    b._uw_dust_below_be_pct = Decimal("0.003")
    b._uw_idle_pressure_enabled = True
    b._uw_idle_min_free_eur = Decimal("150")
    b._uw_idle_min_age_sec = 600.0
    b._uw_idle_below_be_pct = Decimal("0.004")
    b._uw_non_alphai_below_be_pct = Decimal("0.01")
    b._uw_non_alphai_min_age_sec = 3600.0
    b._uw_avoid_max_age_sec = 900.0
    b._uw_alphai_below_be_pct = Decimal("0.02")
    b._uw_alphai_min_age_sec = 10800.0
    b._uw_near_below_be_pct = Decimal("0.008")
    b._uw_near_max_depth_pct = Decimal("0.015")
    b._uw_near_min_age_sec = 2700.0
    b._momentum_enabled = True
    return b


def test_avoid_base_is_exit_urgent() -> None:
    b = _bridge()
    assert b._alphai_is_avoid_base("ETH") is True
    assert b._alphai_is_avoid_base("BNB") is False
    assert b._alphai_exit_urgency("ETH") is True


def test_avoid_uw_recycle_fires_faster_than_non_alphai() -> None:
    b = _bridge()
    b._position_age_sec = lambda venue, base: 950.0  # type: ignore[method-assign]
    b._unit_cost = lambda venue, base: Decimal("2000")  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: False  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_weak_bullish_hold = lambda base: False  # type: ignore[method-assign]
    b._alphai_hold_conviction = lambda base: 0.0  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._venue_budget_remaining = lambda venue: Decimal("500")  # type: ignore[method-assign]
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="ETH",
        symbol="ETHEUR",
        mark=Decimal("1980"),
        be=Decimal("2000"),
        notional=Decimal("200"),
    )
    assert plan is not None
    assert str(plan[0]).startswith("avoid_")


def test_provisional_be_exit_for_aged_avoid() -> None:
    b = _bridge()
    b._has_trusted_cost = lambda venue, base: False  # type: ignore[method-assign]
    b._unit_cost = lambda venue, base: Decimal("1.2")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 700.0  # type: ignore[method-assign]
    assert b._provisional_be_exit_allowed("bitvavo", "ETH") is True
    b._position_age_sec = lambda venue, base: 100.0  # type: ignore[method-assign]
    assert b._provisional_be_exit_allowed("bitvavo", "ETH") is False


def test_adverse_keeps_sleeve_capacity_from_parent() -> None:
    adverse = PLAYBOOK_OVERLAYS[CapitalPlaybook.ADVERSE]
    assert adverse["active_ring_eur"] >= 1200.0
    assert adverse["alphai_idle_deploy_blocked"] is False
    assert adverse["block_new_buys"] is True


def test_spike_filter_clamps_glitch_with_flat_cash() -> None:
    dash_hist._last_good_portfolio_eur = Decimal("4070")
    dash_hist._last_good_free_eur = Decimal("3400")
    dash_hist._last_good_realized_eur = Decimal("-12")
    point = {
        "portfolio_eur": "4139",
        "free_eur": "3400.5",
        "realized_pnl_eur": "-12.0",
    }
    out = _filter_portfolio_mtm_spike(point)
    assert out is not None
    assert out["portfolio_eur"] == "4070"
    assert out.get("spike_filtered") == "1"


def test_spike_filter_allows_real_move_with_cash_change() -> None:
    dash_hist._last_good_portfolio_eur = Decimal("4070")
    dash_hist._last_good_free_eur = Decimal("3400")
    dash_hist._last_good_realized_eur = Decimal("-12")
    point = {
        "portfolio_eur": "4150",
        "free_eur": "3200",
        "realized_pnl_eur": "-12.0",
    }
    out = _filter_portfolio_mtm_spike(point)
    assert out is not None
    assert out["portfolio_eur"] == "4150"
    assert "spike_filtered" not in out

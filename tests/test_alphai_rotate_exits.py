"""AlphaI daytrader rotate exits: avoid/non-pick free under BE (not BE-wait)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor


class _Sig:
    def __init__(self) -> None:
        self.avoid_bases = frozenset({"ETH", "XRP"})
        self.bullish_bases = frozenset({"SOL"})
        self.daily_pick_bases = frozenset({"SOL"})
        self.daily_pick_scores = {"SOL": 15.0, "ETH": -21.0, "XRP": -21.0}

    def is_bearish(self, base: str) -> bool:
        return str(base).upper() in self.avoid_bases

    def is_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return str(base).upper() in self.bullish_bases

    def is_strong_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return False

    def is_weak_bullish_hold(self, base: str) -> bool:
        return False

    def pick_conviction(self, base: str) -> float:
        return 0.19 if str(base).upper() == "SOL" else 0.0

    def is_price_lagging(self, base: str) -> bool:
        return str(base).upper() in {"SOL", "NEAR"}

    def price_confirm_scale(self, base: str) -> float:
        return {"SOL": 0.32, "NEAR": 0.0, "ETH": 0.0}.get(str(base).upper(), 1.0)

    def exit_urgency(self, base: str) -> bool:
        return self.is_bearish(base) or str(base).upper() not in self.bullish_bases


def _bridge() -> MicroBudgetLiveExecutor:
    b = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    b._uw_recycle_enabled = True
    b._sleeve_paused = False
    b._daily_kill_active = False
    b._is_long_hold = lambda base: False  # type: ignore[method-assign]
    b._unit_cost = lambda v, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda v, base: 600.0  # type: ignore[method-assign]
    b._alphai_signals = _Sig()
    b._settings = SimpleNamespace(
        alphai_bullish_buy_enabled=True,
        alphai_require_bullish_new_buys=True,
    )
    b._alphai_bullish_buy = lambda base: str(base).upper() == "SOL"  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_weak_bullish_hold = lambda base: False  # type: ignore[method-assign]
    b._alphai_hold_conviction = (  # type: ignore[method-assign]
        lambda base: 0.19 if str(base).upper() == "SOL" else 0.0
    )
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._uw_dust_max_notional = Decimal("0")
    b._uw_dust_below_be_pct = Decimal("0.003")
    b._uw_non_alphai_below_be_pct = Decimal("0.006")
    b._uw_non_alphai_min_age_sec = 300.0
    b._uw_avoid_max_age_sec = 900.0
    b._uw_alphai_below_be_pct = Decimal("0.012")
    b._uw_alphai_min_age_sec = 1800.0
    b._uw_idle_pressure_enabled = False
    b._uw_idle_min_age_sec = 180.0
    b._uw_idle_below_be_pct = Decimal("0.003")
    b._uw_idle_min_free_eur = Decimal("100")
    b._venue_budget_remaining = lambda v: Decimal("1500")  # type: ignore[method-assign]
    b._sleeve_has_unheld_priority = lambda: False  # type: ignore[method-assign]
    b._desk_lesson_avoid_age_scale = lambda: 1.0  # type: ignore[method-assign]
    b._uw_deadlock_unlock_enabled = False
    b._uw_deadlock_min_age_sec = 180.0
    b._uw_deadlock_below_be_pct = Decimal("0.0025")
    b._capital_deadlocked = lambda v: False  # type: ignore[method-assign]
    b._uw_deadlock_would_buy_gate = True
    b._uw_would_buy_today = lambda base, symbol: False  # type: ignore[method-assign]
    b._uw_deadlock_day_loss_remaining = lambda: Decimal("12")  # type: ignore[method-assign]
    b._alphai_sleeve_priority_buy = lambda base, top_n=2: False  # type: ignore[method-assign]
    b._uw_mid_flat_recycle_enabled = False
    b._uw_mid_flat_min_age_sec = 120.0
    b._uw_near_below_be_pct = Decimal("0.003")
    b._uw_mid_flat_max_depth_pct = Decimal("0.015")
    b._sleeve_deploy_targets = lambda top_n=2: []  # type: ignore[method-assign]
    b._sleeve_held_fills_slot = lambda base: False  # type: ignore[method-assign]
    b._uw_lag_time_partial_enabled = False
    b._uw_lag_time_partial_min_age_sec = 600.0
    b._uw_lag_time_partial_max_depth_pct = Decimal("0.02")
    b._cut_loss_below_be_pct = Decimal("0.025")
    b._daytrade_rotate_non_picks = lambda: True  # type: ignore[method-assign]
    b._alphai_daytrader_enabled = True
    b._daytrader_rotate_exits_enabled = True
    b._daytrader_avoid_below_be_pct = Decimal("0.0025")
    b._daytrader_avoid_min_age_sec = 60.0
    b._daytrader_non_alphai_min_age_sec = 120.0
    b._daytrader_non_alphai_below_be_pct = Decimal("0.005")
    b._daytrader_near_min_age_sec = 90.0
    b._daytrader_weak_alphai_min_age_sec = 480.0
    b._daytrader_lag_time_min_age_sec = 600.0
    b._daytrader_min_conviction = 0.25
    b._daytrader_sleeve_min_confirm = Decimal("0.55")
    b._uw_near_min_age_sec = 90.0
    b._uw_near_max_depth_pct = Decimal("0.012")
    b._alphai_is_avoid_base = (  # type: ignore[method-assign]
        MicroBudgetLiveExecutor._alphai_is_avoid_base.__get__(b)
    )
    b._alphai_daytrader_rotate_exit = (  # type: ignore[method-assign]
        MicroBudgetLiveExecutor._alphai_daytrader_rotate_exit.__get__(b)
    )
    return b


def test_rotate_exit_flags_avoid_and_non_pick() -> None:
    b = _bridge()
    assert b._alphai_daytrader_rotate_exit("ETH") is True
    assert b._alphai_daytrader_rotate_exit("NEAR") is True
    assert b._alphai_daytrader_rotate_exit("SOL") is True  # weak confirm/conviction
    b._alphai_signals = _Sig()
    b._alphai_signals.bullish_bases = frozenset({"SOL", "ADA"})  # type: ignore[misc]
    b._alphai_bullish_buy = lambda base: str(base).upper() in {"SOL", "ADA"}  # type: ignore[method-assign]
    b._alphai_hold_conviction = lambda base: 1.0  # type: ignore[method-assign]
    b._alphai_signals.price_confirm_scale = lambda base: 1.0  # type: ignore[method-assign]
    b._alphai_signals.is_price_lagging = lambda base: False  # type: ignore[method-assign]
    assert b._alphai_daytrader_rotate_exit("ADA") is False


def test_avoid_deep_fires_at_shallow_daytrader_depth() -> None:
    b = _bridge()
    be = Decimal("100")
    mark = Decimal("99.70")  # -0.30% — below old 0.6% floor, above new 0.25%
    plan = b._uw_recycle_plan(
        venue="okx",
        base="ETH",
        symbol="ETHEUR",
        mark=mark,
        be=be,
        notional=Decimal("270"),
    )
    assert plan is not None
    assert plan[0] == "avoid_deep"
    assert plan[1] == "stop"


def test_non_pick_rotate_deep_under_daytrader() -> None:
    b = _bridge()
    be = Decimal("2.10")
    mark = Decimal("2.09")  # ~-0.48%
    plan = b._uw_recycle_plan(
        venue="okx",
        base="NEAR",
        symbol="NEAREUR",
        mark=mark,
        be=be,
        notional=Decimal("100"),
    )
    assert plan is not None
    assert str(plan[0]).startswith("rotate_non_pick")


def test_early_cut_eligible_for_legacy_avoid_bag() -> None:
    b = _bridge()
    b._early_cut_loss_below_be_pct = Decimal("0.008")
    b._early_cut_new_bases_only = True
    b._has_trusted_cost = lambda v, base: True  # type: ignore[method-assign]
    st = {"new_session_base": False}
    assert b._early_cut_eligible(st, venue="okx", base="ETH") is True


def test_session_enables_rotate_exits(tmp_path) -> None:
    from bot.core.config import Settings
    from bot.live.micro_session import _session_settings

    cfg = _session_settings(
        Settings(live_micro_execute_venues="bitvavo,okx"),
        budget_eur=Decimal("2000"),
        symbols=["ETHEUR"],
        persist_path=tmp_path / "state.json",
    )
    assert cfg.live_micro_daytrader_rotate_exits_enabled is True
    assert cfg.live_micro_daytrader_avoid_below_be_pct <= 0.003
    assert cfg.live_micro_daytrader_avoid_min_age_sec <= 60.0

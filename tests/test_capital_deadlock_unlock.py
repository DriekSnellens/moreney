"""Capital deadlock unlock: recycle UW bags when the active ring is starved."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor


class _SleeveSig:
    avoid_bases = frozenset({"ETH"})
    blocked_bases = frozenset()
    bullish_bases = frozenset({"BNB", "ADA"})
    daily_pick_bases = frozenset({"BNB", "ADA"})
    daily_pick_scores = {"BNB": 100.0, "ADA": 90.0}

    def is_bearish(self, base: str) -> bool:
        return str(base).upper() in self.avoid_bases

    def is_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return str(base).upper() in self.bullish_bases

    def is_strong_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return False

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
    b._alphai_signals = _SleeveSig()
    b._settings = SimpleNamespace(
        alphai_bullish_buy_enabled=True,
        alphai_require_bullish_new_buys=False,
    )
    b._execute_venues = ("bitvavo",)
    b._long_hold_bases = frozenset()
    b._uw_recycle_enabled = True
    b._sleeve_paused = False
    b._daily_kill_active = False
    b._uw_dust_max_notional = Decimal("0")
    b._uw_dust_below_be_pct = Decimal("0.003")
    b._uw_idle_pressure_enabled = True
    b._uw_idle_min_free_eur = Decimal("150")
    b._uw_idle_min_age_sec = 600.0
    b._uw_idle_below_be_pct = Decimal("0.004")
    b._uw_deadlock_unlock_enabled = True
    b._uw_deadlock_below_be_pct = Decimal("0.0025")
    b._uw_deadlock_min_age_sec = 300.0
    b._uw_deadlock_partial_enabled = True
    b._uw_deadlock_target_free_eur = Decimal("220")
    b._uw_deadlock_partial_clip_eur = Decimal("220")
    b._uw_deadlock_partial_min_eur = Decimal("40")
    b._uw_deadlock_day_loss_cap_eur = Decimal("15")
    b._uw_deadlock_would_buy_gate = True
    b._uw_deadlock_day_key = ""
    b._uw_deadlock_day_loss_eur = Decimal("0")
    b._uw_deadlock_unlock_remaining_eur = Decimal("220")
    b._alphai_priority_clip_eur = Decimal("220")
    b._alphai_strong_clip_eur = Decimal("0")
    b._uw_mid_flat_recycle_enabled = True
    b._uw_mid_flat_max_depth_pct = Decimal("0.012")
    b._uw_mid_flat_min_age_sec = 600.0
    b._uw_lag_time_partial_enabled = True
    b._uw_lag_time_partial_min_age_sec = 1800.0
    b._uw_lag_time_partial_max_depth_pct = Decimal("0.020")
    b._cut_loss_below_be_pct = Decimal("0.025")
    b._uw_non_alphai_below_be_pct = Decimal("0.01")
    b._uw_non_alphai_min_age_sec = 3600.0
    b._uw_avoid_max_age_sec = 900.0
    b._uw_alphai_below_be_pct = Decimal("0.02")
    b._uw_alphai_min_age_sec = 10800.0
    b._uw_near_below_be_pct = Decimal("0.008")
    b._uw_near_max_depth_pct = Decimal("0.015")
    b._uw_near_min_age_sec = 2700.0
    b._active_ring_eur = Decimal("1850")
    b._ring_soft_block_underwater_eur = Decimal("25")
    b._momentum_enabled = True
    b._desk_lessons_min_free_eur = 150.0
    return b


def test_capital_deadlocked_when_uw_vault_starves_ring() -> None:
    b = _bridge()
    b._underwater_book_notional = lambda venue: Decimal("280")  # type: ignore[method-assign]
    b._active_book_notional = lambda venue: Decimal("0")  # type: ignore[method-assign]
    # Free cash low on the bag venue — capital is locked IN the UW vault.
    b._venue_budget_remaining = lambda venue: Decimal("20")  # type: ignore[method-assign]
    b._sleeve_has_unheld_priority = lambda top_n=2: True  # type: ignore[method-assign]
    assert b._capital_deadlocked("bitvavo") is True


def test_deadlock_unlock_recycles_mild_uw_without_free_cash() -> None:
    b = _bridge()
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 400.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: False  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._underwater_book_notional = lambda venue: Decimal("280")  # type: ignore[method-assign]
    b._active_book_notional = lambda venue: Decimal("0")  # type: ignore[method-assign]
    b._venue_budget_remaining = lambda venue: Decimal("10")  # type: ignore[method-assign]
    b._sleeve_has_unheld_priority = lambda top_n=2: True  # type: ignore[method-assign]
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="SOL",
        symbol="SOLEUR",
        mark=Decimal("99.70"),  # -0.30% — below old idle threshold, above dust
        be=Decimal("100"),
        notional=Decimal("280"),
    )
    assert plan is not None
    assert str(plan[0]).startswith("deadlock_")


def test_idle_pressure_fires_when_uw_locks_ring() -> None:
    """Idle pressure must unlock even when same-venue free cash is low."""
    b = _bridge()
    b._uw_deadlock_unlock_enabled = False  # isolate idle-pressure path
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 700.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: False  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._underwater_book_notional = lambda venue: Decimal("280")  # type: ignore[method-assign]
    b._active_book_notional = lambda venue: Decimal("0")  # type: ignore[method-assign]
    b._venue_budget_remaining = lambda venue: Decimal("5")  # type: ignore[method-assign]
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="SOL",
        symbol="SOLEUR",
        mark=Decimal("99.50"),  # -0.50% >= idle 0.40%
        be=Decimal("100"),
        notional=Decimal("280"),
    )
    assert plan is not None
    assert plan[0] == "idle_pressure"


def test_soft_momentum_desk_bias_no_nameerror() -> None:
    b = _bridge()
    b._ring_needs_deploy = lambda venue: False  # type: ignore[method-assign]
    b._desk_lesson_deploy_bias = lambda: Decimal("1.20")  # type: ignore[method-assign]
    b._sleeve_has_unheld_priority = lambda top_n=2: True  # type: ignore[method-assign]
    b._venue_budget_remaining = lambda venue: Decimal("500")  # type: ignore[method-assign]
    b._ring_util_b_ignore_underwater = True
    b._ring_soft_max_active_eur = Decimal("0")
    b._underwater_book_notional = lambda venue: Decimal("0")  # type: ignore[method-assign]
    b._active_book_notional = lambda venue: Decimal("0")  # type: ignore[method-assign]
    assert b._ring_soft_momentum_eligible("bitvavo") is True


def test_deadlock_overrides_strong_hold_when_sleeve_not_rising() -> None:
    """SOL-class strong holds must still unlock when capital is deadlocked."""
    b = _bridge()
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 400.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: True  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: True  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._alphai_sleeve_priority_buy = lambda base, top_n=2: True  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._underwater_book_notional = lambda venue: Decimal("280")  # type: ignore[method-assign]
    b._active_book_notional = lambda venue: Decimal("0")  # type: ignore[method-assign]
    b._venue_budget_remaining = lambda venue: Decimal("10")  # type: ignore[method-assign]
    b._sleeve_has_unheld_priority = lambda top_n=2: True  # type: ignore[method-assign]
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="SOL",
        symbol="SOLEUR",
        mark=Decimal("99.70"),
        be=Decimal("100"),
        notional=Decimal("280"),
    )
    assert plan is not None
    assert str(plan[0]).startswith("deadlock_")


def test_recycle_priority_prefers_avoid_over_sleeve() -> None:
    b = _bridge()
    b._alphai_is_avoid_base = lambda base: str(base).upper() == "ETH"  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: str(base).upper() in {"BNB", "ADA"}  # type: ignore[method-assign]
    b._alphai_weak_bullish_hold = lambda base: False  # type: ignore[method-assign]
    b._alphai_sleeve_priority_buy = lambda base, top_n=2: str(base).upper() in {"BNB", "ADA"}  # type: ignore[method-assign]
    assert b._uw_recycle_priority("ETH") < b._uw_recycle_priority("SOL")
    assert b._uw_recycle_priority("SOL") < b._uw_recycle_priority("BNB")


def test_playbook_overlay_tightens_deadlock_age() -> None:
    b = _bridge()
    b._playbook_baselines = {
        "uw_deadlock_unlock_enabled": True,
        "uw_deadlock_min_age_sec": 300.0,
        "uw_deadlock_below_be_pct": Decimal("0.0025"),
        "active_ring_eur": b._active_ring_eur,
        "ring_soft_max_active_eur": Decimal("1850"),
        "winner_add_enabled": True,
        "alphai_strong_clip_eur": Decimal("220"),
        "exit_taker_cushion_bps": Decimal("5"),
        "exit_taker_after_maker_fails": 1,
        "be_harvest_min_gain_pct": Decimal("0.004"),
        "be_harvest_partial_pct": Decimal("0.5"),
        "uw_near_min_age_sec": 2700.0,
        "uw_non_alphai_min_age_sec": 3600.0,
        "uw_idle_min_age_sec": 600.0,
        "uw_idle_below_be_pct": Decimal("0.004"),
        "uw_near_below_be_pct": Decimal("0.008"),
        "uw_near_max_depth_pct": Decimal("0.015"),
        "uw_alphai_below_be_pct": Decimal("0.02"),
        "uw_alphai_min_age_sec": 10800.0,
        "early_cut_loss_below_be_pct": Decimal("0.01"),
        "trail_hold_rising_n": 3,
        "alphai_intraday_min_freshness": Decimal("0.4"),
        "alphai_intraday_require_rising": False,
        "alphai_cross_venue_deploy": True,
        "alphai_idle_deploy_blocked": False,
    }
    b._playbook_owns_buy_block = False
    b._daily_kill_active = False
    b._buys_blocked = False
    b._buys_blocked_new_bases_only = False
    b.set_buys_blocked = lambda *a, **k: None  # type: ignore[method-assign]
    b._apply_capital_playbook_overlays(
        {"uw_deadlock_min_age_sec": 180.0, "uw_deadlock_below_be_pct": 0.002}
    )
    assert b._uw_deadlock_min_age_sec == 180.0
    assert b._uw_deadlock_below_be_pct == Decimal("0.002")



def test_partial_unlock_sizes_to_sleeve_clip() -> None:
    b = _bridge()
    b._uw_deadlock_unlock_remaining_eur = Decimal("220")
    b._uw_deadlock_day_loss_eur = Decimal("0")
    qty = b._uw_deadlock_partial_sell_qty(
        free_qty=Decimal("10"),  # 10 * 99.7 ~= 997 EUR bag
        mark=Decimal("99.7"),
        be=Decimal("100"),
        session_cap=Decimal("10"),
    )
    # ~220 EUR / 99.7 ≈ 2.2066
    assert qty > 0
    assert qty < Decimal("10")
    assert abs(qty * Decimal("99.7") - Decimal("220")) < Decimal("1")


def test_partial_unlock_blocked_when_day_loss_cap_spent() -> None:
    import datetime as dt
    b = _bridge()
    b._uw_deadlock_day_key = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    b._uw_deadlock_day_loss_eur = Decimal("15")
    b._uw_deadlock_unlock_remaining_eur = Decimal("220")
    qty = b._uw_deadlock_partial_sell_qty(
        free_qty=Decimal("10"),
        mark=Decimal("99.7"),
        be=Decimal("100"),
        session_cap=Decimal("10"),
    )
    assert qty == Decimal("0")


def test_would_buy_today_nurses_rising_sleeve() -> None:
    b = _bridge()
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._alphai_sleeve_priority_buy = lambda base, top_n=2: True  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: True  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: False  # type: ignore[method-assign]
    assert b._uw_would_buy_today("BNB", "BNBEUR") is True
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    assert b._uw_would_buy_today("BNB", "BNBEUR") is False


def test_deadlock_plan_skips_when_would_buy_today() -> None:
    b = _bridge()
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 400.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: True  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._alphai_sleeve_priority_buy = lambda base, top_n=2: True  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: False  # type: ignore[method-assign]  # rising
    b._underwater_book_notional = lambda venue: Decimal("280")  # type: ignore[method-assign]
    b._active_book_notional = lambda venue: Decimal("0")  # type: ignore[method-assign]
    b._venue_budget_remaining = lambda venue: Decimal("10")  # type: ignore[method-assign]
    b._sleeve_has_unheld_priority = lambda top_n=2: True  # type: ignore[method-assign]
    b._uw_dust_max_notional = Decimal("0")
    # Rising sleeve would-buy → no deadlock tier
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="BNB",
        symbol="BNBEUR",
        mark=Decimal("99.70"),
        be=Decimal("100"),
        notional=Decimal("280"),
    )
    assert plan is None or not str(plan[0]).startswith("deadlock_")


def test_playbook_has_partial_unlock_overlays() -> None:
    from bot.live.capital_playbook import PLAYBOOK_OVERLAYS, PRE_CRASH_FLAT_OVERLAYS, CapitalPlaybook

    flat = PLAYBOOK_OVERLAYS[CapitalPlaybook.FLAT]
    assert flat["uw_deadlock_target_free_eur"] == 220.0
    assert flat["uw_deadlock_day_loss_cap_eur"] == 12.0
    assert PRE_CRASH_FLAT_OVERLAYS["uw_deadlock_day_loss_cap_eur"] == 10.0



def test_mid_flat_recycle_for_non_would_buy() -> None:
    b = _bridge()
    b._uw_mid_flat_recycle_enabled = True
    b._uw_mid_flat_max_depth_pct = Decimal("0.012")
    b._uw_mid_flat_min_age_sec = 600.0
    b._uw_near_below_be_pct = Decimal("0.004")
    b._uw_near_max_depth_pct = Decimal("0.006")
    b._uw_near_min_age_sec = 2700.0
    b._uw_deadlock_unlock_enabled = False
    b._uw_idle_pressure_enabled = False
    b._uw_dust_max_notional = Decimal("0")
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 900.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: False  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._uw_would_buy_today = lambda base, symbol: False  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="UNI",
        symbol="UNIEUR",
        mark=Decimal("99.10"),  # -0.90% mid-depth
        be=Decimal("100"),
        notional=Decimal("200"),
    )
    assert plan is not None
    assert plan[0] == "mid_flat"


def test_mid_flat_partial_rotates_when_free_cash_already_high() -> None:
    """Opportunity-cost: mid-flat must still clip when unlock_remaining is 0."""
    b = _bridge()
    b._uw_deadlock_partial_enabled = True
    b._uw_deadlock_unlock_remaining_eur = Decimal("0")
    b._uw_deadlock_partial_clip_eur = Decimal("220")
    b._uw_deadlock_target_free_eur = Decimal("220")
    b._uw_deadlock_partial_min_eur = Decimal("40")
    b._uw_deadlock_day_loss_remaining = lambda: Decimal("15")  # type: ignore[method-assign]
    qty = b._uw_deadlock_partial_sell_qty(
        free_qty=Decimal("50"),
        mark=Decimal("6"),
        be=Decimal("6.04"),
        session_cap=Decimal("50"),
        rotate_inventory=True,
    )
    assert qty > 0
    assert qty * Decimal("6") <= Decimal("220") + Decimal("0.01")
    assert (
        b._uw_deadlock_partial_sell_qty(
            free_qty=Decimal("50"),
            mark=Decimal("6"),
            be=Decimal("6.04"),
            session_cap=Decimal("50"),
            rotate_inventory=False,
        )
        == Decimal("0")
    )


def test_mid_flat_slot_blocker_uses_shallower_depth() -> None:
    """Weak held UNI at ~0.35% should mid-flat when sleeve targets remain open."""
    b = _bridge()
    b._uw_mid_flat_recycle_enabled = True
    b._uw_mid_flat_max_depth_pct = Decimal("0.012")
    b._uw_mid_flat_min_age_sec = 600.0
    b._uw_near_below_be_pct = Decimal("0.005")  # live pre-crash near floor
    b._uw_deadlock_below_be_pct = Decimal("0.002")
    b._uw_deadlock_unlock_enabled = False
    b._uw_idle_pressure_enabled = False
    b._uw_dust_max_notional = Decimal("0")
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 400.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: True  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._uw_would_buy_today = lambda base, symbol: False  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._sleeve_held_fills_slot = lambda base: False  # type: ignore[method-assign]
    b._sleeve_deploy_targets = lambda top_n=2: ["ADA", "LINK"]  # type: ignore[method-assign]
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="UNI",
        symbol="UNIEUR",
        mark=Decimal("99.58"),  # -0.42% — below near_be 0.5% floor
        be=Decimal("100"),
        notional=Decimal("200"),
    )
    assert plan is not None
    assert plan[0] == "mid_flat"


def test_mid_flat_skips_would_buy_today() -> None:
    b = _bridge()
    b._uw_mid_flat_recycle_enabled = True
    b._uw_mid_flat_max_depth_pct = Decimal("0.012")
    b._uw_mid_flat_min_age_sec = 600.0
    b._uw_near_below_be_pct = Decimal("0.004")
    b._uw_deadlock_unlock_enabled = False
    b._uw_idle_pressure_enabled = False
    b._uw_dust_max_notional = Decimal("0")
    b._uw_non_alphai_min_age_sec = 99999.0
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 900.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: True  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._alphai_weak_bullish_hold = lambda base: False  # type: ignore[method-assign]
    b._alphai_hold_conviction = lambda base: 0.9  # type: ignore[method-assign]
    b._uw_would_buy_today = lambda base, symbol: True  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._uw_alphai_below_be_pct = Decimal("0.02")
    b._uw_alphai_min_age_sec = 10800.0
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="SOL",
        symbol="SOLEUR",
        mark=Decimal("99.10"),
        be=Decimal("100"),
        notional=Decimal("200"),
    )
    assert plan is None or plan[0] != "mid_flat"


def test_lag_time_partial_for_aged_mild_uw() -> None:
    """Aged lagging UNI under BE rotates while LINK/ADA sleeve targets wait."""
    b = _bridge()
    b._uw_mid_flat_recycle_enabled = False  # force lag-time path, not mid_flat
    b._uw_lag_time_partial_enabled = True
    b._uw_lag_time_partial_min_age_sec = 1800.0
    b._uw_lag_time_partial_max_depth_pct = Decimal("0.020")
    b._cut_loss_below_be_pct = Decimal("0.025")
    b._uw_deadlock_unlock_enabled = False
    b._uw_idle_pressure_enabled = False
    b._uw_dust_max_notional = Decimal("0")
    b._uw_non_alphai_min_age_sec = 99999.0
    b._uw_alphai_min_age_sec = 99999.0
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 2000.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: True  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._alphai_weak_bullish_hold = lambda base: True  # type: ignore[method-assign]
    b._uw_would_buy_today = lambda base, symbol: False  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._sleeve_held_fills_slot = lambda base: False  # type: ignore[method-assign]
    b._sleeve_deploy_targets = lambda top_n=2: ["ADA", "LINK"]  # type: ignore[method-assign]
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="UNI",
        symbol="UNIEUR",
        mark=Decimal("99.60"),  # -0.40%
        be=Decimal("100"),
        notional=Decimal("200"),
    )
    assert plan is not None
    assert plan[0] == "lag_time_partial"


def test_lag_time_partial_skipped_without_sleeve_targets() -> None:
    b = _bridge()
    b._uw_mid_flat_recycle_enabled = False
    b._uw_lag_time_partial_enabled = True
    b._uw_lag_time_partial_min_age_sec = 1800.0
    b._uw_lag_time_partial_max_depth_pct = Decimal("0.020")
    b._cut_loss_below_be_pct = Decimal("0.025")
    b._uw_deadlock_unlock_enabled = False
    b._uw_idle_pressure_enabled = False
    b._uw_dust_max_notional = Decimal("0")
    b._uw_non_alphai_min_age_sec = 99999.0
    b._uw_alphai_min_age_sec = 99999.0
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 2000.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: True  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._alphai_weak_bullish_hold = lambda base: True  # type: ignore[method-assign]
    b._uw_would_buy_today = lambda base, symbol: False  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._sleeve_deploy_targets = lambda top_n=2: []  # type: ignore[method-assign]
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="UNI",
        symbol="UNIEUR",
        mark=Decimal("99.60"),
        be=Decimal("100"),
        notional=Decimal("200"),
    )
    assert plan is None or plan[0] != "lag_time_partial"


def test_lag_time_partial_respects_hard_cut_ceiling() -> None:
    """Lag-time max depth stays below hard cut so 4%-style dumps cannot sneak in."""
    b = _bridge()
    b._uw_mid_flat_recycle_enabled = False
    b._uw_lag_time_partial_enabled = True
    b._uw_lag_time_partial_min_age_sec = 1800.0
    b._uw_lag_time_partial_max_depth_pct = Decimal("0.040")  # would-be 4%
    b._cut_loss_below_be_pct = Decimal("0.025")
    b._uw_deadlock_unlock_enabled = False
    b._uw_idle_pressure_enabled = False
    b._uw_dust_max_notional = Decimal("0")
    b._uw_non_alphai_min_age_sec = 99999.0
    b._uw_alphai_min_age_sec = 99999.0
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 2000.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: True  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._alphai_weak_bullish_hold = lambda base: True  # type: ignore[method-assign]
    b._uw_would_buy_today = lambda base, symbol: False  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._sleeve_held_fills_slot = lambda base: False  # type: ignore[method-assign]
    b._sleeve_deploy_targets = lambda top_n=2: ["LINK"]  # type: ignore[method-assign]
    # -3.0% is past 80% of 2.5% hard cut (=2.0%) → lag-time must NOT fire
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="UNI",
        symbol="UNIEUR",
        mark=Decimal("97.00"),
        be=Decimal("100"),
        notional=Decimal("200"),
    )
    assert plan is None or plan[0] != "lag_time_partial"

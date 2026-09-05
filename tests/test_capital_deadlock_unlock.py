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

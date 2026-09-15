"""Three-rule underwater policy + fee routing (replaces the tiered recycle stack)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor


def _bridge() -> MicroBudgetLiveExecutor:
    b = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    b._settings = SimpleNamespace(live_micro_preferred_entry_venue="bitvavo")
    b._execute_venues = ("bitvavo", "okx")
    b._long_hold_bases = frozenset()
    b._uw_recycle_enabled = True
    b._sleeve_paused = False
    b._daily_kill_active = False
    b._uw_dust_max_notional = Decimal("0")
    b._uw_dust_below_be_pct = Decimal("0.003")
    b._uw_policy = "simple"
    b._uw_simple_max_depth_pct = Decimal("0.012")
    b._uw_simple_unsupported_age_sec = 86400.0
    b._uw_simple_avoid_age_sec = 7200.0
    b._uw_simple_rotate_min_age_sec = 900.0
    b._uw_fresh_entry_grace_sec = 1800.0
    b._uw_deadlock_unlock_enabled = True
    b._uw_deadlock_day_loss_cap_eur = Decimal("8")
    b._uw_deadlock_day_key = ""
    b._uw_deadlock_day_loss_eur = Decimal("0")
    b._unit_cost = lambda venue, base: Decimal("100")  # type: ignore[method-assign]
    b._alphai_sleeve_priority_buy = lambda base, top_n=2: False  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: False  # type: ignore[method-assign]
    b._tape_leader_bases = lambda: frozenset()  # type: ignore[method-assign]
    b._alphai_is_avoid_base = lambda base: False  # type: ignore[method-assign]
    b._alphai_blocks_base = lambda base: False  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._uw_would_buy_today = lambda base, symbol: False  # type: ignore[method-assign]
    b._capital_deadlocked = lambda venue: False  # type: ignore[method-assign]
    b._sleeve_deploy_targets = lambda top_n=2: []  # type: ignore[method-assign]
    b._all_held_alt_bases = lambda **kw: {"SOL"}  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 3600.0  # type: ignore[method-assign]
    b._is_long_hold = lambda base: False  # type: ignore[method-assign]
    return b


def _plan(b: MicroBudgetLiveExecutor, mark: str) -> tuple[str, str, Decimal] | None:
    return b._uw_recycle_plan(
        venue="bitvavo",
        base="SOL",
        symbol="SOLEUR",
        mark=Decimal(mark),
        be=Decimal("100"),
        notional=Decimal("200"),
    )


def test_simple_policy_nurses_mild_unsupported_bag_until_aged() -> None:
    b = _bridge()
    # 1h old, -0.5%, nobody buys it: hold (no 2-minute recycles).
    assert _plan(b, "99.5") is None
    # 25h old → aged_unsupported band exit at mild depth.
    b._position_age_sec = lambda venue, base: 90_000.0  # type: ignore[method-assign]
    plan = _plan(b, "99.5")
    assert plan is not None and plan[0] == "aged_unsupported" and plan[1] == "band"
    assert plan[2] == Decimal("100") * Decimal("0.988")


def test_simple_policy_avoid_bags_age_faster_but_never_deep() -> None:
    b = _bridge()
    b._alphai_is_avoid_base = lambda base: True  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 3 * 3600.0  # type: ignore[method-assign]
    plan = _plan(b, "99.4")
    assert plan is not None and plan[0] == "aged_avoid"
    # Deeper than max depth: nurse to recovery-arm / hard cut, never dump here.
    assert _plan(b, "98.0") is None


def test_simple_policy_supported_bag_is_held() -> None:
    b = _bridge()
    b._position_age_sec = lambda venue, base: 90_000.0  # type: ignore[method-assign]
    b._tape_leader_bases = lambda: frozenset({"SOL"})  # type: ignore[method-assign]
    assert _plan(b, "99.5") is None
    b._tape_leader_bases = lambda: frozenset()  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: True  # type: ignore[method-assign]
    assert _plan(b, "99.5") is None


def test_simple_policy_rotates_only_against_confirmed_replacement() -> None:
    b = _bridge()
    b._capital_deadlocked = lambda venue: True  # type: ignore[method-assign]
    # Deadlocked but no unheld replacement → hold.
    assert _plan(b, "99.5") is None
    b._sleeve_deploy_targets = lambda top_n=2: ["LINK"]  # type: ignore[method-assign]
    plan = _plan(b, "99.5")
    assert plan is not None and plan[0] == "rotate_replacement" and plan[1] == "band"
    # Replacement already held → not a replacement.
    b._all_held_alt_bases = lambda **kw: {"SOL", "LINK"}  # type: ignore[method-assign]
    assert _plan(b, "99.5") is None
    # Daily voluntary-loss budget spent → hold.
    b._all_held_alt_bases = lambda **kw: {"SOL"}  # type: ignore[method-assign]
    b._uw_deadlock_day_loss_eur = Decimal("8")
    assert _plan(b, "99.5") is None
    # Fresh session entry inside grace → hold.
    b._uw_deadlock_day_loss_eur = Decimal("0")
    b._position_age_sec = lambda venue, base: 600.0  # type: ignore[method-assign]
    assert _plan(b, "99.5") is None


def test_fee_route_blocks_expensive_venue_when_cheap_one_can_take_clip() -> None:
    b = _bridge()
    b._held_alt_bases = lambda venue, **kw: set()  # type: ignore[method-assign]
    b._underwater_blocked_bases = {}
    b._venue_budget_remaining = lambda venue: Decimal("400")  # type: ignore[method-assign]
    b._observed_fee_rates = {}
    msg = b._fee_route_block("okx", "LTC", Decimal("120"))
    assert msg and "routed to bitvavo" in msg
    # Preferred venue itself is never blocked.
    assert b._fee_route_block("bitvavo", "LTC", Decimal("120")) is None
    # Preferred venue already holds the base → OKX may add.
    b._held_alt_bases = lambda venue, **kw: {"LTC"}  # type: ignore[method-assign]
    assert b._fee_route_block("okx", "LTC", Decimal("120")) is None
    # Preferred venue lacks free ring for the clip → OKX takes it.
    b._held_alt_bases = lambda venue, **kw: set()  # type: ignore[method-assign]
    b._venue_budget_remaining = lambda venue: Decimal("50")  # type: ignore[method-assign]
    assert b._fee_route_block("okx", "LTC", Decimal("120")) is None
    # Routing off.
    b._settings.live_micro_preferred_entry_venue = ""
    b._venue_budget_remaining = lambda venue: Decimal("400")  # type: ignore[method-assign]
    assert b._fee_route_block("okx", "LTC", Decimal("120")) is None

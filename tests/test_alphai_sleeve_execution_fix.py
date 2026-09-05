"""AlphaI sleeve execution fixes: buy under ADVERSE, hold winners, settle loop."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from bot.integrations.alphai.features import evaluate_intraday_entry_gate
from bot.integrations.alphai.pick_outcomes import PickOutcomeStore
from bot.integrations.alphai.signals import AlphaITradingSignals
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor


class _Sig:
    def __init__(self) -> None:
        self.daily_pick_bases = frozenset({"BNB", "ADA", "AVAX"})
        self.daily_pick_scores = {"BNB": 100.0, "ADA": 90.0, "AVAX": 80.0}
        self.bullish_bases = frozenset({"BNB", "ADA", "AVAX"})
        self.avoid_bases = frozenset({"ETH", "XRP"})
        self.blocked_bases = frozenset()

    def is_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return str(base).upper() in self.bullish_bases

    def is_strong_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return str(base).upper() == "BNB"

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

    def is_bearish(self, base: str) -> bool:
        return str(base).upper() in self.avoid_bases

    def is_price_lagging(self, base: str) -> bool:
        return False

    def price_confirm_scale(self, base: str) -> float:
        return 0.50


def test_rising_strict_blocks_but_soft_sleeve_params_allow() -> None:
    """ADVERSE rising-strict WAIT vs sleeve-softened params ALLOW/REDUCE."""
    soft = evaluate_intraday_entry_gate(
        is_alphai_buy=True,
        freshness=Decimal("0.50"),
        adverse_score=Decimal("0.10"),
        entry_timing="NORMAL",
        momentum_down=False,
        momentum_rising=False,
        min_freshness=Decimal("0.40"),
        min_confirm_scale=Decimal("0.35"),
        require_momentum_rising=False,
        price_confirm_scale=Decimal("0.50"),
    )
    assert soft.action in {"ALLOW", "REDUCE"}

    hard = evaluate_intraday_entry_gate(
        is_alphai_buy=True,
        freshness=Decimal("0.50"),
        adverse_score=Decimal("0.10"),
        entry_timing="NORMAL",
        momentum_down=False,
        momentum_rising=False,
        min_freshness=Decimal("0.55"),
        min_confirm_scale=Decimal("0.45"),
        require_momentum_rising=True,
        price_confirm_scale=Decimal("0.50"),
    )
    assert hard.action == "WAIT"


def test_bridge_sleeve_intraday_gate_bypasses_adverse_rising_strict() -> None:
    b = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    b._alphai_signals = _Sig()
    b._settings = SimpleNamespace(
        alphai_bullish_buy_enabled=True,
        alphai_require_bullish_new_buys=False,
    )
    b._alphai_intraday_gate_enabled = True
    b._alphai_intraday_min_freshness = Decimal("0.55")
    b._alphai_intraday_require_rising = True
    b._momentum_enabled = False
    b._alphai_feature_config = SimpleNamespace(
        adverse_bullish_wait_threshold=Decimal("0.55"),
        adverse_bullish_reduce_threshold=Decimal("0.40"),
    )
    b._alphai_feature_for = (  # type: ignore[method-assign]
        lambda base, adverse_score=None: SimpleNamespace(
            freshness=Decimal("0.50"),
            entry_timing="NORMAL",
        )
    )
    b._alphai_ring_fallback_active = lambda: False  # type: ignore[method-assign]
    action, _mult, reasons = b._alphai_intraday_entry_gate("BNB", "BNBEUR")
    assert action in {"ALLOW", "REDUCE"}
    assert "momentum_not_rising_strict" not in reasons


def test_macro_does_not_soften_sleeve_harvest_floor() -> None:
    sig = AlphaITradingSignals(
        bullish_bases=frozenset({"BNB", "ADA"}),
        avoid_bases=frozenset(),
        blocked_bases=frozenset(),
        watch_bases=frozenset(),
        daily_pick_bases=frozenset({"BNB", "ADA", "DOT"}),
        daily_pick_scores={"BNB": 100.0, "ADA": 90.0, "DOT": 40.0},
        macro_active=True,
        bullish_headline_counts={"BNB": 4, "ADA": 3, "DOT": 2},
        bearish_headline_counts={},
        price_lag_bases=frozenset(),
        price_confirm_scales={"BNB": 1.0, "ADA": 1.0, "DOT": 1.0},
        base_reliability={"BNB": 1.0, "ADA": 1.0, "DOT": 1.0},
    )
    assert sig.is_slot_priority_buy("BNB", top_n=2)
    assert not sig.is_slot_priority_buy("DOT", top_n=2)
    assert sig.be_harvest_gain_scale("BNB") > sig.be_harvest_gain_scale("DOT")


def test_avoid_recycles_faster_when_sleeve_unheld() -> None:
    b = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    b._alphai_signals = _Sig()
    b._settings = SimpleNamespace(
        alphai_bullish_buy_enabled=True,
        alphai_require_bullish_new_buys=False,
    )
    b._uw_recycle_enabled = True
    b._sleeve_paused = False
    b._daily_kill_active = False
    b._execute_venues = {"bitvavo"}
    b._is_long_hold = lambda base: False  # type: ignore[method-assign]
    b._unit_cost = lambda venue, base: Decimal("2000")  # type: ignore[method-assign]
    b._position_age_sec = lambda venue, base: 200.0  # type: ignore[method-assign]
    b._alphai_bullish_buy = lambda base: False  # type: ignore[method-assign]
    b._alphai_protects_from_cuts = lambda base: False  # type: ignore[method-assign]
    b._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]
    b._held_alt_bases = lambda venue=None, min_notional_eur=None: set()  # type: ignore[method-assign]
    b._uw_dust_max_notional = Decimal("0")
    b._uw_non_alphai_below_be_pct = Decimal("0.01")
    b._uw_non_alphai_min_age_sec = 3600.0
    b._uw_avoid_max_age_sec = 900.0
    b._uw_idle_pressure_enabled = False
    b._uw_idle_min_age_sec = 600.0
    b._uw_idle_below_be_pct = Decimal("0.004")
    b._uw_idle_min_free_eur = Decimal("150")
    b._uw_alphai_below_be_pct = Decimal("0.02")
    b._uw_alphai_min_age_sec = 10800.0
    b._uw_near_below_be_pct = Decimal("0.008")
    b._uw_near_max_depth_pct = Decimal("0.015")
    b._uw_near_min_age_sec = 2700.0
    b._alphai_weak_bullish_hold = lambda base: False  # type: ignore[method-assign]
    b._alphai_hold_conviction = lambda base: 0.0  # type: ignore[method-assign]
    b._venue_budget_remaining = lambda venue: Decimal("500")  # type: ignore[method-assign]
    assert b._sleeve_has_unheld_priority() is True
    plan = b._uw_recycle_plan(
        venue="bitvavo",
        base="ETH",
        symbol="ETHEUR",
        mark=Decimal("1992"),
        be=Decimal("2000"),
        notional=Decimal("200"),
    )
    assert plan is not None
    assert str(plan[0]).startswith("avoid_")


def test_pick_outcomes_preserve_settled_on_empty_rerecord() -> None:
    store = PickOutcomeStore()
    report = {
        "session_id": "2026-09-05T12:00",
        "generated_at": "2026-09-05T10:00:00+00:00",
        "macro_caution": True,
        "picks": [{"base": "BNB", "score": 100.0, "rank": 1}],
    }
    store.record_session(report, day_returns_pct={"BNB": 2.0, "BTC": 0.5}, settle=True)
    assert store.sessions[0]["settled"] is True
    lesson = store.sessions[0].get("lesson")
    store.record_session(report, day_returns_pct=None, settle=False)
    assert store.sessions[0]["settled"] is True
    assert store.sessions[0].get("lesson") == lesson
    assert int(store.summary().get("settled_sessions") or 0) >= 1

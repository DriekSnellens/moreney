"""AlphaI rank-1/2 sleeve + soft-ADVERSE deploy policy."""

from __future__ import annotations

from types import SimpleNamespace

from bot.live.capital_playbook import PLAYBOOK_OVERLAYS, CapitalPlaybook
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor


class _Sig:
    def __init__(self) -> None:
        self.daily_pick_bases = frozenset({"BNB", "ADA", "AVAX", "LINK"})
        self.daily_pick_scores = {"BNB": 100.0, "ADA": 90.0, "AVAX": 80.0, "LINK": 70.0}
        self.bullish_bases = frozenset({"BNB", "ADA", "AVAX", "LINK"})
        self.avoid_bases = frozenset()
        self.blocked_bases = frozenset()

    def is_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return str(base).upper() in self.bullish_bases

    def is_strong_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return str(base).upper() == "BNB"

    def is_slot_priority_buy(self, base: str, *, top_n: int = 2) -> bool:
        ranked = sorted(self.daily_pick_scores, key=self.daily_pick_scores.get, reverse=True)
        return str(base).upper() in set(ranked[:top_n])


def test_adverse_overlay_keeps_sleeve_and_peak_fade_harvest() -> None:
    adverse = PLAYBOOK_OVERLAYS[CapitalPlaybook.ADVERSE]
    assert adverse["block_new_buys"] is True
    assert adverse["alphai_idle_deploy_blocked"] is False
    assert adverse["alphai_strong_clip_eur"] >= 200
    assert adverse["active_ring_eur"] >= 1200
    assert adverse["be_harvest_min_gain_pct"] >= 0.006
    assert adverse["trail_hold_rising_n"] >= 2
    assert adverse["alphai_cross_venue_deploy"] is True


def test_sleeve_priority_buy_ranks_top_two() -> None:
    bridge = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    bridge._alphai_signals = _Sig()
    bridge._settings = SimpleNamespace(
        alphai_bullish_buy_enabled=True,
        alphai_require_bullish_new_buys=False,
    )
    bridge._alphai_ring_fallback_active = lambda: False  # type: ignore[method-assign]
    bridge._all_held_alt_bases = lambda min_notional_eur=None: set()  # type: ignore[method-assign]
    assert bridge._alphai_sleeve_priority_buy("BNB") is True
    assert bridge._alphai_sleeve_priority_buy("ADA") is True
    assert bridge._alphai_sleeve_priority_buy("AVAX") is False
    assert bridge._alphai_sleeve_priority_buy("DOGE") is False


def test_weak_held_sleeve_slot_promotes_next_unheld() -> None:
    """Underwater/weak UNI must not block LINK when ring needs AlphaI deploy."""
    from decimal import Decimal

    bridge = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    bridge._alphai_signals = _Sig()
    bridge._settings = SimpleNamespace(
        alphai_bullish_buy_enabled=True,
        alphai_require_bullish_new_buys=False,
    )
    bridge._alphai_ring_fallback_active = lambda: False  # type: ignore[method-assign]
    bridge._execute_venues = ("bitvavo",)
    # Structural top-2 BNB+ADA held, but both weak → slots stay open for AVAX/LINK.
    bridge._all_held_alt_bases = (  # type: ignore[method-assign]
        lambda min_notional_eur=None: {"BNB", "ADA"}
    )
    bridge._alphai_weak_bullish_hold = (  # type: ignore[method-assign]
        lambda base: str(base).upper() in {"BNB", "ADA"}
    )
    bridge._underwater_depth_on_venue = (  # type: ignore[method-assign]
        lambda venue, base: Decimal("0.007")
    )

    assert bridge._sleeve_deploy_targets(top_n=2) == ["AVAX", "LINK"]
    assert bridge._sleeve_has_unheld_priority() is True
    assert bridge._alphai_sleeve_priority_buy("AVAX") is True
    assert bridge._alphai_sleeve_priority_buy("LINK") is True
    assert bridge._desk_sleeve_unheld_bases() == ["AVAX", "LINK"]


def test_sleeve_momentum_floor_eases_without_desk_bias() -> None:
    """Empty ring + sleeve target must soften momentum floor even at bias 1.0."""
    from decimal import Decimal

    bridge = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    bridge._momentum_min = Decimal("0.004")
    bridge._ring_momentum_min = Decimal("0.001")
    bridge._ring_soft_momentum_eligible = lambda venue: True  # type: ignore[method-assign]
    bridge._alphai_momentum_floor_scale = lambda base: Decimal("1")  # type: ignore[method-assign]
    bridge._alphai_sleeve_priority_buy = lambda base: True  # type: ignore[method-assign]
    bridge._ring_needs_deploy = lambda venue: True  # type: ignore[method-assign]
    bridge._desk_lesson_deploy_bias = lambda: 1.0  # type: ignore[method-assign]
    floor = bridge._momentum_floor_for_buy("bitvavo", "LINK")
    assert floor < Decimal("0.001")
    assert floor <= Decimal("0.001") / Decimal("1.20")

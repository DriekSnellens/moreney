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
    assert bridge._alphai_sleeve_priority_buy("BNB") is True
    assert bridge._alphai_sleeve_priority_buy("ADA") is True
    assert bridge._alphai_sleeve_priority_buy("AVAX") is False
    assert bridge._alphai_sleeve_priority_buy("DOGE") is False

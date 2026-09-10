"""Stricter AlphaI daytrader entry timing: confirm + conviction + rising."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from bot.core.config import Settings
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor
from bot.live.micro_session import _session_settings


class _Sig:
    def __init__(
        self,
        *,
        confirm: dict[str, float] | None = None,
        conviction: dict[str, float] | None = None,
        lagging: set[str] | None = None,
    ) -> None:
        self.daily_pick_bases = frozenset({"ETH", "NEAR", "SOL"})
        self.daily_pick_scores = {"ETH": 100.0, "NEAR": 50.0, "SOL": 80.0}
        self.bullish_bases = frozenset({"ETH", "NEAR", "SOL"})
        self.avoid_bases = frozenset()
        self.blocked_bases = frozenset()
        self._confirm = confirm or {"ETH": 1.0, "NEAR": 0.0, "SOL": 0.62}
        self._conviction = conviction or {"ETH": 1.0, "NEAR": 0.04, "SOL": 0.40}
        self._lagging = {b.upper() for b in (lagging or {"NEAR"})}

    def is_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return str(base).upper() in self.bullish_bases

    def is_strong_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        return str(base).upper() == "ETH"

    def is_slot_priority_buy(self, base: str, *, top_n: int = 2) -> bool:
        ranked = sorted(
            self.daily_pick_scores, key=self.daily_pick_scores.get, reverse=True
        )
        return str(base).upper() in set(ranked[:top_n])

    def is_price_lagging(self, base: str) -> bool:
        return str(base).upper() in self._lagging

    def price_confirm_scale(self, base: str) -> float:
        return float(self._confirm.get(str(base).upper(), 1.0))

    def pick_conviction(self, base: str) -> float:
        return float(self._conviction.get(str(base).upper(), 0.0))


def _bridge(**kwargs) -> MicroBudgetLiveExecutor:
    b = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    b._alphai_signals = kwargs.get("sig", _Sig())
    b._settings = SimpleNamespace(
        alphai_bullish_buy_enabled=True,
        alphai_require_bullish_new_buys=True,
    )
    b._alphai_intraday_gate_enabled = True
    b._alphai_intraday_min_freshness = Decimal("0.40")
    b._alphai_intraday_require_rising = False
    b._alphai_daytrader_enabled = True
    b._daytrader_min_confirm = Decimal("0.55")
    b._daytrader_sleeve_min_confirm = Decimal("0.55")
    b._daytrader_require_rising = True
    b._daytrader_sleeve_require_rising = True
    b._daytrader_min_conviction = 0.25
    b._daytrader_sleeve_urgency_enabled = False
    b._daytrader_priority_clip_min_confirm = Decimal("0.60")
    b._daytrader_strong_clip_min_confirm = Decimal("0.75")
    b._momentum_enabled = True
    b._momentum_require_last_n_rising = 1
    b._alphai_feature_config = SimpleNamespace(
        adverse_bullish_wait_threshold=Decimal("0.55"),
        adverse_bullish_reduce_threshold=Decimal("0.40"),
    )
    b._alphai_feature_for = (  # type: ignore[method-assign]
        lambda base, adverse_score=None: SimpleNamespace(
            freshness=Decimal("0.75"),
            entry_timing="NORMAL",
        )
    )
    b._alphai_ring_fallback_active = lambda: False  # type: ignore[method-assign]
    b._alphai_sleeve_priority_buy = (  # type: ignore[method-assign]
        lambda base, top_n=2: str(base).upper() in {"ETH", "SOL", "NEAR"}
    )
    rising = bool(kwargs.get("rising", True))
    b._momentum_down = lambda symbol: False  # type: ignore[method-assign]
    b._series_for = lambda symbol: SimpleNamespace(  # type: ignore[method-assign]
        __len__=lambda self: 5,
        last_n_rising=lambda n: rising,
    )
    # Make len(series) work
    class _Series:
        def __len__(self) -> int:
            return 5

        def last_n_rising(self, n: int) -> bool:
            return rising

    b._series_for = lambda symbol: _Series()  # type: ignore[method-assign]
    for k, v in kwargs.items():
        if k in {"sig", "rising"}:
            continue
        setattr(b, k, v)
    return b


def test_daytrader_waits_on_lagging_zero_confirm_sleeve() -> None:
    """NEAR-class: pick + lagging + confirm 0 → hard WAIT (no soft floor)."""
    b = _bridge(rising=True)
    action, mult, reasons = b._alphai_intraday_entry_gate("NEAR", "NEAREUR")
    assert action == "WAIT"
    assert mult == Decimal("0")
    assert "price_lagging" in reasons or "price_confirm_weak" in reasons or (
        "pick_conviction_weak" in reasons
    )


def test_daytrader_waits_on_weak_conviction_even_if_confirmed() -> None:
    sig = _Sig(
        confirm={"ETH": 0.80, "NEAR": 0.80, "SOL": 0.80},
        conviction={"ETH": 1.0, "NEAR": 0.05, "SOL": 0.40},
        lagging=set(),
    )
    b = _bridge(sig=sig, rising=True)
    action, _mult, reasons = b._alphai_intraday_entry_gate("NEAR", "NEAREUR")
    assert action == "WAIT"
    assert "pick_conviction_weak" in reasons


def test_daytrader_sleeve_requires_rising() -> None:
    sig = _Sig(
        confirm={"ETH": 1.0, "NEAR": 0.80, "SOL": 0.80},
        conviction={"ETH": 1.0, "NEAR": 0.50, "SOL": 0.50},
        lagging=set(),
    )
    b = _bridge(sig=sig, rising=False)
    action, _mult, reasons = b._alphai_intraday_entry_gate("ETH", "ETHEUR")
    assert action == "WAIT"
    assert "momentum_not_rising_strict" in reasons


def test_daytrader_allows_confirmed_rising_sleeve() -> None:
    sig = _Sig(
        confirm={"ETH": 1.0, "NEAR": 0.80, "SOL": 0.80},
        conviction={"ETH": 1.0, "NEAR": 0.50, "SOL": 0.50},
        lagging=set(),
    )
    b = _bridge(sig=sig, rising=True)
    action, _mult, reasons = b._alphai_intraday_entry_gate("ETH", "ETHEUR")
    assert action in {"ALLOW", "REDUCE"}
    assert "price_lagging" not in reasons
    assert "pick_conviction_weak" not in reasons


def test_non_daytrader_sleeve_still_softens_rising() -> None:
    b = _bridge(rising=False)
    b._alphai_daytrader_enabled = False
    b._momentum_enabled = False
    action, _mult, reasons = b._alphai_intraday_entry_gate("ETH", "ETHEUR")
    assert action in {"ALLOW", "REDUCE"}
    assert "momentum_not_rising_strict" not in reasons


def test_daytrader_clip_requires_confirm_for_priority() -> None:
    b = _bridge()
    b._winner_add_enabled = False
    b._first_clip_eur = Decimal("105")
    b._alphai_priority_clip_eur = Decimal("220")
    b._alphai_strong_clip_eur = Decimal("280")
    b._add_clip_eur = Decimal("140")
    b._is_new_base_buy = lambda venue, base: True  # type: ignore[method-assign]
    # Weak confirm → first clip only
    b._alphai_signals = _Sig(
        confirm={"ETH": 0.50, "NEAR": 0.0, "SOL": 0.50},
        lagging=set(),
    )
    assert b._buy_clip_cap_eur("okx", "ETH") == Decimal("105")
    # Strong confirm → strong clip
    b._alphai_signals = _Sig(
        confirm={"ETH": 0.90, "NEAR": 0.0, "SOL": 0.70},
        lagging=set(),
    )
    assert b._buy_clip_cap_eur("okx", "ETH") == Decimal("280")
    # Mid confirm priority (SOL rank-2-ish but ETH is strong; use SOL)
    b._alphai_signals = _Sig(
        confirm={"ETH": 0.50, "NEAR": 0.0, "SOL": 0.65},
        lagging=set(),
    )
    # SOL is slot priority (top 2 by score: ETH, SOL) and bullish → priority clip
    assert b._buy_clip_cap_eur("okx", "SOL") == Decimal("220")


def test_session_daytrader_entry_floors(tmp_path) -> None:
    cfg = _session_settings(
        Settings(
            live_micro_execute_venues="bitvavo,okx",
            live_micro_daytrader_min_confirm_scale=0.50,
            live_micro_daytrader_sleeve_min_confirm_scale=0.40,
            live_micro_entry_quality_min_score=55.0,
        ),
        budget_eur=Decimal("2000"),
        symbols=["ETHEUR"],
        persist_path=tmp_path / "state.json",
    )
    assert cfg.live_micro_daytrader_min_confirm_scale >= 0.55
    assert cfg.live_micro_daytrader_sleeve_min_confirm_scale >= 0.55
    assert cfg.live_micro_daytrader_min_conviction >= 0.25
    assert cfg.live_micro_daytrader_sleeve_urgency_enabled is False
    assert cfg.live_micro_daytrader_sleeve_require_rising is True
    assert cfg.live_micro_entry_quality_min_score >= 65.0

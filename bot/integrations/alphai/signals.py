"""AlphaI trading signals — merge regime headlines + daily picks for entry/exit."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bot.integrations.alphai.parse import AlphaIRegimeState

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class AlphaITradingSignals:
    """Unified AlphaI view for maker ranking and live bridge entry/exit."""

    daily_pick_scores: dict[str, float]
    daily_pick_bases: frozenset[str]
    avoid_bases: frozenset[str]
    bullish_bases: frozenset[str]
    blocked_bases: frozenset[str]
    macro_active: bool

    def pick_score(self, base: str) -> float:
        return float(self.daily_pick_scores.get(str(base or "").upper(), 0.0))

    def is_top_pick(self, base: str, *, top_n: int = 3) -> bool:
        b = str(base or "").upper()
        if b not in self.daily_pick_bases:
            return False
        ranked = sorted(
            self.daily_pick_scores.items(),
            key=lambda row: row[1],
            reverse=True,
        )
        tops = {k for k, _ in ranked[:top_n]}
        return b in tops

    def exit_urgency(self, base: str) -> bool:
        """Held bags on avoid/blocked headlines should harvest faster."""
        b = str(base or "").upper()
        return b in self.avoid_bases or b in self.blocked_bases

    def is_bullish_buy(self, base: str) -> bool:
        """Live bullish headline or daily pick with positive score — allow entry."""
        b = str(base or "").upper()
        if b in self.blocked_bases or b in self.avoid_bases:
            return False
        if b in self.bullish_bases:
            return True
        if b in self.daily_pick_bases and self.pick_score(b) > 0:
            return True
        return False

    def is_strong_bullish_buy(self, base: str) -> bool:
        """Live headline bullish or top daily pick — proactive buy path."""
        b = str(base or "").upper()
        if not self.is_bullish_buy(b):
            return False
        if b in self.bullish_bases:
            return True
        return self.is_top_pick(b) and self.pick_score(b) >= 4.0

    def entry_size_multiplier(self, base: str) -> Decimal:
        """Boost size on daily picks + live bullish; trim on avoid (pre-block)."""
        b = str(base or "").upper()
        if b in self.avoid_bases:
            return Decimal("0.75")
        mult = _ONE
        score = self.pick_score(b)
        if score >= 4.0:
            mult += Decimal(str(min(0.25, 0.05 + score * 0.008)))
        if b in self.bullish_bases:
            mult += Decimal("0.08")
        if self.is_top_pick(b):
            mult += Decimal("0.05")
        return min(mult, Decimal("1.35"))

    def maker_rank_boost(self, base: str, *, is_buy: bool) -> Decimal:
        """Additive EUR rank boost for maker opportunity sorting."""
        b = str(base or "").upper()
        boost = _ZERO
        score = self.pick_score(b)
        if score > 0:
            boost += Decimal(str(min(0.18, 0.04 + score * 0.006)))
        if b in self.bullish_bases:
            boost += Decimal("0.05")
        if self.is_top_pick(b) and is_buy:
            boost += Decimal("0.04")
        if b in self.avoid_bases and is_buy:
            boost -= Decimal("0.25")
        return boost

    def fv_buy_premium_bps(self, base: str, default_bps: Decimal) -> Decimal:
        """Widen fair-value buy ceiling on bullish / daily pick bases."""
        b = str(base or "").upper()
        extra = _ZERO
        if self.pick_score(b) >= 4.0:
            extra += Decimal("3")
        if b in self.bullish_bases:
            extra += Decimal("2")
        if self.is_top_pick(b):
            extra += Decimal("2")
        return default_bps + extra

    def momentum_floor_scale(self, base: str) -> Decimal:
        """Scale momentum floor down (easier entry) for top picks / bullish headlines."""
        b = str(base or "").upper()
        if b in self.bullish_bases and self.is_top_pick(b):
            return Decimal("0.50")
        if self.is_top_pick(b) or b in self.bullish_bases:
            return Decimal("0.65")
        if self.pick_score(b) >= 4.0:
            return Decimal("0.80")
        return _ONE

    def be_harvest_gain_scale(self, base: str) -> Decimal:
        """Lower min-gain threshold → earlier partial harvest on avoid/blocked."""
        if self.exit_urgency(base):
            return Decimal("0.55")
        if self.macro_active:
            return Decimal("0.80")
        return _ONE

    def bullish_buy_bases(self) -> frozenset[str]:
        return frozenset(
            b
            for b in set(self.daily_pick_bases) | set(self.bullish_bases)
            if self.is_bullish_buy(b)
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "daily_pick_scores": dict(self.daily_pick_scores),
            "daily_pick_bases": sorted(self.daily_pick_bases),
            "avoid_bases": sorted(self.avoid_bases),
            "bullish_bases": sorted(self.bullish_bases),
            "bullish_buy_bases": sorted(self.bullish_buy_bases()),
            "blocked_bases": sorted(self.blocked_bases),
            "macro_active": self.macro_active,
        }


def build_trading_signals(
    state: AlphaIRegimeState | None,
    daily: dict[str, Any] | None,
) -> AlphaITradingSignals:
    """Merge live regime + daily recommendation report into one signal bundle."""
    scores: dict[str, float] = {}
    pick_bases: set[str] = set()
    avoid: set[str] = set()

    if isinstance(daily, dict):
        for row in daily.get("picks") or []:
            if not isinstance(row, dict):
                continue
            base = str(row.get("base") or "").strip().upper()
            if not base:
                continue
            pick_bases.add(base)
            try:
                scores[base] = float(row.get("score") or 0.0)
            except (TypeError, ValueError):
                scores[base] = 0.0
        for row in daily.get("avoid") or []:
            if not isinstance(row, dict):
                continue
            base = str(row.get("base") or "").strip().upper()
            if base:
                avoid.add(base)

    bullish: set[str] = set()
    blocked: set[str] = set()
    macro = False
    observation = False
    if state is not None:
        bullish = set(getattr(state, "bullish_bases", frozenset()) or ())
        blocked = set(getattr(state, "blocked_bases", frozenset()) or ())
        macro = bool(getattr(state, "macro_reduce_only", False))
        observation = bool(getattr(state, "observation_mode", False))
        # Observation mode: blocked_bases empty but detail populated — soft avoid only.
        if observation and not blocked:
            detail = getattr(state, "blocked_detail", {}) or {}
            for key in detail:
                k = str(key).strip().upper()
                if k and k != "_MACRO_":
                    avoid.add(k)

    return AlphaITradingSignals(
        daily_pick_scores=scores,
        daily_pick_bases=frozenset(pick_bases),
        avoid_bases=frozenset(avoid),
        bullish_bases=frozenset(bullish),
        blocked_bases=frozenset(blocked),
        macro_active=macro,
    )

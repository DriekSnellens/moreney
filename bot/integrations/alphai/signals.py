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
    watch_bases: frozenset[str]
    bullish_bases: frozenset[str]
    blocked_bases: frozenset[str]
    macro_active: bool

    def pick_score(self, base: str) -> float:
        return float(self.daily_pick_scores.get(str(base or "").upper(), 0.0))

    def is_top_pick(self, base: str, *, top_n: int = 5) -> bool:
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

    def priority_buy_bases(self, *, top_n: int = 5) -> frozenset[str]:
        """Daily picks with positive score — preferred deploy targets."""
        ranked = sorted(
            (
                (b, s)
                for b, s in self.daily_pick_scores.items()
                if b in self.daily_pick_bases
                and s > 0
                and b not in self.avoid_bases
                and b not in self.blocked_bases
            ),
            key=lambda row: row[1],
            reverse=True,
        )
        return frozenset(b for b, _ in ranked[:top_n])

    def unheld_priority_buys(
        self,
        held_bases: set[str] | frozenset[str],
        *,
        top_n: int = 5,
    ) -> frozenset[str]:
        held = {str(b).upper() for b in held_bases}
        return frozenset(
            b for b in self.priority_buy_bases(top_n=top_n) if b not in held
        )

    def is_slot_priority_buy(self, base: str, *, top_n: int = 5) -> bool:
        return str(base or "").upper() in self.priority_buy_bases(top_n=top_n)

    def non_pick_slot_penalty(
        self,
        base: str,
        held_bases: set[str] | frozenset[str],
        *,
        top_n: int = 5,
    ) -> Decimal:
        """Demote non-priority buys while unheld AlphaI picks still need a slot."""
        if not self.unheld_priority_buys(held_bases, top_n=top_n):
            return _ZERO
        b = str(base or "").upper()
        if self.is_slot_priority_buy(b, top_n=top_n):
            return _ZERO
        return Decimal("-0.60")

    def is_bearish(self, base: str) -> bool:
        """Daily avoid or live bearish/blocked headline."""
        b = str(base or "").upper()
        return b in self.avoid_bases or b in self.blocked_bases

    def allows_new_buy(self, base: str) -> bool:
        """True for AlphaI bullish headlines or top positive daily picks."""
        b = str(base or "").upper()
        if self.is_bearish(b):
            return False
        if b in self.bullish_bases:
            return True
        # Top-N positive daily picks (default 5) — expands thin buy universe.
        if self.is_slot_priority_buy(b, top_n=5):
            return True
        return self.is_bullish_buy(b, ring_fallback=False)

    def recycle_sell_only(self, base: str) -> bool:
        """Non-bullish bags should recycle at BE+ — no new buy exposure."""
        return not self.allows_new_buy(base)

    def exit_urgency(self, base: str) -> bool:
        """Bearish or non-bullish bags harvest faster (still never below fee-aware BE)."""
        return self.is_bearish(base) or self.recycle_sell_only(base)

    def be_harvest_gain_scale(self, base: str) -> Decimal:
        """Lower min-gain threshold → earlier partial harvest on avoid/neutral."""
        if self.is_bearish(base):
            return Decimal("0.50")
        if self.recycle_sell_only(base):
            return Decimal("0.65")
        if self.macro_active:
            return Decimal("0.80")
        return _ONE

    def all_bullish_held(self, held_bases: set[str] | frozenset[str]) -> bool:
        """True when every bullish buy pick is already in portfolio (or none exist)."""
        buys = self.bullish_buy_bases()
        held = {str(b).upper() for b in held_bases}
        if not buys:
            return True
        return buys.issubset(held)

    def is_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        """Live bullish headline or daily pick with positive score — allow entry.

        When *ring_fallback* is set (active ring underfilled and all bullish picks
        already held), also allow watch-list / non-avoid focus bases so the desk
        is not idle under macro caution.
        """
        b = str(base or "").upper()
        if b in self.blocked_bases or b in self.avoid_bases:
            return False
        if b in self.bullish_bases:
            return True
        if b in self.daily_pick_bases and self.pick_score(b) > 0:
            return True
        if ring_fallback:
            if b in self.watch_bases:
                return True
            # Unscored / flat focus: allow only when no unheld bullish picks remain.
            return self.pick_score(b) >= 0
        return False

    def is_strong_bullish_buy(self, base: str, *, ring_fallback: bool = False) -> bool:
        """Live headline bullish or high-conviction daily pick — max clip path.

        Weak top-N names (low score) stay buyable via ``allows_new_buy`` / priority
        clip, but must not receive strong-clip / soft-gate treatment.
        """
        b = str(base or "").upper()
        if not self.is_bullish_buy(b, ring_fallback=ring_fallback):
            return False
        if b in self.bullish_bases:
            return True
        # High bar: top pick AND meaningful score (ETH/XRP-class, not DOGE-18).
        if self.is_top_pick(b, top_n=3) and self.pick_score(b) >= 35.0:
            return True
        if ring_fallback and b in self.watch_bases and self.pick_score(b) >= 10.0:
            return True
        return False

    def entry_size_multiplier(self, base: str) -> Decimal:
        """Boost size on daily picks + live bullish; trim on avoid (pre-block)."""
        b = str(base or "").upper()
        if b in self.avoid_bases:
            return Decimal("0.75")
        mult = _ONE
        score = self.pick_score(b)
        if score >= 4.0:
            # High scores (e.g. ETH ~90+) get near-max clip boost for capital deploy.
            mult += Decimal(str(min(0.40, 0.05 + score * 0.004)))
        if b in self.bullish_bases:
            mult += Decimal("0.10")
        if self.is_top_pick(b):
            mult += Decimal("0.08")
        if self.is_strong_bullish_buy(b):
            mult += Decimal("0.05")
        return min(mult, Decimal("1.50"))

    def maker_rank_boost(self, base: str, *, is_buy: bool) -> Decimal:
        """Additive EUR rank boost for maker opportunity sorting."""
        b = str(base or "").upper()
        boost = _ZERO
        score = self.pick_score(b)
        if is_buy:
            if score > 0:
                # Stronger weight so high-score picks outrank random focus coins.
                boost += Decimal(str(min(0.45, 0.08 + score * 0.01)))
            if b in self.bullish_bases:
                boost += Decimal("0.08")
            if self.is_top_pick(b):
                boost += Decimal("0.20")
            if self.is_bearish(b):
                boost -= Decimal("0.50")
            elif not self.allows_new_buy(b):
                boost -= Decimal("0.35")
        else:
            # Prefer harvesting bearish / non-bullish bags first (still ≥ BE).
            if self.is_bearish(b):
                boost += Decimal("0.35")
            elif not self.allows_new_buy(b):
                boost += Decimal("0.12")
            if b in self.bullish_bases or self.is_top_pick(b):
                boost -= Decimal("0.08")
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

    def inventory_build(self, base: str, *, ring_fallback: bool = False) -> bool:
        """First buy on strong AlphaI signal; ring_fallback opens watch/focus path."""
        if self.is_strong_bullish_buy(base):
            return True
        if not ring_fallback:
            return False
        b = str(base or "").upper()
        if b in self.blocked_bases or b in self.avoid_bases:
            return False
        # Prefer watch / soft-positive; else any non-avoid with non-negative score.
        if b in self.watch_bases:
            return True
        if b in self.daily_pick_bases and self.pick_score(b) > 0:
            return True
        return self.pick_score(b) >= 0

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
            "watch_bases": sorted(self.watch_bases),
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
    watch: set[str] = set()

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
                try:
                    scores[base] = float(row.get("score") or scores.get(base, 0.0))
                except (TypeError, ValueError):
                    pass
        for row in daily.get("watch") or []:
            if not isinstance(row, dict):
                continue
            base = str(row.get("base") or "").strip().upper()
            if not base or base in avoid:
                continue
            watch.add(base)
            try:
                scores[base] = float(row.get("score") or scores.get(base, 0.0))
            except (TypeError, ValueError):
                scores.setdefault(base, 0.0)

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
        watch_bases=frozenset(watch),
        bullish_bases=frozenset(bullish),
        blocked_bases=frozenset(blocked),
        macro_active=macro,
    )

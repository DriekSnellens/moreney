"""AlphaI scored features — freshness decay, opportunity score, capital preference.

Deterministic helpers for wiring AlphaI into opportunity/capital/execution without
relaxing risk limits. Auto-apply stays false; shadow ablation measures impact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from bot.integrations.alphai.signals import AlphaITradingSignals

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HALF = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class AlphaIFeatureConfig:
    enabled: bool = True
    score_weight: Decimal = Decimal("0.06")
    freshness_half_life_hours: Decimal = Decimal("4")
    max_freshness_hours: Decimal = Decimal("24")
    adverse_bullish_wait_threshold: Decimal = Decimal("0.55")
    adverse_bullish_reduce_threshold: Decimal = Decimal("0.40")
    capital_preference_boost: Decimal = Decimal("0.15")
    capital_avoid_penalty: Decimal = Decimal("0.35")
    exit_urgency_trail_scale: Decimal = Decimal("0.70")
    exit_urgency_be_cushion_scale: Decimal = Decimal("0.55")
    bullish_trail_hold_boost: Decimal = Decimal("1.15")
    shadow_only: bool = True
    auto_apply: bool = False


@dataclass(frozen=True, slots=True)
class AlphaIFeatureAssessment:
    base: str
    feature_score: Decimal  # 0..1 for opportunity weighting
    freshness: Decimal  # 0..1 decayed
    capital_preference: Decimal  # multiplier around 1.0 (clamped)
    entry_timing: str  # NORMAL | WAIT | REDUCE
    size_multiplier: Decimal  # downward-biased when adverse×news
    exit_urgency: bool
    trail_hold_scale: Decimal
    be_harvest_gain_scale: Decimal
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "feature_score": str(self.feature_score.quantize(Decimal("0.01"))),
            "freshness": str(self.freshness.quantize(Decimal("0.01"))),
            "capital_preference": str(self.capital_preference.quantize(Decimal("0.01"))),
            "entry_timing": self.entry_timing,
            "size_multiplier": str(self.size_multiplier.quantize(Decimal("0.01"))),
            "exit_urgency": self.exit_urgency,
            "trail_hold_scale": str(self.trail_hold_scale.quantize(Decimal("0.01"))),
            "be_harvest_gain_scale": str(self.be_harvest_gain_scale.quantize(Decimal("0.01"))),
            "reasons": list(self.reasons),
        }


def config_from_settings(settings: Any) -> AlphaIFeatureConfig:
    return AlphaIFeatureConfig(
        enabled=bool(getattr(settings, "alphai_feature_scoring_enabled", True)),
        score_weight=Decimal(str(getattr(settings, "alphai_opp_weight", 0.06))),
        freshness_half_life_hours=Decimal(
            str(getattr(settings, "alphai_freshness_half_life_hours", 4.0))
        ),
        max_freshness_hours=Decimal(
            str(getattr(settings, "alphai_max_freshness_hours", 24.0))
        ),
        adverse_bullish_wait_threshold=Decimal(
            str(getattr(settings, "alphai_adverse_bullish_wait_threshold", 0.55))
        ),
        adverse_bullish_reduce_threshold=Decimal(
            str(getattr(settings, "alphai_adverse_bullish_reduce_threshold", 0.40))
        ),
        capital_preference_boost=Decimal(
            str(getattr(settings, "alphai_capital_preference_boost", 0.15))
        ),
        capital_avoid_penalty=Decimal(
            str(getattr(settings, "alphai_capital_avoid_penalty", 0.35))
        ),
        exit_urgency_trail_scale=Decimal(
            str(getattr(settings, "alphai_exit_urgency_trail_scale", 0.70))
        ),
        exit_urgency_be_cushion_scale=Decimal(
            str(getattr(settings, "alphai_exit_urgency_be_cushion_scale", 0.55))
        ),
        bullish_trail_hold_boost=Decimal(
            str(getattr(settings, "alphai_bullish_trail_hold_boost", 1.15))
        ),
        shadow_only=bool(getattr(settings, "alphai_feature_shadow_only", True)),
        auto_apply=bool(getattr(settings, "alphai_feature_auto_apply", False)),
    )


def _clamp(v: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, v))


def _parse_ts(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def freshness_factor(
    *,
    signal_age_hours: Decimal | None,
    half_life_hours: Decimal,
    max_hours: Decimal,
) -> Decimal:
    """Exponential decay: full weight at age=0, ~0.5 at half-life, ~0 at max."""
    if signal_age_hours is None:
        return Decimal("0.75")  # unknown age → mildly discounted
    if signal_age_hours <= 0:
        return _ONE
    if signal_age_hours >= max_hours:
        return _ZERO
    if half_life_hours <= 0:
        return _ONE if signal_age_hours < max_hours else _ZERO
    # 0.5 ** (age / half_life)
    ratio = float(signal_age_hours / half_life_hours)
    decay = Decimal(str(0.5 ** ratio))
    return _clamp(decay, _ZERO, _ONE)


def signal_age_hours(
    *,
    generated_at: object = None,
    published_at: object = None,
    now: datetime | None = None,
) -> Decimal | None:
    ts = _parse_ts(published_at) or _parse_ts(generated_at)
    if ts is None:
        return None
    now_dt = now or datetime.now(timezone.utc)
    seconds = (now_dt - ts).total_seconds()
    if seconds < 0:
        return _ZERO
    return Decimal(str(seconds / 3600.0))


def compute_alphai_feature(
    base: str,
    signals: AlphaITradingSignals | None,
    *,
    adverse_score: Decimal | None = None,
    signal_age_hours_value: Decimal | None = None,
    config: AlphaIFeatureConfig | None = None,
) -> AlphaIFeatureAssessment:
    """Explainable AlphaI feature for one base — decision-time info only."""
    cfg = config or AlphaIFeatureConfig()
    b = str(base or "").upper()
    reasons: list[str] = []

    if not cfg.enabled or signals is None:
        return AlphaIFeatureAssessment(
            base=b,
            feature_score=_HALF,
            freshness=_ONE,
            capital_preference=_ONE,
            entry_timing="NORMAL",
            size_multiplier=_ONE,
            exit_urgency=False,
            trail_hold_scale=_ONE,
            be_harvest_gain_scale=_ONE,
            reasons=("alphai_disabled",),
        )

    fresh = freshness_factor(
        signal_age_hours=signal_age_hours_value,
        half_life_hours=cfg.freshness_half_life_hours,
        max_hours=cfg.max_freshness_hours,
    )

    pick = Decimal(str(signals.pick_score(b)))
    # Relative conviction among live picks (handles 18–114 score ranges).
    if hasattr(signals, "pick_conviction"):
        pick_norm = _clamp(Decimal(str(signals.pick_conviction(b))), _ZERO, _ONE)
    else:
        pick_norm = _clamp((pick + Decimal("5")) / Decimal("15"), _ZERO, _ONE)

    raw = Decimal("0.45")  # neutral baseline
    if b in signals.blocked_bases or b in signals.avoid_bases:
        raw = Decimal("0.10")
        reasons.append("alphai_avoid")
    elif b in signals.bullish_bases:
        raw = Decimal("0.55") + pick_norm * Decimal("0.40")
        reasons.append("alphai_bullish_headline")
    elif signals.is_top_pick(b):
        raw = Decimal("0.50") + pick_norm * Decimal("0.45")
        reasons.append("alphai_top_pick")
    elif b in signals.daily_pick_bases and pick > 0:
        raw = Decimal("0.45") + pick_norm * Decimal("0.40")
        reasons.append("alphai_daily_pick")
    elif b in signals.watch_bases:
        raw = Decimal("0.50")
        reasons.append("alphai_watch")
    else:
        reasons.append("alphai_neutral")

    # Avoid always wins over stale bullish
    if b in signals.avoid_bases or b in signals.blocked_bases:
        raw = min(raw, Decimal("0.15"))

    # Mixed headlines pull feature score toward neutral.
    if hasattr(signals, "is_headline_mixed") and signals.is_headline_mixed(b):
        conflict = Decimal(str(signals.headline_conflict_ratio(b)))
        raw = raw * (_ONE - conflict * Decimal("0.35")) + Decimal("0.45") * conflict * Decimal(
            "0.35"
        )
        reasons.append("alphai_headline_mixed")

    feature = _clamp(raw * fresh + Decimal("0.35") * (_ONE - fresh), _ZERO, _ONE)
    if fresh < Decimal("0.35"):
        reasons.append("alphai_stale")

    # Capital preference: boost by conviction, penalize avoid — never force deploy
    pref = _ONE
    if b in signals.avoid_bases or b in signals.blocked_bases:
        pref = _ONE - cfg.capital_avoid_penalty
        reasons.append("capital_avoid_penalty")
    elif signals.is_top_pick(b) or b in signals.bullish_bases or (
        b in signals.daily_pick_bases and pick > 0
    ):
        pref = _ONE + cfg.capital_preference_boost * fresh * pick_norm
        reasons.append("capital_preference_boost")
    pref = _clamp(pref, Decimal("0.50"), Decimal("1.25"))

    # Adverse × news entry timing
    entry_timing = "NORMAL"
    size_mult = _ONE
    is_bullish_path = (
        b in signals.bullish_bases
        or (b in signals.daily_pick_bases and pick > 0)
    ) and b not in signals.avoid_bases and b not in signals.blocked_bases

    adv = adverse_score if adverse_score is not None else _ZERO
    if is_bullish_path and adv >= cfg.adverse_bullish_wait_threshold:
        entry_timing = "WAIT"
        size_mult = Decimal("0.50")
        reasons.append("alphai_adverse_wait")
    elif is_bullish_path and adv >= cfg.adverse_bullish_reduce_threshold:
        entry_timing = "REDUCE"
        size_mult = Decimal("0.75")
        reasons.append("alphai_adverse_reduce")

    # Mixed headlines: soft size trim even without adverse tape.
    if is_bullish_path and hasattr(signals, "headline_conflict_ratio"):
        conflict = Decimal(str(signals.headline_conflict_ratio(b)))
        if conflict > 0:
            size_mult = min(
                size_mult,
                Decimal(str(round(max(0.70, float(_ONE - conflict * Decimal("0.40"))), 2))),
            )
            if conflict >= Decimal("0.25") and entry_timing == "NORMAL":
                entry_timing = "REDUCE"
                reasons.append("alphai_mixed_reduce")

    # Stale bullish: never max-size.
    if is_bullish_path and fresh < Decimal("0.35"):
        size_mult = min(size_mult, Decimal("0.75"))
        if entry_timing == "NORMAL":
            entry_timing = "REDUCE"
            reasons.append("alphai_stale_reduce")

    exit_urg = signals.exit_urgency(b)
    if hasattr(signals, "is_weak_bullish_hold") and signals.is_weak_bullish_hold(b):
        # Soft urgency: harvest faster without treating as full avoid.
        reasons.append("weak_bullish_hold")
    trail_hold = _ONE
    be_scale = (
        signals.be_harvest_gain_scale(b)
        if hasattr(signals, "be_harvest_gain_scale")
        else _ONE
    )
    if exit_urg:
        trail_hold = cfg.exit_urgency_trail_scale
        be_scale = min(be_scale, cfg.exit_urgency_be_cushion_scale)
        reasons.append("exit_urgency")
    elif is_bullish_path and fresh >= Decimal("0.50"):
        # Conviction-scaled trail hold (strong ETH >> weak DOGE).
        boost_span = cfg.bullish_trail_hold_boost - _ONE
        trail_hold = (_ONE + boost_span * pick_norm * fresh).quantize(Decimal("0.01"))
        if hasattr(signals, "trail_hold_boost"):
            trail_hold = min(trail_hold, signals.trail_hold_boost(b))
        reasons.append("bullish_trail_hold")
    elif is_bullish_path and fresh < Decimal("0.35"):
        trail_hold = Decimal("0.85")
        be_scale = min(be_scale, Decimal("0.75"))
        reasons.append("stale_trail_trim")
    if signals.macro_active and not exit_urg:
        be_scale = min(be_scale, Decimal("0.80"))
        reasons.append("macro_caution")

    return AlphaIFeatureAssessment(
        base=b,
        feature_score=feature.quantize(Decimal("0.01")),
        freshness=fresh.quantize(Decimal("0.01")),
        capital_preference=pref.quantize(Decimal("0.01")),
        entry_timing=entry_timing,
        size_multiplier=size_mult,
        exit_urgency=exit_urg,
        trail_hold_scale=trail_hold.quantize(Decimal("0.01")),
        be_harvest_gain_scale=be_scale.quantize(Decimal("0.01")),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def alphai_feature_from_signals_snapshot(
    base: str,
    signals: AlphaITradingSignals | None,
    *,
    daily_generated_at: object = None,
    headline_published_at: object = None,
    adverse_score: Decimal | None = None,
    now: datetime | None = None,
    config: AlphaIFeatureConfig | None = None,
) -> AlphaIFeatureAssessment:
    age = signal_age_hours(
        generated_at=daily_generated_at,
        published_at=headline_published_at,
        now=now,
    )
    return compute_alphai_feature(
        base,
        signals,
        adverse_score=adverse_score,
        signal_age_hours_value=age,
        config=config,
    )

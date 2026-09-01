"""Parameter recommendation engine — suggest only, never auto-apply."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bot.intelligence.economic_attribution import EconomicAttributionStore, SHADOW_THRESHOLDS, _d

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ParameterRecommendation:
    parameter: str
    current: str
    recommended: str
    evidence: dict[str, Any]
    confidence: str
    auto_apply: bool = False


def _confidence(samples: int) -> str:
    if samples >= 100:
        return "HIGH"
    if samples >= 50:
        return "MEDIUM"
    if samples >= 20:
        return "LOW"
    return "INSUFFICIENT"


def recommend_adverse_threshold(
    store: EconomicAttributionStore,
    *,
    current: Decimal = Decimal("0.70"),
    calibration: list[dict[str, Any]] | None = None,
) -> ParameterRecommendation | None:
    """Recommend cancel threshold from adverse bucket calibration + shadow counts."""
    cal = calibration or store.adverse_calibration()
    if not cal:
        return None

    # Find bucket where toxic rate jumps materially
    best_thr = current
    best_score = _ZERO
    toxic_rates: list[tuple[Decimal, Decimal]] = []
    for row in cal:
        bucket = row.get("bucket") or ""
        toxic = row.get("toxic_fill_rate")
        samples = int(row.get("samples") or 0)
        if toxic is None or samples < 5:
            continue
        lo = _d(bucket.split("-")[0])
        toxic_rates.append((lo, _d(toxic)))

    if len(toxic_rates) >= 2:
        for i in range(1, len(toxic_rates)):
            lo, tr = toxic_rates[i]
            prev_tr = toxic_rates[i - 1][1]
            if tr - prev_tr > Decimal("0.15"):
                best_thr = lo
                best_score = tr - prev_tr
                break

    # Cross-check shadow threshold cancel counts
    shadow = store.shadow_threshold_cancels
    shadow_samples = sum(shadow.values()) or 1
    cancel_alpha = store.cancel_alpha_summary()

    samples = int(cancel_alpha.get("samples") or 0)
    avg_alpha = _d(cancel_alpha.get("average_cancel_alpha_eur"))

    if samples < 20 and not toxic_rates:
        return ParameterRecommendation(
            parameter="adverse_selection_cancel_threshold",
            current=str(current),
            recommended=str(current),
            evidence={
                "samples": samples,
                "note": "insufficient data for threshold change",
            },
            confidence="INSUFFICIENT",
            auto_apply=False,
        )

    recommended = best_thr if best_score > 0 else current
    if avg_alpha > _ZERO and samples >= 20:
        # Keep current if cancel alpha positive
        recommended = current

    return ParameterRecommendation(
        parameter="adverse_selection_cancel_threshold",
        current=str(current),
        recommended=str(recommended.quantize(Decimal("0.01"))),
        evidence={
            "samples": samples,
            "cancel_alpha_eur": cancel_alpha.get("average_cancel_alpha_eur"),
            "avoided_adverse_cost_eur": cancel_alpha.get("avoided_adverse_loss_eur"),
            "missed_opportunity_eur": cancel_alpha.get("missed_opportunity_eur"),
            "toxic_rate_by_bucket": cal,
            "shadow_threshold_cancels": shadow,
        },
        confidence=_confidence(samples),
        auto_apply=False,
    )


def recommend_resting_max_age(
    store: EconomicAttributionStore,
    *,
    current: float = 30.0,
) -> ParameterRecommendation:
    lock = store.capital_lock_summary()
    samples = store.cancel_alpha_samples
    return ParameterRecommendation(
        parameter="resting_max_age_sec",
        current=str(current),
        recommended=str(current),
        evidence={
            "average_lock_seconds": lock.get("average_lock_seconds"),
            "p95_lock_seconds": lock.get("p95_lock_seconds"),
            "samples": samples,
            "note": "no change until lock analytics show excess idle time",
        },
        confidence=_confidence(samples),
        auto_apply=False,
    )


def recommend_regime_scoring(
    *,
    live_scoring_enabled: bool = False,
) -> ParameterRecommendation:
    return ParameterRecommendation(
        parameter="regime_scoring_enabled",
        current=str(live_scoring_enabled),
        recommended="False",
        evidence={
            "note": "Historical replay showed 100% reject with regime scoring ON",
            "action": "Keep observation-only until sparse-data protection validated",
        },
        confidence="HIGH",
        auto_apply=False,
    )


def generate_recommendations(
    store: EconomicAttributionStore,
    *,
    adverse_threshold: Decimal = Decimal("0.70"),
    resting_max_age: float = 30.0,
    regime_scoring_enabled: bool = False,
) -> list[ParameterRecommendation]:
    recs: list[ParameterRecommendation] = []
    adv = recommend_adverse_threshold(store, current=adverse_threshold)
    if adv:
        recs.append(adv)
    recs.append(recommend_resting_max_age(store, current=resting_max_age))
    recs.append(recommend_regime_scoring(live_scoring_enabled=regime_scoring_enabled))
    return recs


def recommendations_to_dict(recs: list[ParameterRecommendation]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in recs:
        out.append({
            "parameter": r.parameter,
            "current": r.current,
            "recommended": r.recommended,
            "confidence": r.confidence,
            "auto_apply": r.auto_apply,
            "evidence": r.evidence,
        })
    return out

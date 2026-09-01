"""Shadow quote admission — never alters live execution."""

from __future__ import annotations

from decimal import Decimal

from bot.opportunity.toxicity.shrinkage import HierarchicalToxicityModel
from bot.opportunity.toxicity.types import PreTradeFeatures, ShadowDecision, ToxicityPrediction

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def shadow_admit(
    features: PreTradeFeatures,
    prediction: ToxicityPrediction,
    *,
    uncertainty_weight: Decimal = Decimal("0.5"),
    required_margin_eur: Decimal = _ZERO,
    p_fill: Decimal = Decimal("1"),
) -> ShadowDecision:
    """Compute toxicity-adjusted NET and shadow accept/reject.

    Compares in bps space when notional is known to avoid notional-estimation
    blow-ups, then converts back to EUR for reporting:

        extra_adverse_bps = max(0, predicted_bps − buffer_bps)
        unc_penalty_bps = uncertainty_bps × weight / sqrt(n+1)
        edge_bps = expected_net_bps − extra_adverse_bps − unc_penalty_bps
        accept iff p_fill × edge_bps > 0

    Does not loosen fills or live gates.
    """
    notional = features.notional_eur
    before = features.expected_net_eur
    if notional > 0:
        buffer_bps = features.expected_buffer_eur / notional * _BPS
        expected_net_bps = before / notional * _BPS
        extra_bps = max(_ZERO, prediction.expected_adverse_bps - buffer_bps)
        n = max(0, int(prediction.sample_count))
        # Uncertainty penalty shrinks as evidence accumulates.
        unc_bps = prediction.uncertainty_bps * uncertainty_weight / Decimal(str((n + 1) ** 0.5))
        edge_bps = expected_net_bps - extra_bps - unc_bps
        extra_adverse = extra_bps / _BPS * notional
        unc_penalty = unc_bps / _BPS * notional
        adjusted = edge_bps / _BPS * notional - required_margin_eur
        ev = p_fill * (edge_bps / _BPS * notional - required_margin_eur)
    else:
        already = features.expected_buffer_eur
        extra_adverse = max(_ZERO, prediction.expected_adverse_eur - already)
        unc_penalty = prediction.uncertainty_bps * uncertainty_weight / Decimal("100")
        adjusted = before - extra_adverse - unc_penalty - required_margin_eur
        ev = p_fill * adjusted

    if ev > 0:
        return ShadowDecision(
            accept=True,
            reason="shadow_toxicity_accept",
            expected_net_before_toxicity=before,
            expected_adverse_eur=prediction.expected_adverse_eur,
            uncertainty_penalty_eur=unc_penalty,
            toxicity_adjusted_net=adjusted,
            prediction=prediction,
        )
    return ShadowDecision(
        accept=False,
        reason="shadow_toxicity_reject",
        expected_net_before_toxicity=before,
        expected_adverse_eur=prediction.expected_adverse_eur,
        uncertainty_penalty_eur=unc_penalty,
        toxicity_adjusted_net=adjusted,
        prediction=prediction,
    )


def predict_and_shadow(
    model: HierarchicalToxicityModel,
    features: PreTradeFeatures,
    **kwargs: object,
) -> ShadowDecision:
    pred = model.predict(features)
    return shadow_admit(features, pred, **kwargs)  # type: ignore[arg-type]

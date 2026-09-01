"""Edge magnitude vs costs and vs predeclared model uncertainty."""

from __future__ import annotations

from typing import Any

from bot.research.robustness.protocol import (
    ADVERSE_UNCERTAINTY_BPS,
    EDGE_TO_UNCERTAINTY_FAIL,
    FEE_UNCERTAINTY_REL,
    FILL_MODEL_UNCERTAINTY_BPS,
    LATENCY_MS_TO_BPS,
    LATENCY_UNCERTAINTY_MS,
    NOTIONAL_EUR,
    SLIP_UNCERTAINTY_BPS,
)
from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.economics import round_trip_fee_rate


def _bps(eur: float, notional: float = NOTIONAL_EUR) -> float:
    if notional <= 0:
        return 0.0
    return float(eur) / float(notional) * 10000.0


def magnitude(
    *,
    expected_net: float,
    venue: str,
    venue_exit: str | None,
    mean_forward: float | None,
) -> dict[str, Any]:
    fee_rate = float(round_trip_fee_rate(venue, venue_exit))
    fees_eur = NOTIONAL_EUR * fee_rate
    slip_eur = NOTIONAL_EUR * (SLIPPAGE_BPS_DEFAULT / 10000.0)
    adv_eur = NOTIONAL_EUR * (ADVERSE_BPS_DEFAULT / 10000.0)
    lat_eur = NOTIONAL_EUR * (LATENCY_PENALTY_BPS / 10000.0)
    cost_eur = fees_eur + slip_eur + adv_eur + lat_eur
    # Reconstruct gross from the existing waterfall identity.
    gross_eur = float(expected_net) + cost_eur
    if mean_forward is not None:
        gross_from_fwd = NOTIONAL_EUR * abs(float(mean_forward))
        # Prefer the identity-consistent gross; record the forward-implied value.
    else:
        gross_from_fwd = None
    fee_bps = _bps(fees_eur)
    slip_bps = SLIPPAGE_BPS_DEFAULT
    adv_bps = ADVERSE_BPS_DEFAULT
    lat_bps = LATENCY_PENALTY_BPS
    buffer_bps = adv_bps + lat_bps
    gross_bps = _bps(gross_eur)
    net_bps = _bps(expected_net)
    net_per_notional = float(expected_net) / NOTIONAL_EUR
    net_per_fill_replay_note = "see accounting units; not used as unlabeled primary"
    edge_to_cost = (gross_eur / cost_eur) if cost_eur else None
    unc_bps = (
        fee_bps * FEE_UNCERTAINTY_REL
        + SLIP_UNCERTAINTY_BPS
        + ADVERSE_UNCERTAINTY_BPS
        + FILL_MODEL_UNCERTAINTY_BPS
        + LATENCY_UNCERTAINTY_MS * LATENCY_MS_TO_BPS
    )
    unc_eur = NOTIONAL_EUR * unc_bps / 10000.0
    edge_to_unc = (abs(float(expected_net)) / unc_eur) if unc_eur else None
    too_high = edge_to_unc is not None and edge_to_unc < EDGE_TO_UNCERTAINTY_FAIL
    return {
        "NET_per_fill_primary": {
            "value": float(expected_net),
            "unit": "EUR_per_signal",
            "definition": "EXPECTED_NET from the unchanged waterfall (notional 100 EUR).",
        },
        "NET_per_bps": {
            "value": (float(expected_net) / gross_bps) if gross_bps else None,
            "unit": "EUR_per_gross_bps",
            "definition": "EXPECTED_NET / gross_edge_bps.",
        },
        "NET_per_notional": {
            "value": net_per_notional,
            "unit": "fraction_of_notional",
            "definition": "EXPECTED_NET / notional.",
        },
        "gross_edge_bps": {"value": gross_bps, "unit": "bps_of_notional"},
        "fee_bps": {"value": fee_bps, "unit": "bps_of_notional"},
        "slippage_bps": {"value": slip_bps, "unit": "bps_of_notional"},
        "adverse_bps": {"value": adv_bps, "unit": "bps_of_notional"},
        "execution_buffer_bps": {"value": buffer_bps, "unit": "bps_of_notional"},
        "latency_bps": {"value": lat_bps, "unit": "bps_of_notional"},
        "net_bps": {"value": net_bps, "unit": "bps_of_notional"},
        "cost_eur_per_signal": {"value": cost_eur, "unit": "EUR_per_signal"},
        "gross_eur_per_signal": {"value": gross_eur, "unit": "EUR_per_signal"},
        "gross_from_abs_forward": {"value": gross_from_fwd, "unit": "EUR_per_signal"},
        "EDGE_TO_COST_RATIO": edge_to_cost,
        "EDGE_TO_MODEL_UNCERTAINTY_RATIO": edge_to_unc,
        "model_uncertainty_bps": {"value": unc_bps, "unit": "bps_of_notional"},
        "model_uncertainty_eur": {"value": unc_eur, "unit": "EUR_per_signal"},
        "MODEL_UNCERTAINTY_TOO_HIGH": too_high,
        "NET_per_fill_replay_note": net_per_fill_replay_note,
        "fee_rate_roundtrip": fee_rate,
    }

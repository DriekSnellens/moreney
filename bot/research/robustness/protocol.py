"""Frozen edge-robustness protocol. Does not change production costs or hypotheses."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    MAX_TOP_SYMBOL_PNL_SHARE,
    NOTIONAL_EUR_DEFAULT,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.economics import execution_replay_net
from bot.research.tournament.freeze import git_commit
from bot.research.regime_lab.protocol import FORENSIC_OOS_END_NS, H0005, H0007, LOOKBACK_BUFFER_NS

PACKAGE_LABEL = "EDGE_ROBUSTNESS_LAB"
PROTOCOL_VERSION = "robustness_lab_v1"
RANDOM_SEED = 20260817

# Historical first-lab OOS (immutable experiment boundary — not a tuned parameter).
FIRST_LAB_OOS_START_NS = 1_786_974_431_156_051_072
FIRST_LAB_OOS_END_NS = 1_786_977_290_774_087_936

# Frozen hypothesis params from the first lab freeze. Do not retune.
FROZEN_H0005_PARAMS = {
    "dislocation_bps": 40.0,
    "follower": "bitvavo",
    "horizon_ms": 5000,
    "leader": "okx",
    "venue_a": "okx",
    "venue_b": "bitvavo",
}
FROZEN_H0007_PARAMS = {
    "deviation_bps": 20.0,
    "horizon_ms": 5000,
    "venue": "bitvavo",
}

FILL_RATE_BASELINE = float(execution_replay_net(expected_net=0.0)["fill_rate"])
ADVERSE_EXTRA_BPS = float(execution_replay_net(expected_net=0.0)["adverse_extra_bps"])
NOTIONAL_EUR = float(NOTIONAL_EUR_DEFAULT)

# Gate selectivity floor (predeclared; not fit on OOS).
MIN_GATE_SELECTIVITY = 0.05

# Sequential walk-forward windows after the first OOS.
WINDOW_SECONDS = 1800.0
MIN_COMPLETE_WINDOW_SECONDS = 1800.0
MIN_REGIME_OBS = 30
MIN_WINDOW_SIGNALS = 30

# Research-only latency conversion. Not a production fill/latency change.
# 1 ms additional delay → 0.01 bps extra latency penalty (500 ms → 5 bps).
LATENCY_MS_TO_BPS = 0.01

# Research-only model-uncertainty band (bps of notional). Not production.
FEE_UNCERTAINTY_REL = 0.10
SLIP_UNCERTAINTY_BPS = 2.0
ADVERSE_UNCERTAINTY_BPS = 2.0
# Fill-model extra adverse already in execution_replay.
FILL_MODEL_UNCERTAINTY_BPS = ADVERSE_EXTRA_BPS
LATENCY_UNCERTAINTY_MS = 50.0

EDGE_TO_UNCERTAINTY_FAIL = 1.0

# Stress grids (research-only overlay).
FEE_MULTS = (1.0, 1.10, 1.25, 1.50)
SLIP_ADD_BPS = (0.0, 1.0, 2.0, 5.0)
ADVERSE_ADD_BPS = (0.0, 1.0, 2.0, 5.0, 10.0)
FILL_PROBS = (FILL_RATE_BASELINE, 0.90, 0.75, 0.50)
LATENCY_ADD_MS = (0.0, 10.0, 50.0, 100.0, 500.0)
PARTIAL_RATIOS = (1.00, 0.90, 0.75, 0.50)

# Predeclared combined "reasonable" stress — not cherry-picked after seeing results.
REASONABLE_STRESS = {
    "fee_mult": 1.25,
    "slip_add_bps": 2.0,
    "adverse_add_bps": 2.0,
    "fill_prob": 0.50,
    "latency_add_ms": 50.0,
    "partial_ratio": 0.75,
}

WINDOW_DOMINANCE_SHARE = MAX_TOP_SYMBOL_PNL_SHARE  # 0.70, same cap, not relaxed

HISTORICAL_MECHANICAL = {
    "H-0005": "OOS_PASS",
    "H-0007": "OOS_PASS",
}

STRIDE = 4


def protocol_payload() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "random_seed": RANDOM_SEED,
        "forensic_oos_end_ns": FORENSIC_OOS_END_NS,
        "first_lab_oos_start_ns": FIRST_LAB_OOS_START_NS,
        "first_lab_oos_end_ns": FIRST_LAB_OOS_END_NS,
        "lookback_buffer_ns": LOOKBACK_BUFFER_NS,
        "frozen_h0005_params": FROZEN_H0005_PARAMS,
        "frozen_h0007_params": FROZEN_H0007_PARAMS,
        "min_gate_selectivity": MIN_GATE_SELECTIVITY,
        "window_seconds": WINDOW_SECONDS,
        "min_regime_obs": MIN_REGIME_OBS,
        "fill_rate_baseline": FILL_RATE_BASELINE,
        "adverse_extra_bps": ADVERSE_EXTRA_BPS,
        "notional_eur": NOTIONAL_EUR,
        "latency_ms_to_bps": LATENCY_MS_TO_BPS,
        "fee_uncertainty_rel": FEE_UNCERTAINTY_REL,
        "slip_uncertainty_bps": SLIP_UNCERTAINTY_BPS,
        "adverse_uncertainty_bps": ADVERSE_UNCERTAINTY_BPS,
        "fill_model_uncertainty_bps": FILL_MODEL_UNCERTAINTY_BPS,
        "latency_uncertainty_ms": LATENCY_UNCERTAINTY_MS,
        "edge_to_uncertainty_fail": EDGE_TO_UNCERTAINTY_FAIL,
        "fee_mults": list(FEE_MULTS),
        "slip_add_bps": list(SLIP_ADD_BPS),
        "adverse_add_bps": list(ADVERSE_ADD_BPS),
        "fill_probs": list(FILL_PROBS),
        "latency_add_ms": list(LATENCY_ADD_MS),
        "partial_ratios": list(PARTIAL_RATIOS),
        "reasonable_stress": REASONABLE_STRESS,
        "window_dominance_share": WINDOW_DOMINANCE_SHARE,
        "historical_mechanical": HISTORICAL_MECHANICAL,
        "stride": STRIDE,
        "production_assumptions_loosened": False,
        "execution_enabled": False,
        "h0005": H0005,
        "h0007": H0007,
        "baseline_adverse_bps": ADVERSE_BPS_DEFAULT,
        "baseline_slippage_bps": SLIPPAGE_BPS_DEFAULT,
        "baseline_latency_bps": LATENCY_PENALTY_BPS,
        "note": "Research-only overlay. Mechanical OOS_PASS unchanged. Do not retune.",
    }


def protocol_hash() -> str:
    return hashlib.sha256(
        json.dumps(protocol_payload(), sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def build_manifest(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    man = {
        "configuration_hash": protocol_hash(),
        "code_commit": git_commit(),
        "random_seed": RANDOM_SEED,
        "protocol_version": PROTOCOL_VERSION,
        "immutable": True,
        "protocol": protocol_payload(),
    }
    if extra:
        man.update(extra)
    return man

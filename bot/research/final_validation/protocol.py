"""Frozen final-validation protocol.

Written and hashed BEFORE replay. Do not edit after seeing results.
Does not change production execution, fees, fills, or strategy parameters.

Scenario overlay values are taken from the existing robustness-lab grids
(`FEE_MULTS`, `SLIP_ADD_BPS`, `ADVERSE_ADD_BPS`, `FILL_PROBS`,
`LATENCY_ADD_MS`, `PARTIAL_RATIOS`, `REASONABLE_STRESS`, uncertainty bands).
None were chosen to improve PnL.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bot.research.execution_realism.config import (
    EXECUTION_REALISM_PRODUCTION_ENABLED,
    MIN_INDEPENDENT_WINDOWS,
    NOTIONAL_EUR,
)
from bot.research.robustness.protocol import (
    ADVERSE_ADD_BPS,
    ADVERSE_EXTRA_BPS,
    ADVERSE_UNCERTAINTY_BPS,
    FEE_MULTS,
    FEE_UNCERTAINTY_REL,
    FILL_PROBS,
    FILL_RATE_BASELINE,
    FROZEN_H0005_PARAMS,
    FROZEN_H0007_PARAMS,
    LATENCY_ADD_MS,
    LATENCY_MS_TO_BPS,
    LATENCY_UNCERTAINTY_MS,
    PARTIAL_RATIOS,
    REASONABLE_STRESS,
    SLIP_ADD_BPS,
    SLIP_UNCERTAINTY_BPS,
    WINDOW_DOMINANCE_SHARE,
)
from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.freeze import git_commit

# HEDGE_DELAY_MS lives in execution_realism.config, not robustness.
from bot.research.execution_realism.config import HEDGE_DELAY_MS as _HEDGE_DELAY_MS

PACKAGE_LABEL = "CROSS_VENUE_DISLOCATION_FINAL_VALIDATION"
PROTOCOL_VERSION = "final_validation_v1"
ARTIFACT_SCHEMA_VERSION = "final_validation_v1"
RANDOM_SEED = 20260818
STRATEGY_ID = "cross_venue_dislocation"
PARENT_HYPOTHESIS_ID = "H-0001"

# ------------------------------------------------------------------
# FROZEN RESEARCH UNIVERSE — do not add families
# ------------------------------------------------------------------
UNIVERSE: dict[str, dict[str, str]] = {
    "H-0007": {
        "classification": "REJECT",
        "status": "GATE_INACTIVE",
        "reason": (
            "The wide-spread regime gate does not materially change the traded "
            "universe. It has no demonstrated incremental value."
        ),
    },
    "H-0005": {
        "classification": "REJECT_AS_INCREMENTAL_FILTER",
        "status": "REJECTED_AS_IMPROVEMENT",
        "reason": (
            "The quote freshness gate removes economically positive parent trades. "
            "Paired delta is negative across the published windows. "
            "Do NOT retune quote_age_ms."
        ),
    },
    "cross_venue_dislocation": {
        "classification": "PRIMARY_VALIDATION_CANDIDATE",
        "status": "UNDER_VALIDATION",
        "reason": (
            "Only currently observed strategy with meaningful positive canonical "
            "replay evidence across multiple independent windows."
        ),
    },
}

ALLOWED_STRATEGY_IDS = frozenset(UNIVERSE)
NEW_STRATEGIES_CREATED: tuple[str, ...] = ()
HYPOTHESIS_GENERATOR_ENABLED = False

VERDICTS = (
    "ROBUST_PAPER_CANDIDATE",
    "PROMISING_BUT_INSUFFICIENT",
    "EXECUTION_FRAGILE",
    "REJECTED",
)

# ------------------------------------------------------------------
# FROZEN SCENARIO MATRIX (5 cells). Values from existing grids only.
# ------------------------------------------------------------------
# BASELINE fill_prob=1.0 matches canonical accounting: waterfall is applied
# to every candidate. FILL_RATE_BASELINE (0.55) is used only as the
# EstimatedFillCount denominator, not as a miss model.
# Missed-fill degradation begins at MILD (FILL_PROBS 0.90).

_FAST_HEDGE_MS = float(_HEDGE_DELAY_MS["FAST"])
_NORMAL_HEDGE_MS = float(_HEDGE_DELAY_MS["NORMAL"])
_SLOW_HEDGE_MS = float(_HEDGE_DELAY_MS["SLOW"])

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario_id": "BASELINE",
        "classification": "BASELINE",
        "description": "Frozen canonical execution assumptions. No extra degradation.",
        "fill_model": "EXISTING_TRADE_THROUGH",
        "latency_scenario": "IDEALIZED",
        "hedge_scenario": "INSTANT",
        "cancel_scenario": "NORMAL",
        "fill_prob": 1.0,
        "fee_mult": 1.0,
        "slip_add_bps": 0.0,
        "adverse_add_bps": 0.0,
        "latency_add_ms": 0.0,
        "hedge_delay_ms": 0.0,
        "partial_ratio": 1.0,
    },
    {
        "scenario_id": "MILD_REALISM",
        "classification": "REALISTIC",
        "description": (
            "One-step model-uncertainty band: fee +10%, +2 bps slip, +2 bps adverse, "
            "+50 ms latency, fill_prob=0.90, partial_ratio=0.90, hedge FAST."
        ),
        "fill_model": "EXISTING_TRADE_THROUGH",
        "latency_scenario": "FAST",
        "hedge_scenario": "FAST",
        "cancel_scenario": "NORMAL",
        "fill_prob": 0.90,  # FILL_PROBS
        "fee_mult": 1.0 + FEE_UNCERTAINTY_REL,  # 1.10
        "slip_add_bps": float(SLIP_UNCERTAINTY_BPS),  # 2.0
        "adverse_add_bps": float(ADVERSE_UNCERTAINTY_BPS),  # 2.0
        "latency_add_ms": float(LATENCY_UNCERTAINTY_MS),  # 50.0
        "hedge_delay_ms": _FAST_HEDGE_MS,  # 10.0
        "partial_ratio": 0.90,  # PARTIAL_RATIOS
    },
    {
        "scenario_id": "MODERATE_REALISM",
        "classification": "REALISTIC",
        "description": (
            "Predeclared REASONABLE_STRESS from the robustness lab: fee 1.25, "
            "+2 bps slip, +2 bps adverse, fill_prob=0.50, latency +50 ms, "
            "partial_ratio=0.75, hedge NORMAL."
        ),
        "fill_model": "EXISTING_TRADE_THROUGH",
        "latency_scenario": "NORMAL",
        "hedge_scenario": "NORMAL",
        "cancel_scenario": "NORMAL",
        "fill_prob": float(REASONABLE_STRESS["fill_prob"]),
        "fee_mult": float(REASONABLE_STRESS["fee_mult"]),
        "slip_add_bps": float(REASONABLE_STRESS["slip_add_bps"]),
        "adverse_add_bps": float(REASONABLE_STRESS["adverse_add_bps"]),
        "latency_add_ms": float(REASONABLE_STRESS["latency_add_ms"]),
        "hedge_delay_ms": _NORMAL_HEDGE_MS,
        "partial_ratio": float(REASONABLE_STRESS["partial_ratio"]),
    },
    {
        "scenario_id": "HARSH_REALISM",
        "classification": "STRESS",
        "description": (
            "Conservative grid corner still inside robustness tuples: fee 1.50, "
            "+5 bps slip, +5 bps adverse, fill_prob=0.50, latency +100 ms, "
            "partial_ratio=0.50, hedge SLOW."
        ),
        "fill_model": "EXISTING_TRADE_THROUGH",
        "latency_scenario": "SLOW",
        "hedge_scenario": "SLOW",
        "cancel_scenario": "NORMAL",
        "fill_prob": 0.50,
        "fee_mult": 1.50,
        "slip_add_bps": 5.0,
        "adverse_add_bps": 5.0,
        "latency_add_ms": 100.0,
        "hedge_delay_ms": _SLOW_HEDGE_MS,
        "partial_ratio": 0.50,
    },
    {
        "scenario_id": "STRESS",
        "classification": "ADVERSARIAL",
        "description": (
            "Pessimistic upper bound on the same grids: fee 1.50, +5 bps slip, "
            "+10 bps adverse, fill_prob=0.50, latency +500 ms, partial_ratio=0.50, "
            "hedge SLOW."
        ),
        "fill_model": "EXISTING_TRADE_THROUGH",
        "latency_scenario": "STRESSED",
        "hedge_scenario": "SLOW",
        "cancel_scenario": "NORMAL",
        "fill_prob": 0.50,
        "fee_mult": 1.50,
        "slip_add_bps": 5.0,
        "adverse_add_bps": 10.0,
        "latency_add_ms": 500.0,
        "hedge_delay_ms": _SLOW_HEDGE_MS,
        "partial_ratio": 0.50,
    },
)

# Dimensions the tape cannot support without changing signal discovery
# or fabricating microstructure.
UNSUPPORTED_BY_DATA: tuple[dict[str, str], ...] = (
    {
        "dimension": "quote_disappearance_after_decision",
        "status": "UNSUPPORTED_BY_DATA",
        "reason": (
            "Quote lifetime until fill is not on the L1 tape. Applying extra "
            "staleness would change signal discovery (forbidden) or invent fills."
        ),
    },
    {
        "dimension": "missing_follower_book",
        "status": "UNSUPPORTED_BY_DATA",
        "reason": (
            "Follower-book dropout is not an independent tape event stream. "
            "Not fabricated."
        ),
    },
    {
        "dimension": "cross_venue_sync_beyond_exchange_ts_flag",
        "status": "UNSUPPORTED_BY_DATA",
        "reason": (
            "Bitvavo exchange_ts coverage may be 0%. Uncertainty is flagged, "
            "not replaced with a synthetic clock."
        ),
    },
)

# ------------------------------------------------------------------
# FROZEN DECISION RULES (before replay)
# ------------------------------------------------------------------
DECISION_RULES: dict[str, Any] = {
    "min_independent_windows_for_robust": MIN_INDEPENDENT_WINDOWS,  # 20
    "window_dominance_cap": WINDOW_DOMINANCE_SHARE,  # 0.70
    "mild_must_stay_positive": True,
    "moderate_must_stay_positive_for_robust": True,
    "harsh_and_stress_may_fail": True,
    "baseline_must_pass_accounting": True,
    "ROBUST_PAPER_CANDIDATE": (
        "canonical accounting PASS; BASELINE execution_net > 0; "
        "MILD_REALISM execution_net > 0; MODERATE_REALISM execution_net > 0; "
        "MODERATE positive_windows > negative_windows; "
        f"complete windows >= {MIN_INDEPENDENT_WINDOWS}; "
        f"top_window_share < {WINDOW_DOMINANCE_SHARE}; "
        "no unsupported dimension is required for profitability."
    ),
    "PROMISING_BUT_INSUFFICIENT": (
        "BASELINE and MILD positive, but MODERATE is weak/unstable, "
        "or complete windows < min_independent_windows_for_robust, "
        "or a single window dominates economics."
    ),
    "EXECUTION_FRAGILE": (
        "BASELINE positive but MODERATE_REALISM execution_net <= 0, "
        "or profitability depends on optimistic fill/latency (MILD holds, "
        "MODERATE fails)."
    ),
    "REJECTED": (
        "BASELINE execution_net <= 0, or accounting FAIL, "
        "or MILD_REALISM execution_net <= 0, "
        "or MILD negative across a majority of independent windows."
    ),
}

NEXT_ACTIONS: dict[str, str] = {
    "ROBUST_PAPER_CANDIDATE": (
        "Start SHADOW_PAPER_VALIDATION with the strategy frozen; "
        "do not enable production execution; do not optimize parameters."
    ),
    "PROMISING_BUT_INSUFFICIENT": (
        "Collect additional unseen tape until 20 complete independent windows "
        "exist; do not retune; do not create new hypotheses."
    ),
    "EXECUTION_FRAGILE": (
        "Reject the strategy as a paper candidate; do not retune automatically; "
        "do not create a new gate or hypothesis."
    ),
    "REJECTED": (
        "Archive cross-venue dislocation and stop research on this family."
    ),
}

SHADOW_PAPER_SPEC: dict[str, Any] = {
    "enabled": False,
    "production_execution": "DISABLED",
    "strategy_frozen": True,
    "parameter_optimization": False,
    "hypothesis_generation": False,
    "predeclared_min_complete_windows": MIN_INDEPENDENT_WINDOWS,
    "predeclared_min_calendar_days": 7,
    "record_every_candidate": True,
    "compare_expected_vs_realized": True,
}


def protocol_payload() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "random_seed": RANDOM_SEED,
        "package": PACKAGE_LABEL,
        "strategy_id": STRATEGY_ID,
        "parent_hypothesis_id": PARENT_HYPOTHESIS_ID,
        "universe": UNIVERSE,
        "new_strategies_created": list(NEW_STRATEGIES_CREATED),
        "hypothesis_generator_enabled": HYPOTHESIS_GENERATOR_ENABLED,
        "production_execution_enabled": EXECUTION_REALISM_PRODUCTION_ENABLED,
        "scenarios": [dict(s) for s in SCENARIOS],
        "unsupported_by_data": [dict(u) for u in UNSUPPORTED_BY_DATA],
        "decision_rules": DECISION_RULES,
        "next_actions": NEXT_ACTIONS,
        "frozen_h0005_params": FROZEN_H0005_PARAMS,
        "frozen_h0007_params": FROZEN_H0007_PARAMS,
        "fill_rate_baseline_estimated_count_only": FILL_RATE_BASELINE,
        "adverse_bps_canonical": ADVERSE_BPS_DEFAULT,
        "slippage_bps_canonical": SLIPPAGE_BPS_DEFAULT,
        "latency_bps_canonical": LATENCY_PENALTY_BPS,
        "adverse_extra_bps_sidecar": ADVERSE_EXTRA_BPS,
        "latency_ms_to_bps": LATENCY_MS_TO_BPS,
        "notional_eur": str(NOTIONAL_EUR),
        "source_grids": {
            "fee_mults": list(FEE_MULTS),
            "slip_add_bps": list(SLIP_ADD_BPS),
            "adverse_add_bps": list(ADVERSE_ADD_BPS),
            "fill_probs": list(FILL_PROBS),
            "latency_add_ms": list(LATENCY_ADD_MS),
            "partial_ratios": list(PARTIAL_RATIOS),
            "reasonable_stress": dict(REASONABLE_STRESS),
        },
        "shadow_paper_spec": SHADOW_PAPER_SPEC,
    }


def protocol_hash() -> str:
    return hashlib.sha256(
        json.dumps(protocol_payload(), sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def scenario_config_hash(scenario: dict[str, Any]) -> str:
    payload = {
        "scenario": {k: scenario[k] for k in sorted(scenario)},
        "protocol_hash": protocol_hash(),
        "schema_version": ARTIFACT_SCHEMA_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def matrix_fingerprint() -> str:
    payload = [scenario_config_hash(s) for s in SCENARIOS]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def build_manifest(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {
        "configuration_hash": protocol_hash(),
        "matrix_fingerprint": matrix_fingerprint(),
        "code_commit": git_commit(),
        "protocol_version": PROTOCOL_VERSION,
        "immutable": True,
        "protocol": protocol_payload(),
    }
    if extra:
        m.update(extra)
    return m

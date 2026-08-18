"""Frozen SHADOW_PAPER_VALIDATION protocol.

Predeclared before live collection. Do not edit after seeing live results.
Does not change production execution, fees, fills, or strategy parameters.
Does not invent strategies, retune thresholds, or invoke the LLM.

Observation latency values are copied from the already-frozen execution-realism
NORMAL/FAST hedge delays. They are not a new optimization.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bot.research.execution_realism.config import (
    EXECUTION_REALISM_PRODUCTION_ENABLED,
    HEDGE_DELAY_MS,
)
from bot.research.final_validation.protocol import UNIVERSE as FINAL_UNIVERSE
from bot.research.robustness.protocol import (
    FROZEN_H0005_PARAMS,
    WINDOW_DOMINANCE_SHARE,
    WINDOW_SECONDS,
)
from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    NOTIONAL_EUR_DEFAULT,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.economics import round_trip_fee_rate
from bot.research.tournament.freeze import git_commit

PACKAGE_LABEL = "SHADOW_PAPER_VALIDATION"
PROTOCOL_VERSION = "shadow_paper_validation_v1"
# Artifact layout for the live scorecard. Independent of PROTOCOL_VERSION
# (frozen acceptance). Incompatible with v1 run directories.
ARTIFACT_SCHEMA_VERSION = "shadow_scorecard_v1"
RANDOM_SEED = 20260818
RUNTIME_ID_LIVE = "live_paper"

STRATEGY_ID = "cross_venue_dislocation"
STRATEGY_DISPLAY_NAME = "Cross-Venue Dislocation"
PARENT_HYPOTHESIS_ID = "H-0001"

# ------------------------------------------------------------------
# HARD FREEZE — copied from the validated parent. Do not retune.
# ------------------------------------------------------------------
FROZEN_PARAMS: dict[str, Any] = dict(FROZEN_H0005_PARAMS)
VENUE_A = str(FROZEN_PARAMS["venue_a"])  # okx
VENUE_B = str(FROZEN_PARAMS["venue_b"])  # bitvavo
ROUTE = f"{VENUE_A}|{VENUE_B}"
ROUTE_UNIVERSE: tuple[str, ...] = (ROUTE,)
DISLOCATION_BPS = float(FROZEN_PARAMS["dislocation_bps"])
HORIZON_MS = int(FROZEN_PARAMS["horizon_ms"])

NOTIONAL_EUR = float(NOTIONAL_EUR_DEFAULT)
ADVERSE_BPS = float(ADVERSE_BPS_DEFAULT)
SLIPPAGE_BPS = float(SLIPPAGE_BPS_DEFAULT)
LATENCY_BPS = float(LATENCY_PENALTY_BPS)
FEE_RATE_ROUNDTRIP = float(round_trip_fee_rate(VENUE_A, VENUE_B))

# Observation schedule (frozen execution-realism delays — not new knobs).
ENTRY_OBSERVE_MS = float(HEDGE_DELAY_MS["FAST"])  # 10 ms
HEDGE_OBSERVE_MS = float(HEDGE_DELAY_MS["NORMAL"])  # 50 ms

# Data-quality STALE bound: book older than the signal horizon cannot be
# treated as a live executable quote. This is NOT H-0005 quote_age_ms=250.
MAX_DECISION_STALE_MS = float(HORIZON_MS)

# Hedge classified WORSE when live hedge ask/bid moves against us by more
# than the already-frozen slippage+latency buffer.
HEDGE_WORSE_BPS = SLIPPAGE_BPS + LATENCY_BPS

WINDOW_SECONDS_LIVE = float(WINDOW_SECONDS)

# ------------------------------------------------------------------
# RESEARCH LOCK — no open-ended discovery during this phase
# ------------------------------------------------------------------
HYPOTHESIS_GENERATOR_ENABLED = False
AUTOMATIC_RETUNING_ALLOWED = False
AUTOMATIC_OPTIMIZATION_ALLOWED = False
NEW_STRATEGIES_CREATED: tuple[str, ...] = ()
NEW_REGIME_GATES_ALLOWED = False
PRODUCTION_EXECUTION_ENABLED = False
PAPER_EXECUTOR_LIVE_TRADING_ENABLED = False
SHADOW_PAPER_VALIDATION_ACTIVE = True
RESEARCH_LOCKED = True

H0005_STATUS = FINAL_UNIVERSE["H-0005"]["classification"]  # REJECT_AS_INCREMENTAL_FILTER
H0007_STATUS = FINAL_UNIVERSE["H-0007"]["classification"]  # REJECT

# ------------------------------------------------------------------
# Historical final-validation baseline (published; not recomputed here)
# ------------------------------------------------------------------
HISTORICAL_FINAL_VALIDATION: dict[str, Any] = {
    "FINAL_VALIDATION_VERDICT": "ROBUST_PAPER_CANDIDATE",
    "n_windows": 62,
    "n_candidates": 67443,
    "n_canonical_fills": 67443,
    "fill_model": "CANONICAL_WATERFALL_EVERY_CANDIDATE",
    "expected_fill_assumption": "1.0_canonical_fill_per_candidate",
    "BASELINE_EXECUTION_NET_EUR": 212011.78,
    "MILD_REALISM_EXECUTION_NET_EUR": 166564.01,
    "MODERATE_REALISM_EXECUTION_NET_EUR": 75449.09,
    "HARSH_REALISM_EXECUTION_NET_EUR": 46421.48,
    "STRESS_EXECUTION_NET_EUR": 44461.66,
    "positive_windows": 62,
    "negative_windows": 0,
    "accounting": "PASS",
    "top_window_share": 0.039,
    "production_execution": "DISABLED",
}

# ------------------------------------------------------------------
# Sample requirement (do not stop early)
# ------------------------------------------------------------------
MIN_COMPLETE_WINDOWS = 20
MIN_CALENDAR_DAYS = 7
# Volume floor: if 20 windows × 7 days still have almost no valid
# observations, keep collecting. Not chosen to fit a live result.
MIN_VALID_OBSERVATIONS = 100

# Collection target beyond the official minimum. NOT a verdict threshold.
# After the frozen minimum, keep collecting passively toward this.
PREFERRED_COMPLETE_WINDOWS = 50
PREFERRED_CALENDAR_DAYS = 14
EARLY_STOP_IF_POSITIVE = False
EARLY_STOP_IF_NEGATIVE = False

# Descriptive markout horizons (ms). Not a strategy-horizon retune.
# Strategy decision horizon remains HORIZON_MS (5000).
MARKOUT_HORIZONS_MS: tuple[int, ...] = (1000, 5000, 30000, 60000)

# ------------------------------------------------------------------
# Predeclared acceptance thresholds. Frozen before collection.
# ------------------------------------------------------------------
MAX_DATA_INVALID_RATE = 0.25
MIN_FILL_RATE_VALIDATED = 0.25
MIN_FILL_RATE_NOT_FRAGILE = 0.10
MAX_HEDGE_FAILURE_RATE = 0.35
MIN_QUOTE_SURVIVAL_RATE = 0.20
MIN_FOLLOWER_AVAILABILITY_RATE = 0.50
MAX_MEAN_GAP_RATIO = 0.75  # |mean gap| vs |mean expected_net| when gap is negative
UNCERTAINTY_GAP_RATIO = 0.40
MAX_TOP_WINDOW_SHARE = float(WINDOW_DOMINANCE_SHARE)  # 0.70

VERDICTS = (
    "SHADOW_VALIDATED",
    "SHADOW_PROMISING",
    "SHADOW_EXECUTION_FRAGILE",
    "SHADOW_REJECTED",
    "INSUFFICIENT_LIVE_SAMPLE",
)

NEXT_ACTIONS: dict[str, str] = {
    "SHADOW_VALIDATED": "PROPOSE_LIMITED_PAPER_EXECUTION",
    "SHADOW_PROMISING": "CONTINUE_UNTIL_SAMPLE_COMPLETE",
    "SHADOW_EXECUTION_FRAGILE": "REJECT_STRATEGY",
    "SHADOW_REJECTED": "ARCHIVE_STRATEGY",
    "INSUFFICIENT_LIVE_SAMPLE": "CONTINUE_COLLECTING",
}

OUTCOMES = (
    "NO_FILL",
    "FULL_FILL",
    "PARTIAL_FILL",
    "STALE",
    "QUOTE_DISAPPEARED",
    "FOLLOWER_UNAVAILABLE",
    "HEDGE_WORSENED",
    "DATA_INVALID",
)

# Performance bounds (hot path)
MAX_PENDING = 256
MAX_GAP_SAMPLES = 4096
WRITER_BATCH_SIZE = 50
WRITER_FLUSH_INTERVAL_S = 2.0
ACCOUNTING_TOLERANCE = 1e-8

DEFAULT_RUN_DIR = "data/research/shadow_validation"
DEFAULT_RUNS_ROOT = "data/research/shadow_validation/runs"
FROZEN_STRATEGY_FILENAME = "frozen_strategy.json"
ACCEPTANCE_FILENAME = "acceptance_criteria.json"
MANIFEST_FILENAME = "manifest.json"
OBSERVATIONS_FILENAME = "observations.jsonl"
ACCUMULATOR_FILENAME = "accumulator.json"
FINAL_RESULTS_FILENAME = "final_results.json"
REPORT_PATH = "docs/CROSS_VENUE_DISLOCATION_SHADOW_VALIDATION.md"
PROPOSAL_PATH = "docs/LIMITED_PAPER_EXECUTION_PROPOSAL.md"

# Limited-paper proposal limits (written only on SHADOW_VALIDATED; not enabled).
PROPOSAL_CAPITAL_LIMIT_EUR = 500.0
PROPOSAL_MAX_NOTIONAL_EUR = NOTIONAL_EUR
PROPOSAL_MAX_CONCURRENT_POSITIONS = 1
PROPOSAL_MAX_DAILY_LOSS_EUR = 50.0
PROPOSAL_ROUTE_WHITELIST: tuple[str, ...] = ROUTE_UNIVERSE
PROPOSAL_KILL_SWITCH_REQUIRED = True


def frozen_parameters() -> dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "params": dict(FROZEN_PARAMS),
        "route_universe": list(ROUTE_UNIVERSE),
        "venues": [VENUE_A, VENUE_B],
        "dislocation_bps": DISLOCATION_BPS,
        "horizon_ms": HORIZON_MS,
        "notional_eur": NOTIONAL_EUR,
        "adverse_bps": ADVERSE_BPS,
        "slippage_bps": SLIPPAGE_BPS,
        "latency_bps": LATENCY_BPS,
        "fee_rate_roundtrip": FEE_RATE_ROUNDTRIP,
        "fee_model": "retail_taker_roundtrip",
        "entry_observe_ms": ENTRY_OBSERVE_MS,
        "hedge_observe_ms": HEDGE_OBSERVE_MS,
        "max_decision_stale_ms": MAX_DECISION_STALE_MS,
        "hedge_worse_bps": HEDGE_WORSE_BPS,
        "window_seconds": WINDOW_SECONDS_LIVE,
        "h0005": H0005_STATUS,
        "h0007": H0007_STATUS,
        "production_execution_enabled": PRODUCTION_EXECUTION_ENABLED,
        "execution_realism_production_enabled": EXECUTION_REALISM_PRODUCTION_ENABLED,
    }


def frozen_acceptance() -> dict[str, Any]:
    return {
        "min_complete_windows": MIN_COMPLETE_WINDOWS,
        "min_calendar_days": MIN_CALENDAR_DAYS,
        "min_valid_observations": MIN_VALID_OBSERVATIONS,
        "max_data_invalid_rate": MAX_DATA_INVALID_RATE,
        "min_fill_rate_validated": MIN_FILL_RATE_VALIDATED,
        "min_fill_rate_not_fragile": MIN_FILL_RATE_NOT_FRAGILE,
        "max_hedge_failure_rate": MAX_HEDGE_FAILURE_RATE,
        "min_quote_survival_rate": MIN_QUOTE_SURVIVAL_RATE,
        "min_follower_availability_rate": MIN_FOLLOWER_AVAILABILITY_RATE,
        "max_mean_gap_ratio": MAX_MEAN_GAP_RATIO,
        "uncertainty_gap_ratio": UNCERTAINTY_GAP_RATIO,
        "max_top_window_share": MAX_TOP_WINDOW_SHARE,
        "verdicts": list(VERDICTS),
        "next_actions": dict(NEXT_ACTIONS),
        "stop_early_if_positive": False,
        "automatic_retuning_allowed": AUTOMATIC_RETUNING_ALLOWED,
        "hypothesis_generator_enabled": HYPOTHESIS_GENERATOR_ENABLED,
    }


def collection_targets() -> dict[str, Any]:
    """Not verdict criteria. Do not use to retune or to stop early."""
    return {
        "preferred_complete_windows": PREFERRED_COMPLETE_WINDOWS,
        "preferred_calendar_days": PREFERRED_CALENDAR_DAYS,
        "early_stop_if_positive": EARLY_STOP_IF_POSITIVE,
        "early_stop_if_negative": EARLY_STOP_IF_NEGATIVE,
        "markout_horizons_ms": list(MARKOUT_HORIZONS_MS),
        "route_universe_limited": True,
        "route_universe": list(ROUTE_UNIVERSE),
    }


def _stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def parameter_hash() -> str:
    return _stable_hash(frozen_parameters())


def config_hash() -> str:
    return _stable_hash(
        {
            "protocol_version": PROTOCOL_VERSION,
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "acceptance": frozen_acceptance(),
            "historical": HISTORICAL_FINAL_VALIDATION,
            "research_locked": RESEARCH_LOCKED,
            "new_strategies": list(NEW_STRATEGIES_CREATED),
        }
    )


def protocol_hash() -> str:
    return _stable_hash(
        {
            "parameter_hash": parameter_hash(),
            "config_hash": config_hash(),
            "strategy_id": STRATEGY_ID,
        }
    )


def acceptance_hash() -> str:
    """Hash of frozen acceptance only. Changing criteria must fail tests."""
    return _stable_hash(frozen_acceptance())


def strategy_fingerprint() -> str:
    """Dataset-independent strategy identity. No tape, no git, no clock."""
    return _stable_hash(
        {
            "strategy_id": STRATEGY_ID,
            "parameter_hash": parameter_hash(),
            "config_hash": config_hash(),
        }
    )


def current_git_commit() -> str | None:
    return git_commit()

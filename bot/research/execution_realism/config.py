"""Frozen protocol and configuration for execution realism lab.

All constants are predeclared before seeing results. Do not tune after running.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from bot.research.robustness.protocol import (
    ADVERSE_EXTRA_BPS,
    FILL_RATE_BASELINE,
    FROZEN_H0005_PARAMS,
    FROZEN_H0007_PARAMS,
    STRIDE,
    WINDOW_SECONDS,
)
from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    MAX_TOP_ROUTE_PNL_SHARE,
    MAX_TOP_SYMBOL_PNL_SHARE,
    NOTIONAL_EUR_DEFAULT,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.freeze import git_commit

PACKAGE_LABEL = "EXECUTION_REALISM_LAB"
PROTOCOL_VERSION = "execution_realism_v1"
RANDOM_SEED = 20260818

# Hard invariants
EXECUTION_REALISM_ENABLED = True
EXECUTION_REALISM_SHADOW_ONLY = True
EXECUTION_REALISM_PRODUCTION_ENABLED = False

# Notional per signal (unchanged from canonical replay)
NOTIONAL_EUR = Decimal(str(NOTIONAL_EUR_DEFAULT))

# Existing cost model (baseline, not loosened)
ADVERSE_BPS = float(ADVERSE_BPS_DEFAULT)
SLIPPAGE_BPS = float(SLIPPAGE_BPS_DEFAULT)
LATENCY_BPS = float(LATENCY_PENALTY_BPS)
FILL_RATE = float(FILL_RATE_BASELINE)

# ------------------------------------------------------------------
# LATENCY SCENARIOS (predeclared, not fit on OOS)
# ------------------------------------------------------------------
# All values in milliseconds.
# observation_delay: time from market event to when our system observes it
# decision_delay: strategy computation time
# order_transmission: network send to venue
# venue_processing: venue's internal matching latency
# cancel_latency: time to send+process a cancel
# hedge_latency: full round-trip to execute hedge leg

LATENCY_SCENARIOS: dict[str, dict[str, float]] = {
    "IDEALIZED": {
        "observation_delay_ms": 0.0,
        "decision_delay_ms": 0.0,
        "order_transmission_ms": 0.0,
        "venue_processing_ms": 0.0,
        "cancel_latency_ms": 0.0,
        "hedge_latency_ms": 0.0,
    },
    "FAST": {
        "observation_delay_ms": 1.0,
        "decision_delay_ms": 0.5,
        "order_transmission_ms": 2.0,
        "venue_processing_ms": 1.0,
        "cancel_latency_ms": 3.0,
        "hedge_latency_ms": 5.0,
    },
    "NORMAL": {
        "observation_delay_ms": 5.0,
        "decision_delay_ms": 2.0,
        "order_transmission_ms": 10.0,
        "venue_processing_ms": 5.0,
        "cancel_latency_ms": 15.0,
        "hedge_latency_ms": 25.0,
    },
    "SLOW": {
        "observation_delay_ms": 20.0,
        "decision_delay_ms": 5.0,
        "order_transmission_ms": 50.0,
        "venue_processing_ms": 10.0,
        "cancel_latency_ms": 60.0,
        "hedge_latency_ms": 100.0,
    },
    "STRESSED": {
        "observation_delay_ms": 50.0,
        "decision_delay_ms": 20.0,
        "order_transmission_ms": 150.0,
        "venue_processing_ms": 50.0,
        "cancel_latency_ms": 200.0,
        "hedge_latency_ms": 300.0,
    },
}

# ------------------------------------------------------------------
# FILL MODELS (labels only; implementations in fill_model.py)
# ------------------------------------------------------------------
FILL_MODELS = (
    "EXISTING_TRADE_THROUGH",
    "POST_ONLY_SURVIVAL",
    "DEPTH_CONSTRAINED",
    "UNCERTAINTY_BOUNDED",
)

# ------------------------------------------------------------------
# HEDGE SCENARIOS
# ------------------------------------------------------------------
HEDGE_SCENARIOS = ("INSTANT", "FAST", "NORMAL", "SLOW")
HEDGE_DELAY_MS: dict[str, float] = {
    "INSTANT": 0.0,
    "FAST": 10.0,
    "NORMAL": 50.0,
    "SLOW": 200.0,
}

# ------------------------------------------------------------------
# CANCEL SCENARIOS
# ------------------------------------------------------------------
CANCEL_SCENARIOS = ("IMMEDIATE", "NORMAL", "DELAYED")
CANCEL_DELAY_MS: dict[str, float] = {
    "IMMEDIATE": 0.0,
    "NORMAL": 20.0,
    "DELAYED": 100.0,
}

# ------------------------------------------------------------------
# ROBUSTNESS CRITERION (predeclared, not tuned after seeing results)
# ------------------------------------------------------------------
MIN_INDEPENDENT_WINDOWS = 20
MIN_POSITIVE_SCENARIO_FRACTION = 0.70
CONCENTRATION_CAP = float(MAX_TOP_SYMBOL_PNL_SHARE)
ROUTE_CAP = float(MAX_TOP_ROUTE_PNL_SHARE)

# ------------------------------------------------------------------
# BREAK-EVEN SURFACE GRID
# ------------------------------------------------------------------
BREAKEVEN_LATENCY_MS = (0, 10, 25, 50, 100, 250, 500)
BREAKEVEN_ADVERSE_ADD_BPS = (0.0, 2.0, 4.0, 8.0, 16.0, 32.0)
BREAKEVEN_FEE_MULT = (1.0, 1.25, 1.50, 2.0)
BREAKEVEN_FILL_RATE = (1.0, 0.80, 0.60, 0.40, 0.20)
BREAKEVEN_HEDGE_DELAY_MS = (0, 25, 50, 100, 250)

# ------------------------------------------------------------------
# PERFORMANCE
# ------------------------------------------------------------------
MAX_MEMORY_MB = 1024
STRIDE_DEFAULT = STRIDE

# ------------------------------------------------------------------
# SCENARIO MATRIX STAGING
# ------------------------------------------------------------------
STAGE1_MAX_SCENARIOS = 100
STAGE2_MAX_SCENARIOS = 40
STAGE3_RESOLUTION = 20


def protocol_payload() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "random_seed": RANDOM_SEED,
        "execution_realism_enabled": EXECUTION_REALISM_ENABLED,
        "shadow_only": EXECUTION_REALISM_SHADOW_ONLY,
        "production_enabled": EXECUTION_REALISM_PRODUCTION_ENABLED,
        "notional_eur": str(NOTIONAL_EUR),
        "adverse_bps": ADVERSE_BPS,
        "slippage_bps": SLIPPAGE_BPS,
        "latency_bps": LATENCY_BPS,
        "fill_rate_baseline": FILL_RATE,
        "latency_scenarios": list(LATENCY_SCENARIOS.keys()),
        "fill_models": list(FILL_MODELS),
        "hedge_scenarios": list(HEDGE_SCENARIOS),
        "cancel_scenarios": list(CANCEL_SCENARIOS),
        "min_independent_windows": MIN_INDEPENDENT_WINDOWS,
        "min_positive_scenario_fraction": MIN_POSITIVE_SCENARIO_FRACTION,
        "concentration_cap": CONCENTRATION_CAP,
        "stride": STRIDE_DEFAULT,
        "frozen_h0005_params": FROZEN_H0005_PARAMS,
        "frozen_h0007_params": FROZEN_H0007_PARAMS,
    }


def protocol_hash() -> str:
    return hashlib.sha256(
        json.dumps(protocol_payload(), sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def build_manifest(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {
        "configuration_hash": protocol_hash(),
        "code_commit": git_commit(),
        "protocol_version": PROTOCOL_VERSION,
        "immutable": True,
        "protocol": protocol_payload(),
    }
    if extra:
        m.update(extra)
    return m

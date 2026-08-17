"""Frozen canonical-accounting protocol assumptions.

Does not change production costs, fills, fees, OOS gates, or hypotheses.
Thresholds here are either reused from existing research config or labeled
as protocol assumptions — never fit on OOS results.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    MAX_TOP_ROUTE_PNL_SHARE,
    MAX_TOP_SYMBOL_PNL_SHARE,
    NOTIONAL_EUR_DEFAULT,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.economics import execution_replay_net, shared_cost_assumptions
from bot.research.tournament.freeze import git_commit
from bot.research.robustness.protocol import (
    FIRST_LAB_OOS_END_NS,
    FIRST_LAB_OOS_START_NS,
    FORENSIC_OOS_END_NS,
    FROZEN_H0005_PARAMS,
    FROZEN_H0007_PARAMS,
    LOOKBACK_BUFFER_NS,
    STRIDE,
    WINDOW_SECONDS,
)
from bot.research.regime_lab.protocol import H0005, H0007

SCHEMA_VERSION = "canonical-accounting-v1"
REPLAY_VERSION = "canonical_execution_replay_v1"
PACKAGE_LABEL = "CANONICAL_REPLAY_ACCOUNTING"
PROTOCOL_VERSION = "canonical_accounting_v1"
RANDOM_SEED = 20260817

# Decimal identity tolerance (cash). Matches opportunity waterfall scale.
WATERFALL_TOLERANCE = Decimal("0.0001")

# Existing fill-model estimate used by research completed_round_trips.
FILL_RATE = float(execution_replay_net(expected_net=0.0)["fill_rate"])
ADVERSE_EXTRA_BPS = float(execution_replay_net(expected_net=0.0)["adverse_extra_bps"])
NOTIONAL_EUR = Decimal(str(NOTIONAL_EUR_DEFAULT))

FEE_MODEL = "retail_taker_roundtrip"
FILL_MODEL = "estimated_round_trips_v1"
ADVERSE_MODEL = "fixed_bps_of_notional_v1"
MEAN_EDGE_REPLAY_VERSION = "mean_edge_execution_replay_v1"

# Reused frozen concentration caps — not relaxed, not retuned.
CONCENTRATION_THRESHOLD = float(MAX_TOP_SYMBOL_PNL_SHARE)
ROUTE_CONCENTRATION_THRESHOLD = float(MAX_TOP_ROUTE_PNL_SHARE)

# PROTOCOL ASSUMPTION (predeclared): replication pass requires this many
# independent complete time windows. Not chosen from current 14-window tape.
MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS = 20

# Stress multipliers (research-only overlay on canonical replay).
FEE_MULTIPLIERS = (Decimal("1.0"), Decimal("1.10"), Decimal("1.25"), Decimal("1.50"))
SLIPPAGE_MULTIPLIERS = (Decimal("1.0"), Decimal("1.50"), Decimal("2.0"), Decimal("3.50"))
ADVERSE_MULTIPLIERS = (
    Decimal("1.0"),
    Decimal("1.125"),
    Decimal("1.25"),
    Decimal("1.625"),
    Decimal("2.25"),
)

H0007_AUTO_CHILD_GENERATION = False

AMBIGUOUS_FIELD_NAMES = (
    "net_per_fill",
    "pnl_per_fill",
    "NET_per_fill",
    "NET/fill",
    "pnl",
    "net",
    "edge",
    "profit",
    "EV",
)


def protocol_payload() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "mean_edge_replay_version": MEAN_EDGE_REPLAY_VERSION,
        "random_seed": RANDOM_SEED,
        "waterfall_tolerance": str(WATERFALL_TOLERANCE),
        "fee_model": FEE_MODEL,
        "fill_model": FILL_MODEL,
        "adverse_model": ADVERSE_MODEL,
        "fill_rate": FILL_RATE,
        "adverse_extra_bps": ADVERSE_EXTRA_BPS,
        "notional_eur": str(NOTIONAL_EUR),
        "baseline_adverse_bps": ADVERSE_BPS_DEFAULT,
        "baseline_slippage_bps": SLIPPAGE_BPS_DEFAULT,
        "baseline_latency_bps": LATENCY_PENALTY_BPS,
        "concentration_threshold": CONCENTRATION_THRESHOLD,
        "route_concentration_threshold": ROUTE_CONCENTRATION_THRESHOLD,
        "min_independent_windows_for_replication_pass": (
            MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS
        ),
        "min_windows_source": "protocol_assumption_not_fit_on_oos",
        "fee_multipliers": [str(x) for x in FEE_MULTIPLIERS],
        "slippage_multipliers": [str(x) for x in SLIPPAGE_MULTIPLIERS],
        "adverse_multipliers": [str(x) for x in ADVERSE_MULTIPLIERS],
        "h0007_auto_child_generation": H0007_AUTO_CHILD_GENERATION,
        "frozen_h0005_params": FROZEN_H0005_PARAMS,
        "frozen_h0007_params": FROZEN_H0007_PARAMS,
        "first_lab_oos_start_ns": FIRST_LAB_OOS_START_NS,
        "first_lab_oos_end_ns": FIRST_LAB_OOS_END_NS,
        "forensic_oos_end_ns": FORENSIC_OOS_END_NS,
        "lookback_buffer_ns": LOOKBACK_BUFFER_NS,
        "window_seconds": WINDOW_SECONDS,
        "stride": STRIDE,
        "cost_assumptions": shared_cost_assumptions(),
        "h0005": H0005,
        "h0007": H0007,
        "production_execution": "DISABLED",
        "execution_enabled": False,
        "thresholds_tuned_on_oos": False,
        "fills_fees_adverse_changed": False,
        "note": (
            "Accounting architecture only. Do not retune gates. "
            "MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS is a protocol assumption."
        ),
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
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "immutable": True,
        "protocol": protocol_payload(),
    }
    if extra:
        man.update(extra)
    return man

"""Frozen H-0005 / H-0007 protocol — freeze before inspecting results.

Changing any constant here is a NEW hypothesis version.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bot.research import CRITERIA_VERSION
from bot.research.forensics.buckets import (
    DENSITY_LOOKBACK_MS,
    QUOTE_AGE_FRESH_MS,
    SPREAD_WIDE_BPS,
    VOL_LOOKBACK_MS,
)
from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    MAX_TOP_ROUTE_PNL_SHARE,
    MAX_TOP_SYMBOL_PNL_SHARE,
    MIN_DEV_OBSERVATIONS,
    MIN_DEV_SIGNALS,
    MIN_OOS_OBSERVATIONS,
    MIN_OOS_SIGNALS,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.economics import shared_cost_assumptions
from bot.research.tournament.freeze import git_commit

PACKAGE_LABEL = "REGIME_HYPOTHESIS_LAB"
PROTOCOL_VERSION = "regime_lab_v1"
RANDOM_SEED = 20260817

# Forensic tournament OOS end — DISCOVERY/FORENSICS. Never recycle into DEV/OOS.
FORENSIC_OOS_END_NS = 1_786_967_758_683_398_912
FORENSIC_DATASET_ID = "mdresearch-research_md_v1-d71a392a288f1195"
LOOKBACK_BUFFER_NS = 60_000_000_000

MIN_FRESH_SECONDS = 1800.0
HORIZONS_MS = (500, 1000, 2000, 5000)

H0005 = {
    "hypothesis_id": "H-0005",
    "parent_hypothesis_id": "H-0001",
    "strategy_id": "cross_venue_dislocation_freshness",
    "economic_mechanism": (
        "Cross-venue mid dislocation is evaluated only when pre-trade quote "
        "freshness is FRESH (quote_age_ms < 250). Stale/unsupported quotes are "
        "not signals and are not training labels."
    ),
    "pre_trade_features": [
        "quote_age_ms",
        "venue",
        "symbol",
        "bid",
        "ask",
        "mid",
        "cross_venue_divergence",
        "spread",
        "depth",
        "event_rate",
        "local_receive_timestamp",
        "exchange_timestamp",
        "clock_quality",
        "latency_flag",
    ],
    "signal_definition": (
        "Parent CVD signed convergence, admitted iff quote_age_ms is known and "
        f"< {QUOTE_AGE_FRESH_MS} ms. Bitvavo exchange_ts is never invented."
    ),
    "research_status": "CANDIDATE",
    "fresh_max_ms": QUOTE_AGE_FRESH_MS,
}

H0007 = {
    "hypothesis_id": "H-0007",
    "parent_hypothesis_id": "H-0003",
    "strategy_id": "short_horizon_mean_reversion_wide_spread",
    "economic_mechanism": (
        "Short-horizon mean reversion vs cross-venue fair is evaluated only when "
        "pre-trade spread is WIDE (spread_bps >= 20). Event density is a recorded "
        "feature, not a fitted sparse-only filter."
    ),
    "pre_trade_features": [
        "spread_bps",
        "cross_venue_divergence",
        "quote_age_ms",
        "event_density",
        "depth",
        "volatility",
        "mid_return_history",
    ],
    "signal_definition": (
        "Parent SHMR signed reversion, admitted iff spread_bps is known and "
        f">= {SPREAD_WIDE_BPS}. Sparse density is not an admission threshold."
    ),
    "research_status": "CANDIDATE",
    "wide_spread_bps": SPREAD_WIDE_BPS,
}

COST_MODEL = {
    **shared_cost_assumptions(),
    "capital_lock": "horizon_ms (reported; not a NET override)",
    "inventory_effects": "waterfall inventory_relief=0 (unchanged parent model)",
    "net_is_only_profit_metric": True,
}

RISK_MODEL = {
    "max_top_symbol_pnl_share": MAX_TOP_SYMBOL_PNL_SHARE,
    "max_top_route_pnl_share": MAX_TOP_ROUTE_PNL_SHARE,
    "route_universe_limited_annotation": True,
    "criteria_relaxed": False,
}

EXECUTION_MODEL = {
    "enabled": False,
    "fill_model": "trade_through_conservative",
    "no_queue_fills": True,
    "affects_production_ranking": False,
}

EXPECTED_FAILURE_MODES = [
    "stale_quote_artifact_does_not_survive_fresh_gate",
    "wide_spread_is_non_tradable_or_cost_negative",
    "NON_PARTICIPATION_ONLY",
    "NO_SELECTIVE_EDGE",
    "OOS_FAILED",
    "UNSTABLE",
    "INSUFFICIENT_FRESH_DATA",
]


def protocol_payload() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "criteria_version": CRITERIA_VERSION,
        "random_seed": RANDOM_SEED,
        "forensic_oos_end_ns": FORENSIC_OOS_END_NS,
        "forensic_dataset_id": FORENSIC_DATASET_ID,
        "lookback_buffer_ns": LOOKBACK_BUFFER_NS,
        "min_fresh_seconds": MIN_FRESH_SECONDS,
        "horizons_ms": list(HORIZONS_MS),
        "h0005": H0005,
        "h0007": H0007,
        "cost_model": COST_MODEL,
        "risk_model": RISK_MODEL,
        "execution_model": EXECUTION_MODEL,
        "expected_failure_modes": EXPECTED_FAILURE_MODES,
        "sample_floors": {
            "min_dev_observations": MIN_DEV_OBSERVATIONS,
            "min_dev_signals": MIN_DEV_SIGNALS,
            "min_oos_observations": MIN_OOS_OBSERVATIONS,
            "min_oos_signals": MIN_OOS_SIGNALS,
        },
        "adverse_bps": ADVERSE_BPS_DEFAULT,
        "slippage_bps": SLIPPAGE_BPS_DEFAULT,
        "latency_bps": LATENCY_PENALTY_BPS,
        "quote_age_fresh_ms": QUOTE_AGE_FRESH_MS,
        "spread_wide_bps": SPREAD_WIDE_BPS,
        "density_lookback_ms": DENSITY_LOOKBACK_MS,
        "vol_lookback_ms": VOL_LOOKBACK_MS,
        "note": "Frozen before result inspection. Do not tune on OOS.",
    }


def protocol_hash() -> str:
    return hashlib.sha256(
        json.dumps(protocol_payload(), sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def hypothesis_hash(spec: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:16]


def build_manifest(
    *,
    dataset_id: str,
    dataset_fingerprint: str,
    split: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    man = {
        "data_manifest_hash": hashlib.sha256(
            f"{dataset_id}:{dataset_fingerprint}:{FORENSIC_OOS_END_NS}".encode()
        ).hexdigest(),
        "hypothesis_hash_h0005": hypothesis_hash(H0005),
        "hypothesis_hash_h0007": hypothesis_hash(H0007),
        "code_commit": git_commit(),
        "configuration_hash": protocol_hash(),
        "random_seed": RANDOM_SEED,
        "DEV_boundary": (split.get("development") if split.get("available") else None),
        "OOS_boundary": (split.get("untouched_oos") if split.get("available") else None),
        "FREEZE_boundary": (split.get("freeze_boundary") if split.get("available") else None),
        "dataset_id": dataset_id,
        "dataset_fingerprint": dataset_fingerprint,
        "protocol_version": PROTOCOL_VERSION,
        "immutable": True,
        "protocol": protocol_payload(),
    }
    if extra:
        man.update(extra)
    return man

"""Frozen alpha-attribution protocol. Forensic only. No new strategies."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from bot.research.forensics.buckets import (
    DENSITY_DENSE,
    DENSITY_SPARSE,
    DOMINANCE_SHARE,
    LIQ_DEEP_EUR,
    LIQ_THIN_EUR,
    LOO_DOMINANCE_SHARE,
    MIN_BLOCKS_WITH_SIGNALS,
    MIN_SIGNALS_FOR_CLASS,
    QUOTE_AGE_FRESH_MS,
    QUOTE_AGE_STALE_MS,
    SPREAD_TIGHT_BPS,
    SPREAD_WIDE_BPS,
    VOL_HIGH_BPS,
    VOL_LOW_BPS,
)
from bot.research.tournament.criteria import (
    IMBALANCE_THRESH_GRID,
    MAX_TOP_ROUTE_PNL_SHARE,
    MAX_TOP_SYMBOL_PNL_SHARE,
)
from bot.research.tournament.freeze import git_commit
from bot.research.robustness.protocol import FROZEN_H0005_PARAMS, STRIDE
from bot.research.accounting.protocol import (
    REPLAY_VERSION,
    SCHEMA_VERSION,
    WATERFALL_TOLERANCE,
)

PACKAGE_LABEL = "ALPHA_ATTRIBUTION_LAB"
PROTOCOL_VERSION = "alpha_attribution_v1"
RANDOM_SEED = 20260817

# Existing floors — not fit on this tape.
MIN_SIGNALS = MIN_SIGNALS_FOR_CLASS  # 30
MIN_WINDOWS_WITH_SIGNALS = MIN_BLOCKS_WITH_SIGNALS  # 3
SYMBOL_SHARE_CAP = MAX_TOP_SYMBOL_PNL_SHARE  # 0.70
ROUTE_SHARE_CAP = MAX_TOP_ROUTE_PNL_SHARE  # 0.70
WINDOW_SHARE_CAP = DOMINANCE_SHARE  # 0.70
LOO_SHARE_FLAG = LOO_DOMINANCE_SHARE  # 0.50

IMBALANCE_FLAT = float(IMBALANCE_THRESH_GRID[0])  # 0.15, existing grid floor

DESCRIPTIVE_ONLY = True
HYPOTHESIS_AUTOCREATE = False
H0005_AUTO_CHILD_GENERATION = False
PRODUCTION_EXECUTION = "DISABLED"

# Published paired delta from canonical accounting. Audit against this; do not rewrite.
PUBLISHED_PAIRED_DELTA_EUR = Decimal("-51461.2894766299632178779")

# Named contexts from existing forensic buckets. Not a searched grid.
CONTEXT_NAMES = (
    "FRESH_STRONG_DEEP",
    "FRESH_STRONG_NOT_DEEP",
    "FRESH_NOT_STRONG",
    "STALE_STRONG",
    "STALE_NOT_STRONG",
    "VERY_STALE",
    "UNKNOWN_AGE",
)


def protocol_payload() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "random_seed": RANDOM_SEED,
        "descriptive_only": DESCRIPTIVE_ONLY,
        "hypothesis_autocreate": HYPOTHESIS_AUTOCREATE,
        "h0005_auto_child_generation": H0005_AUTO_CHILD_GENERATION,
        "production_execution": PRODUCTION_EXECUTION,
        "published_paired_delta_eur": str(PUBLISHED_PAIRED_DELTA_EUR),
        "frozen_h0005_params": FROZEN_H0005_PARAMS,
        "quote_age_fresh_ms": QUOTE_AGE_FRESH_MS,
        "quote_age_stale_ms": QUOTE_AGE_STALE_MS,
        "spread_tight_bps": SPREAD_TIGHT_BPS,
        "spread_wide_bps": SPREAD_WIDE_BPS,
        "vol_low_bps": VOL_LOW_BPS,
        "vol_high_bps": VOL_HIGH_BPS,
        "liq_thin_eur": LIQ_THIN_EUR,
        "liq_deep_eur": LIQ_DEEP_EUR,
        "density_sparse": DENSITY_SPARSE,
        "density_dense": DENSITY_DENSE,
        "min_signals": MIN_SIGNALS,
        "min_windows_with_signals": MIN_WINDOWS_WITH_SIGNALS,
        "symbol_share_cap": SYMBOL_SHARE_CAP,
        "route_share_cap": ROUTE_SHARE_CAP,
        "window_share_cap": WINDOW_SHARE_CAP,
        "loo_share_flag": LOO_SHARE_FLAG,
        "imbalance_flat": IMBALANCE_FLAT,
        "context_names": list(CONTEXT_NAMES),
        "stride": STRIDE,
        "waterfall_tolerance": str(WATERFALL_TOLERANCE),
        "thresholds_tuned_on_oos": False,
        "note": (
            "Forensic attribution of the paired H-0005 universe. "
            "Bins are existing forensic/tournament constants. DESCRIPTIVE_ONLY."
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
        "protocol_version": PROTOCOL_VERSION,
        "immutable": True,
        "protocol": protocol_payload(),
    }
    if extra:
        man.update(extra)
    return man


def assert_no_oos_threshold_creation(payload: dict[str, Any] | None = None) -> None:
    """OOS forensic numbers must never become a fitted threshold."""
    row = payload or {}
    proto = (row.get("manifest") or {}).get("protocol") or row.get("protocol") or {}
    if (
        row.get("oos_thresholds_created")
        or row.get("thresholds_tuned_on_oos")
        or proto.get("thresholds_tuned_on_oos")
        or HYPOTHESIS_AUTOCREATE
    ):
        raise RuntimeError("OOS data cannot create a threshold")


def reject_auto_strategy(*, parent_id: str | None, title: str = "", source: str = "") -> str | None:
    """Attribution may emit RESEARCH_OBSERVATION only — never a production child."""
    pid = str(parent_id or "")
    src = str(source or "").lower()
    title_u = str(title or "").upper()
    if not H0005_AUTO_CHILD_GENERATION and (
        pid == "H-0005" or ("H-0005" in title_u and "RETUNE" in title_u)
    ):
        return "H-0005_no_automatic_child_or_retune_from_attribution"
    if pid == "H-0007":
        return "H-0007_GATE_INACTIVE_no_automatic_child_hypotheses"
    if src in {"alpha_attribution", "alpha_attribution_lab"}:
        return "alpha_attribution_emits_research_observations_only"
    return None

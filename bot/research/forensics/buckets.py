"""Predeclared, non-overlapping, non-adaptive forensic buckets."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Frozen forensics criteria — do not fit on the tape.
FORENSICS_SEED = 20260817
N_PERMUTATIONS = 199
N_CHRONO_BLOCKS = 5

VOL_LOOKBACK_MS = 5000
DENSITY_LOOKBACK_MS = 1000
MARKET_RETURN_LOOKBACK_MS = 5000
QUOTE_AGE_FRESH_MS = 250
QUOTE_AGE_STALE_MS = 2000

# Volatility of |lookback return| in bps
VOL_LOW_BPS = 5.0
VOL_HIGH_BPS = 20.0

# Spread in bps
SPREAD_TIGHT_BPS = 5.0
SPREAD_WIDE_BPS = 20.0

# Notional L1 depth in EUR
LIQ_THIN_EUR = 500.0
LIQ_DEEP_EUR = 5000.0

# Event count in trailing 1s
DENSITY_SPARSE = 5
DENSITY_DENSE = 20

# Equal-weight majors lookback return in bps
MKT_DOWN_BPS = -10.0
MKT_UP_BPS = 10.0

# Holding vs requested horizon
HOLD_SHORT_MULT = 0.5
HOLD_LONG_MULT = 2.0

# Signal strength vs frozen threshold
STRENGTH_MED_MULT = 2.0
STRENGTH_STRONG_MULT = 4.0

# Classification floors (descriptive; not tournament gates)
DOMINANCE_SHARE = 0.70
LOO_DOMINANCE_SHARE = 0.50
REGIME_SHARE = 0.70
FEATURE_RATIO_STRUCTURAL = 2.0
NULL_EXTREME_ALPHA = 0.10
MIN_SIGNALS_FOR_CLASS = 30
MIN_BLOCKS_WITH_SIGNALS = 3

MAJORS = ("BTCEUR", "ETHEUR", "SOLEUR")


def bucket_manifest() -> dict[str, Any]:
    return {
        "adaptive": False,
        "overlapping": False,
        "seed": FORENSICS_SEED,
        "n_permutations": N_PERMUTATIONS,
        "n_chrono_blocks": N_CHRONO_BLOCKS,
        "vol_lookback_ms": VOL_LOOKBACK_MS,
        "density_lookback_ms": DENSITY_LOOKBACK_MS,
        "market_return_lookback_ms": MARKET_RETURN_LOOKBACK_MS,
        "vol_bps": [0, VOL_LOW_BPS, VOL_HIGH_BPS, None],
        "spread_bps": [0, SPREAD_TIGHT_BPS, SPREAD_WIDE_BPS, None],
        "liquidity_eur": [0, LIQ_THIN_EUR, LIQ_DEEP_EUR, None],
        "density_counts": [0, DENSITY_SPARSE, DENSITY_DENSE, None],
        "market_return_bps": [None, MKT_DOWN_BPS, MKT_UP_BPS, None],
        "note": "Fixed thresholds. Not percentiles of the current tape.",
    }


def utc_hour(ts_ns: int) -> int:
    return datetime.fromtimestamp(ts_ns / 1e9, UTC).hour


def chrono_block_id(ts_ns: int, start_ns: int, end_ns_inclusive: int) -> str:
    span = max(1, int(end_ns_inclusive) - int(start_ns) + 1)
    width = span / N_CHRONO_BLOCKS
    raw = int((int(ts_ns) - int(start_ns)) / width)
    idx = min(N_CHRONO_BLOCKS - 1, max(0, raw))
    return f"BLOCK_{idx + 1}"


def vol_regime(abs_return_bps: float | None) -> str:
    if abs_return_bps is None:
        return "UNKNOWN"
    if abs_return_bps < VOL_LOW_BPS:
        return "LOW"
    if abs_return_bps < VOL_HIGH_BPS:
        return "MID"
    return "HIGH"


def spread_regime(spread_bps: float | None) -> str:
    if spread_bps is None:
        return "UNKNOWN"
    if spread_bps < SPREAD_TIGHT_BPS:
        return "TIGHT"
    if spread_bps < SPREAD_WIDE_BPS:
        return "NORMAL"
    return "WIDE"


def liquidity_regime(notional_eur: float | None) -> str:
    if notional_eur is None:
        return "UNKNOWN"
    if notional_eur < LIQ_THIN_EUR:
        return "THIN"
    if notional_eur < LIQ_DEEP_EUR:
        return "MEDIUM"
    return "DEEP"


def density_regime(count: int | None) -> str:
    if count is None:
        return "UNKNOWN"
    if count < DENSITY_SPARSE:
        return "SPARSE"
    if count < DENSITY_DENSE:
        return "NORMAL"
    return "DENSE"


def market_return_regime(ret_bps: float | None) -> str:
    if ret_bps is None:
        return "UNKNOWN"
    if ret_bps < MKT_DOWN_BPS:
        return "DOWN"
    if ret_bps < MKT_UP_BPS:
        return "FLAT"
    return "UP"


def holding_regime(holding_ns: int | None, horizon_ms: int) -> str:
    if holding_ns is None or horizon_ms <= 0:
        return "UNKNOWN"
    ratio = holding_ns / (horizon_ms * 1_000_000)
    if ratio < HOLD_SHORT_MULT:
        return "SHORT"
    if ratio < HOLD_LONG_MULT:
        return "ON_HORIZON"
    return "LONG"


def strength_regime(strength_bps: float | None, threshold_bps: float) -> str:
    if strength_bps is None or threshold_bps <= 0:
        return "UNKNOWN"
    ratio = strength_bps / threshold_bps
    if ratio < 1.0:
        return "BELOW_THRESHOLD"
    if ratio < STRENGTH_MED_MULT:
        return "WEAK"
    if ratio < STRENGTH_STRONG_MULT:
        return "MEDIUM"
    return "STRONG"


def quote_age_regime(age_ms: float | None) -> str:
    if age_ms is None:
        return "UNKNOWN"
    if age_ms < QUOTE_AGE_FRESH_MS:
        return "FRESH"
    if age_ms < QUOTE_AGE_STALE_MS:
        return "STALE"
    return "VERY_STALE"

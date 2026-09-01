"""Centralized, predeclared tournament criteria — never tune after OOS inspection."""

from __future__ import annotations

from typing import Any

from bot.research import CRITERIA_VERSION

# Sample adequacy — documented floors; not set to make current tape pass.
MIN_DEV_OBSERVATIONS = 500
MIN_DEV_SIGNALS = 50
MIN_OOS_SIGNALS = 30
MIN_OOS_OBSERVATIONS = 200

# Signal gate: absolute mean forward return (fraction) must exceed this
# after uncertainty penalty to count as predictive separation.
MIN_SIGNAL_EFFECT = 1e-5  # 0.01 bps of price — tiny floor; CI still gates
SIGNAL_UNCERTAINTY_Z = 1.64  # ~90% one-sided

# Development parameter grids (small, predeclared)
HORIZONS_MS = (50, 100, 250, 500, 1000, 2000, 5000)
LOOKBACKS_MS = (100, 500, 1000)
DISLOCATION_BPS_GRID = (5.0, 10.0, 20.0, 40.0)
IMBALANCE_THRESH_GRID = (0.15, 0.25, 0.40)
MOMENTUM_THRESH_GRID = (0.00005, 0.0001, 0.0002)  # fractional return
MEAN_REV_BPS_GRID = (5.0, 10.0, 20.0)

CORE_VENUES = ("binance", "bitvavo", "okx")
DIRECTED_ROUTES = (
    ("binance", "bitvavo"),
    ("binance", "okx"),
    ("bitvavo", "binance"),
    ("bitvavo", "okx"),
    ("okx", "binance"),
    ("okx", "bitvavo"),
)

# Economics: use retail taker×2 round-trip proxy + adverse + buffer (shared).
ADVERSE_BPS_DEFAULT = 8.0  # conservative; matches known adverse trade-through scale
LATENCY_PENALTY_BPS = 2.0
SLIPPAGE_BPS_DEFAULT = 2.0
NOTIONAL_EUR_DEFAULT = 100.0

# Stability
MAX_TOP_SYMBOL_PNL_SHARE = 0.70
MAX_TOP_ROUTE_PNL_SHARE = 0.70

# Tournament score weights (deterministic; failed gates score 0)
SCORE_WEIGHTS = {
    "oos_predictive": 0.30,
    "expected_net": 0.25,
    "execution": 0.20,
    "stability": 0.15,
    "sample": 0.10,
}

VERDICTS = (
    "DATA_UNSUPPORTED",
    "NO_SIGNAL",
    "INSUFFICIENT_SAMPLE",
    "IN_SAMPLE_ONLY",
    "OOS_FAILED",
    "COST_NEGATIVE",
    "EXECUTION_NEGATIVE",
    "UNSTABLE",
    "PAPER_CANDIDATE",
)

GATES = (
    "DATA",
    "SIGNAL",
    "DEVELOPMENT",
    "FREEZE",
    "OOS",
    "ECONOMICS",
    "EXECUTION",
    "STABILITY",
    "SCORE",
)


def criteria_manifest() -> dict[str, Any]:
    return {
        "criteria_version": CRITERIA_VERSION,
        "min_dev_observations": MIN_DEV_OBSERVATIONS,
        "min_dev_signals": MIN_DEV_SIGNALS,
        "min_oos_signals": MIN_OOS_SIGNALS,
        "min_oos_observations": MIN_OOS_OBSERVATIONS,
        "min_signal_effect": MIN_SIGNAL_EFFECT,
        "horizons_ms": list(HORIZONS_MS),
        "adverse_bps_default": ADVERSE_BPS_DEFAULT,
        "note": "Frozen criteria. Bump CRITERIA_VERSION to change. Never tune on OOS.",
    }

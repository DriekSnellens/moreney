"""Deterministic concentration forensics — explain STABILITY failures.

Research-only. Does not retune strategies, fees, fills, OOS, or execution.
"""

from __future__ import annotations

FORENSICS_CRITERIA_VERSION = "concentration_forensics_v1"
PACKAGE_LABEL = "CONCENTRATION_FORENSICS"
TARGET_STRATEGIES = (
    "cross_venue_dislocation",
    "short_horizon_mean_reversion",
)

__all__ = [
    "FORENSICS_CRITERIA_VERSION",
    "PACKAGE_LABEL",
    "TARGET_STRATEGIES",
]

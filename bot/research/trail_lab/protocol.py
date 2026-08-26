"""Frozen search space for trail harvest settings."""

from __future__ import annotations

from typing import Any

# Live micro baseline before trail-lab apply (selectivity patch).
CURRENT_LIVE: dict[str, float] = {
    "soft_arm_pct": 0.010,
    "soft_partial_pct": 0.30,
    "soft_drawdown_pct": 0.004,
    "hard_arm_pct": 0.06,
    "hard_drawdown_pct": 0.03,
    "hard_partial_pct": 0.25,
    "fee_rate": 0.0025,  # conservative maker+buffer proxy
    "sell_buffer_bps": 10.0,
}

# Applied after trail-lab OOS win (synthetic paths + live bag costs).
LAB_BEST: dict[str, float] = {
    "soft_arm_pct": 0.020,
    "soft_partial_pct": 0.50,
    "soft_drawdown_pct": 0.004,
    "hard_arm_pct": 0.06,
    "hard_drawdown_pct": 0.03,
    "hard_partial_pct": 0.25,
    "fee_rate": 0.0025,
    "sell_buffer_bps": 10.0,
}

# Small predeclared grid — IS picks, OOS reports.
GRID: dict[str, list[float]] = {
    "soft_arm_pct": [0.008, 0.010, 0.015, 0.020],
    "soft_partial_pct": [0.0, 0.25, 0.30, 0.50],
    "soft_drawdown_pct": [0.003, 0.004, 0.008, 0.012],
}

HARD_FIXED: dict[str, float] = {
    "hard_arm_pct": 0.06,
    "hard_drawdown_pct": 0.03,
    "hard_partial_pct": 0.25,
    "fee_rate": 0.0025,
    "sell_buffer_bps": 10.0,
}


def iter_grid() -> list[dict[str, float]]:
    configs: list[dict[str, float]] = []
    for soft_arm in GRID["soft_arm_pct"]:
        for soft_partial in GRID["soft_partial_pct"]:
            for soft_dd in GRID["soft_drawdown_pct"]:
                row = {
                    "soft_arm_pct": float(soft_arm),
                    "soft_partial_pct": float(soft_partial),
                    "soft_drawdown_pct": float(soft_dd),
                    **HARD_FIXED,
                }
                configs.append(row)
    return configs


def config_id(cfg: dict[str, Any]) -> str:
    return (
        f"arm{cfg['soft_arm_pct']:.3f}"
        f"_p{cfg['soft_partial_pct']:.2f}"
        f"_dd{cfg['soft_drawdown_pct']:.3f}"
    )

"""Dry vs wet Bitvavo-1d replay for residual-weekly and BTC+RS clip.

Research-only. Does not arm live books, dump inventory, or change the
€2k 15m satellite. AlphaI is out of scope (not on 1d residual/clip).
"""

from bot.research.residual_wet.engine import (
    DRY,
    WET,
    FillModel,
    metrics,
    pick_residual,
    run_clip,
    run_residual,
)

__all__ = [
    "DRY",
    "WET",
    "FillModel",
    "metrics",
    "pick_residual",
    "run_clip",
    "run_residual",
]

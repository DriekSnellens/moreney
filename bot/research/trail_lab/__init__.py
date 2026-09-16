"""Offline trail take-profit parameter search (never-loss constrained)."""

from bot.research.trail_lab.engine import run_trail_lab
from bot.research.trail_lab.protocol import CURRENT_LIVE, GRID

__all__ = ["run_trail_lab", "CURRENT_LIVE", "GRID"]

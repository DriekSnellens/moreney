"""Offline exit overlays on the live BTC+RS clip (research only)."""

from bot.research.clip_exit_lab.engine import WET, run_clip_exits
from bot.research.clip_exit_lab.policies import POLICIES, ExitPolicy

__all__ = ["ExitPolicy", "POLICIES", "WET", "run_clip_exits"]

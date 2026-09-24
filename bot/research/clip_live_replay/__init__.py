"""Replay the armed live clip pack on the wet 1d tape.

Uses ClipConfig defaults (20/80, SMA50 flatten, 10d skip-1, floor 4%,
10% alt-trail, weekly). Research only — does not arm or flatten live.
"""

from bot.research.clip_live_replay.engine import live_pack_knobs, run_live_pack

__all__ = ["live_pack_knobs", "run_live_pack"]

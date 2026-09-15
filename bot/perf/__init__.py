"""Lightweight performance instrumentation (opt-in)."""

from bot.perf.cycle_metrics import CycleLatencyTracker, LatencyStats

__all__ = ["CycleLatencyTracker", "LatencyStats"]

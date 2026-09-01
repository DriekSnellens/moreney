"""Live vs Research Expectancy Attribution Audit.

Research-only diagnostic: reconstructs where economic expectancy diverges between
research replay and live micro execution. Does NOT modify live trading logic.

Usage:
  python -m bot.research.live_vs_research_attribution
  python -m bot.research.live_vs_research_attribution --output data/research/live_vs_research_attribution.json
"""

from __future__ import annotations

from bot.research.live_vs_research_attribution.runner import run_attribution_audit

__all__ = ["run_attribution_audit"]

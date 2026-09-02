"""Diagnose live underperformance vs daily € targets (research-only)."""

from bot.research.underperformance_diagnosis.analyze import analyze_underperformance
from bot.research.underperformance_diagnosis.report import write_report

__all__ = ["analyze_underperformance", "write_report"]

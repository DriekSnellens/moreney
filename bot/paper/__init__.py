"""Paper trading package — 24/7 paper runner, tracking, and persistence."""

from bot.paper.models import (
    DailyStats,
    ExchangePairStats,
    HourlyStats,
    PerformanceSnapshot,
    StrategyStats,
    TrackedOpportunity,
)
from bot.paper.store import PaperTradingStore
from bot.paper.tracker import PerformanceTracker

__all__ = [
    "DailyStats",
    "ExchangePairStats",
    "HourlyStats",
    "PaperRunner",
    "PaperTradingStore",
    "PerformanceSnapshot",
    "PerformanceTracker",
    "StrategyStats",
    "TrackedOpportunity",
]


def __getattr__(name: str):
    # Lazy: avoid circular import with strategies → capital_policy → paper.
    if name == "PaperRunner":
        from bot.paper.runner import PaperRunner

        return PaperRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Backtesting framework scaffolding.

Replays historical MarketSnapshot sequences through strategies and the
profitability / risk pipeline without live execution.
"""

from backtesting.engine import BacktestEngine, BacktestResult

__all__ = ["BacktestEngine", "BacktestResult"]

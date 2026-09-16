"""Walk-forward backtest for the Daily Momentum Desk (research-only).

Runs ``bot.live.momentum_desk`` — the exact code the live runner executes —
on Bitvavo 15m candles. Usage::

    python -m bot.research.momentum_backtest --days 90
    python -m bot.research.momentum_backtest --days 14 --trail 0.025 --hours 0,12
"""

from bot.research.momentum_backtest.engine import (
    BacktestResult,
    ClosedTrade,
    load_candles,
    simulate,
    walk_forward,
)

__all__ = ["BacktestResult", "ClosedTrade", "load_candles", "simulate", "walk_forward"]

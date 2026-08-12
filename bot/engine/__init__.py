"""Trading engine: wires market data → strategy → profitability → risk → execution."""

from bot.engine.orchestrator import TradingEngine, TradeCycleResult

__all__ = ["TradeCycleResult", "TradingEngine"]

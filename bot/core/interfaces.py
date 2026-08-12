"""Layer contracts (Protocols / ABCs).

These interfaces enforce separation of concerns:
market data → strategy → profitability → risk → execution.

Strategies depend only on MarketSnapshot / domain types and must never import
exchange clients. Exchange I/O is confined to bot.exchanges and used by
market_data / execution layers.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from bot.core.exchange_types import (
    ExchangeOrder,
    HealthCheckResult,
    OrderBook,
    TradingFee,
)
from bot.core.models import (
    ExecutionResult,
    MarketSnapshot,
    OrderRequest,
    PortfolioSnapshot,
    ProfitabilityResult,
    RiskDecision,
    TradeOpportunity,
)


@runtime_checkable
class MarketDataProvider(Protocol):
    """Provides normalized market snapshots to strategies."""

    async def get_snapshot(self, symbol: str) -> MarketSnapshot: ...

    async def get_snapshots(self, symbols: Sequence[str]) -> list[MarketSnapshot]: ...


@runtime_checkable
class Strategy(Protocol):
    """Evaluates market data and emits TradeOpportunity objects only."""

    name: str

    async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]: ...


@runtime_checkable
class ProfitabilityEngine(Protocol):
    """Computes expected NET profit after costs and buffers."""

    async def evaluate(self, opportunity: TradeOpportunity) -> ProfitabilityResult: ...


@runtime_checkable
class RiskEngine(Protocol):
    """Approves or rejects every trade before execution."""

    async def evaluate(
        self,
        opportunity: TradeOpportunity,
        profitability: ProfitabilityResult,
        portfolio: PortfolioSnapshot,
    ) -> RiskDecision: ...


@runtime_checkable
class Executor(Protocol):
    """Places orders (paper or live). No withdrawal methods exist."""

    async def execute(self, order: OrderRequest) -> ExecutionResult: ...


@runtime_checkable
class PortfolioService(Protocol):
    """Reads portfolio state for risk and reporting."""

    async def get_snapshot(self) -> PortfolioSnapshot: ...


class ExchangeClient(ABC):
    """Exchange adapter for market data and trading only.

    Intentionally omits any withdrawal / transfer-out capability.
    Live order placement must remain disabled until explicitly enabled on the
    concrete adapter (see ``enable_trading`` on CCXT-backed clients).
    """

    name: str

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> MarketSnapshot:
        """Fetch a normalized ticker / top-of-book snapshot."""

    @abstractmethod
    async def fetch_order_book(self, symbol: str, *, limit: int | None = None) -> OrderBook:
        """Fetch a normalized order book."""

    @abstractmethod
    async def fetch_trading_fees(self, symbol: str) -> TradingFee:
        """Fetch maker/taker trading fees for a symbol."""

    @abstractmethod
    async def fetch_open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        """List open orders, optionally filtered by symbol."""

    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: str) -> ExchangeOrder:
        """Fetch a single order by exchange id."""

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> ExecutionResult:
        """Place a trading order (or dry-run when live trading is disabled)."""

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> ExchangeOrder:
        """Cancel an open order."""

    @abstractmethod
    async def get_balances(self) -> PortfolioSnapshot:
        """Fetch trading balances and positions (no withdrawal endpoints)."""

    @abstractmethod
    async def health_check(self) -> HealthCheckResult:
        """Probe connectivity / auth / basic market-data reachability."""

    async def close(self) -> None:
        """Release underlying network resources (optional)."""
        return None

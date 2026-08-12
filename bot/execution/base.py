"""Shared executor base. Trading orders only — no withdrawals."""

from abc import ABC, abstractmethod

from bot.core.models import ExecutionResult, OrderRequest


class BaseExecutor(ABC):
    """Executor contract used by the trading engine."""

    name: str

    @abstractmethod
    async def execute(self, order: OrderRequest) -> ExecutionResult:
        """Execute a trading order (paper or live)."""

"""Execution facade wiring PaperExecutor → FillTracker → Portfolio.

Live execution remains disabled and isolated. This module never places real orders.
"""

from __future__ import annotations

import logging

from bot.core.config import Settings
from bot.core.enums import ExecutionMode, OrderType
from bot.core.exchange_types import OrderBook
from bot.core.exceptions import ConfigurationError, ExecutionError
from bot.core.models import ExecutionResult, OrderRequest
from bot.execution.base import BaseExecutor
from bot.execution.fill_tracker import FillTracker
from bot.execution.order_manager import OrderManager
from bot.execution.paper_executor import PaperExecutor
from bot.portfolio.portfolio import PaperPortfolio

logger = logging.getLogger(__name__)


class ExecutionService(BaseExecutor):
    """Paper-only execution service used by TradingEngine.

    Implements the Executor protocol while owning order/fill/portfolio wiring.
    """

    name = "paper_service"

    def __init__(
        self,
        settings: Settings,
        *,
        portfolio: PaperPortfolio | None = None,
    ) -> None:
        if settings.execution_mode != ExecutionMode.PAPER:
            raise ConfigurationError(
                "ExecutionService is paper-only. Set EXECUTION_MODE=paper. "
                "Live trading is not enabled."
            )
        self._settings = settings
        self._portfolio = portfolio or PaperPortfolio(settings)
        self._orders = OrderManager()
        self._fills = FillTracker(self._portfolio)
        self._paper = PaperExecutor(
            settings,
            portfolio=self._portfolio,
            order_manager=self._orders,
            fill_tracker=self._fills,
        )

    @property
    def portfolio(self) -> PaperPortfolio:
        return self._portfolio

    @property
    def paper(self) -> PaperExecutor:
        return self._paper

    @property
    def order_manager(self) -> OrderManager:
        return self._orders

    @property
    def fill_tracker(self) -> FillTracker:
        return self._fills

    async def execute(
        self,
        order: OrderRequest,
        *,
        order_book: OrderBook | None = None,
        strategy: str = "",
        order_type: OrderType = OrderType.LIMIT,
    ) -> ExecutionResult:
        result = await self._paper.execute(
            order,
            order_book=order_book,
            strategy=strategy,
            order_type=order_type,
        )
        if result.metadata.get("real_exchange_order"):
            raise ExecutionError("Safety violation: real exchange order flag set in paper path")
        return result

    def match_resting(self, books: dict[str, dict[str, OrderBook]]) -> list[ExecutionResult]:
        return self._paper.match_resting(books)


def create_paper_execution(settings: Settings) -> ExecutionService:
    """Factory that always returns an isolated paper execution stack."""
    if settings.execution_mode != ExecutionMode.PAPER:
        logger.error(
            "Refusing to create paper stack while execution_mode=%s",
            settings.execution_mode,
        )
        raise ConfigurationError("Paper execution requires EXECUTION_MODE=paper")
    return ExecutionService(settings)

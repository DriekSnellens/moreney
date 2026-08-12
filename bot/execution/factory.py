"""Factory for selecting paper (default) or disabled live scaffolding."""

from bot.core.config import Settings
from bot.core.enums import ExecutionMode
from bot.core.interfaces import ExchangeClient
from bot.execution.base import BaseExecutor
from bot.execution.executor import ExecutionService, create_paper_execution
from bot.execution.live import LiveExecutor
from bot.portfolio.portfolio import PaperPortfolio


def create_executor(
    settings: Settings,
    *,
    exchange_client: ExchangeClient | None = None,
    live_enabled: bool = False,
    portfolio: PaperPortfolio | None = None,
) -> BaseExecutor:
    """Create the configured executor. Defaults to isolated paper stack."""
    if settings.execution_mode == ExecutionMode.PAPER:
        if portfolio is not None:
            return ExecutionService(settings, portfolio=portfolio)
        return create_paper_execution(settings)

    settings.require_live_credentials()
    if exchange_client is None:
        raise ValueError("Live execution requires an ExchangeClient")
    # Live remains scaffolding-only; ``enabled`` defaults false unless caller opts in.
    return LiveExecutor(exchange_client, enabled=live_enabled)

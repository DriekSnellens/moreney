"""Factory for selecting paper (default) or live micro executor."""

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
    use_multi_venue_live: bool = True,
) -> BaseExecutor:
    """Create the configured executor. Defaults to isolated paper stack.

    Live path uses ``MultiVenueLiveExecutor`` (policy-gated) when
    ``use_multi_venue_live=True``. PaperRunner must never call this with
    ``execution_mode=live``.
    """
    if settings.execution_mode == ExecutionMode.PAPER:
        if portfolio is not None:
            return ExecutionService(settings, portfolio=portfolio)
        return create_paper_execution(settings)

    settings.require_live_credentials()
    if use_multi_venue_live:
        from bot.live.executor import MultiVenueLiveExecutor

        # force_enabled follows live_enabled; policy still must unlock.
        return MultiVenueLiveExecutor(settings, force_enabled=live_enabled)

    if exchange_client is None:
        raise ValueError("Live execution requires an ExchangeClient")
    return LiveExecutor(exchange_client, enabled=live_enabled)

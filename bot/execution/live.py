"""Live executor scaffolding. Does not place real trades until adapters are ready."""

from bot.core.enums import OrderStatus
from bot.core.exceptions import ExecutionError
from bot.core.interfaces import ExchangeClient
from bot.core.models import ExecutionResult, OrderRequest
from bot.execution.base import BaseExecutor


class LiveExecutor(BaseExecutor):
    """Routes approved orders to an exchange adapter.

    Scaffolding only: real trading behavior is deferred. The client may still be
    a stub. No withdrawal paths exist.

    Prefer ``bot.live.executor.MultiVenueLiveExecutor`` for multi-venue live paths;
    both remain fail-closed unless explicitly enabled.
    """

    name = "live"

    def __init__(self, client: ExchangeClient, *, enabled: bool = False) -> None:
        self._client = client
        # Fail closed: live trading must be explicitly enabled when implementing.
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def execute(self, order: OrderRequest) -> ExecutionResult:
        if not self._enabled:
            raise ExecutionError(
                "LiveExecutor is disabled. Actual trading is not implemented yet; "
                "use PaperExecutor or enable after implementing exchange adapters."
            )
        result = await self._client.place_order(order)
        if result.status == OrderStatus.REJECTED:
            raise ExecutionError(result.message or "Exchange rejected order")
        return result

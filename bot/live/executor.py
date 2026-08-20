"""Multi-venue live executor — fail-closed unless micro gates pass."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.core.config import Settings
from bot.core.enums import OrderStatus
from bot.core.exceptions import ExecutionError
from bot.core.models import ExecutionResult, OrderRequest
from bot.execution.base import BaseExecutor
from bot.live.audit import LiveAuditLog
from bot.live.micro import MicroLivePolicy
from bot.live.registry import MultiVenueRegistry


class MultiVenueLiveExecutor(BaseExecutor):
    """Routes orders to per-venue clients only when micro-live policy allows.

    Default construction leaves trading disabled. PaperExecutor remains the
    only path used by PaperRunner.
    """

    name = "live_multi"

    def __init__(
        self,
        settings: Settings,
        *,
        registry: MultiVenueRegistry | None = None,
        policy: MicroLivePolicy | None = None,
        audit: LiveAuditLog | None = None,
        force_enabled: bool = False,
    ) -> None:
        self._settings = settings
        self._registry = registry or MultiVenueRegistry(settings)
        self._policy = policy or MicroLivePolicy(settings)
        self._audit = audit or LiveAuditLog(
            getattr(settings, "live_audit_path", "./data/live_audit.jsonl")
        )
        self._force_enabled = force_enabled
        self._open_orders = 0
        self._daily_loss = Decimal("0")

    def trading_allowed(self) -> tuple[bool, str]:
        if self._force_enabled:
            return self._policy.can_place_orders()
        return False, "MultiVenueLiveExecutor not force-enabled (scaffolding)"

    async def execute(self, order: OrderRequest) -> ExecutionResult:
        allowed, reason = self.trading_allowed()
        venue = str(
            getattr(order, "exchange", None)
            or (order.metadata or {}).get("exchange")
            or (order.metadata or {}).get("venue")
            or ""
        ).lower()
        symbol = str(order.symbol)
        px = Decimal(str(order.limit_price or 0))
        qty = Decimal(str(order.quantity or 0))
        notional = px * qty if px > 0 else qty

        ok, detail = self._policy.validate_order(
            venue=venue or "unknown",
            symbol=symbol,
            notional_eur=notional,
            open_orders=self._open_orders,
            daily_loss_eur=self._daily_loss,
        )
        if not allowed or not ok:
            msg = f"Live order blocked: {reason if not allowed else detail}"
            self._audit.record(
                "order_blocked",
                {"venue": venue, "symbol": symbol, "reason": msg},
            )
            raise ExecutionError(msg)

        client = self._registry.get_client(venue, enable_trading=True)
        if client is None:
            raise ExecutionError(f"No credentials/client for venue {venue}")

        self._audit.record(
            "order_submit",
            {"venue": venue, "symbol": symbol, "quantity": str(qty), "price": str(px)},
        )
        result = await client.place_order(order)
        self._audit.record(
            "order_result",
            {
                "venue": venue,
                "symbol": symbol,
                "status": str(result.status),
                "message": result.message,
            },
        )
        if result.status == OrderStatus.REJECTED:
            raise ExecutionError(result.message or "Exchange rejected order")
        self._open_orders += 1
        return result

    def status(self) -> dict[str, Any]:
        allowed, reason = self.trading_allowed()
        return {
            "name": self.name,
            "scaffolding": not self._force_enabled,
            "trading_allowed": allowed,
            "block_reason": None if allowed else reason,
            "policy": self._policy.status(),
            "registry": self._registry.status(),
            "withdrawals_supported": False,
        }

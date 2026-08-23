"""Multi-venue live executor — fail-closed unless micro gates pass."""

from __future__ import annotations

import logging
import time
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

logger = logging.getLogger(__name__)


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
        self._open_orders_checked_mono = 0.0
        self._open_orders_cache_sec = 5.0

    def trading_allowed(self) -> tuple[bool, str]:
        if self._force_enabled:
            return self._policy.can_place_orders()
        return False, "MultiVenueLiveExecutor not force-enabled (scaffolding)"

    async def refresh_open_order_count(
        self, venue: str | None = None, *, force: bool = False
    ) -> int:
        """Sync open-order counter from the exchange (cached to limit API load)."""
        now = time.monotonic()
        if (
            not force
            and now - self._open_orders_checked_mono < self._open_orders_cache_sec
        ):
            return self._open_orders

        venues = (
            [venue.strip().lower()]
            if venue
            else list(self._policy.allowed_venues())
        )
        total = 0
        for name in venues:
            if not name:
                continue
            client = self._registry.get_client(name, enable_trading=True)
            if client is None or not hasattr(client, "fetch_open_orders"):
                continue
            try:
                orders = await client.fetch_open_orders()
                total += len(orders or [])
            except Exception:  # noqa: BLE001
                logger.warning("refresh_open_order_count failed for %s", name)
        self._open_orders = total
        self._open_orders_checked_mono = now
        return total

    def note_open_orders(self, count: int) -> None:
        self._open_orders = max(0, int(count))
        # Local note is advisory until the next forced exchange refresh.
        self._open_orders_checked_mono = 0.0

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

        try:
            await self.refresh_open_order_count(venue or None)
        except Exception:  # noqa: BLE001
            logger.warning("open-order refresh skipped before place")

        side = str(getattr(order, "side", "") or "")
        ok, detail = self._policy.validate_order(
            venue=venue or "unknown",
            symbol=symbol,
            notional_eur=notional,
            open_orders=self._open_orders,
            daily_loss_eur=self._daily_loss,
            side=side,
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
        filled = Decimal(str(result.filled_quantity or 0))
        status_val = (
            result.status.value if hasattr(result.status, "value") else str(result.status)
        )
        # Only count still-open orders toward the policy cap.
        if filled <= 0 and str(status_val).lower() not in {"filled", "closed"}:
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
            "open_orders_tracked": self._open_orders,
            "withdrawals_supported": False,
        }

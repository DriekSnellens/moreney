"""Micro-live trading engine — separate from PaperRunner, fail-closed.

Places real orders only when MicroLivePolicy allows AND the caller passes
``confirm=true``. Paper trading never imports or uses this engine.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from functools import lru_cache
from typing import Any
from uuid import uuid4

from bot.core.config import Settings, get_settings
from bot.core.enums import OpportunitySide
from bot.core.exceptions import ExecutionError
from bot.core.models import ExecutionResult, OrderRequest
from bot.live.audit import LiveAuditLog
from bot.live.executor import MultiVenueLiveExecutor
from bot.live.micro import MicroLivePolicy
from bot.live.micro_unlock import dry_run_order, unlock_checklist
from bot.live.registry import MultiVenueRegistry

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


class LiveMicroEngine:
    """Manual / API-driven micro live orders with hard policy gates."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        registry: MultiVenueRegistry | None = None,
        policy: MicroLivePolicy | None = None,
        audit: LiveAuditLog | None = None,
        executor: MultiVenueLiveExecutor | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or MultiVenueRegistry(self._settings)
        self._policy = policy or MicroLivePolicy(self._settings)
        self._audit = audit or LiveAuditLog(
            getattr(self._settings, "live_audit_path", "./data/live_audit.jsonl")
        )
        # force_enabled=True means policy gates decide — not a silent "always on".
        self._executor = executor or MultiVenueLiveExecutor(
            self._settings,
            registry=self._registry,
            policy=self._policy,
            audit=self._audit,
            force_enabled=True,
        )
        self._session_orders: list[dict[str, Any]] = []
        self._armed = False  # process-local arming; still needs policy unlocks

    @property
    def policy(self) -> MicroLivePolicy:
        return self._policy

    @property
    def executor(self) -> MultiVenueLiveExecutor:
        return self._executor

    def arm(self) -> dict[str, Any]:
        """Arm the engine for this process. Does not place orders or flip env flags."""
        allowed, reason = self._policy.can_place_orders()
        if not allowed:
            self._armed = False
            return {"armed": False, "ok": False, "reason": reason}
        self._armed = True
        self._audit.record("micro_engine_armed", {"ok": True})
        return {"armed": True, "ok": True, "reason": "ok"}

    def disarm(self) -> dict[str, Any]:
        self._armed = False
        self._audit.record("micro_engine_disarmed", {})
        return {"armed": False, "ok": True}

    def status(self) -> dict[str, Any]:
        allowed, reason = self._policy.can_place_orders()
        return {
            "engine": "live_micro",
            "armed": self._armed,
            "paper_runner_coupled": False,
            "can_place_orders": allowed and self._armed,
            "policy_allows": allowed,
            "block_reason": (
                None
                if (allowed and self._armed)
                else (reason if not allowed else "engine_not_armed")
            ),
            "policy": self._policy.status(),
            "executor": self._executor.status(),
            "session_order_count": len(self._session_orders),
            "open_orders_tracked": self._executor._open_orders,
            "open_orders_by_venue": dict(self._executor._open_orders_by_venue),
            "daily_loss_tracked": str(self._executor._daily_loss),
            "unlock_checklist": unlock_checklist(self._settings),
            "withdrawals_supported": False,
            "note": (
                "PaperRunner never uses this engine. "
                "Arm via POST /live/micro/arm after env unlocks, "
                "then POST /live/micro/orders with confirm=true."
            ),
        }

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Policy dry-run — never hits the exchange order endpoint."""
        return dry_run_order(
            self._settings,
            venue=str(payload.get("venue") or "bitvavo"),
            symbol=str(payload.get("symbol") or "BTCEUR"),
            side=str(payload.get("side") or "buy"),
            quantity=payload.get("quantity") or "0.001",
            limit_price=payload.get("limit_price"),
            notional_eur=payload.get("notional_eur"),
        )

    async def _resolve_quantity(
        self,
        *,
        venue: str,
        symbol: str,
        side: OpportunitySide,
        quantity: Decimal | None,
        notional_eur: Decimal | None,
        limit_price: Decimal | None,
    ) -> tuple[Decimal, Decimal, Decimal | None]:
        """Return (quantity, notional_eur, limit_price)."""
        if quantity is not None and quantity > 0:
            if limit_price and limit_price > 0:
                return quantity, quantity * limit_price, limit_price
            if notional_eur and notional_eur > 0:
                return quantity, notional_eur, limit_price
            # Market order with qty only — estimate notional from ticker
            px = await self._fetch_ref_price(venue, symbol, side)
            return quantity, quantity * px, limit_price

        if notional_eur is None or notional_eur <= 0:
            raise ExecutionError("quantity or notional_eur is required")

        px = limit_price if limit_price and limit_price > 0 else await self._fetch_ref_price(
            venue, symbol, side
        )
        if px <= 0:
            raise ExecutionError("cannot size order: reference price unavailable")
        qty = (notional_eur / px).quantize(Decimal("0.00000001"))
        if qty <= 0:
            raise ExecutionError("computed quantity is zero")
        return qty, notional_eur, limit_price

    async def _fetch_ref_price(
        self, venue: str, symbol: str, side: OpportunitySide
    ) -> Decimal:
        client = self._registry.get_client(venue, enable_trading=False)
        if client is None:
            raise ExecutionError(f"No credentials for venue {venue}")
        snap = await client.fetch_ticker(symbol)
        if side == OpportunitySide.BUY:
            return Decimal(str(snap.ask or snap.last or 0))
        return Decimal(str(snap.bid or snap.last or 0))

    async def submit(
        self,
        payload: dict[str, Any],
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Submit a real micro order when armed + policy unlocked + confirm."""
        if not confirm:
            return {
                "submitted": False,
                "executed": False,
                "reason": "confirmation_required",
                "message": 'POST body must include "confirm": true',
                "preview": self.preview(payload),
            }

        if not self._armed:
            return {
                "submitted": False,
                "executed": False,
                "reason": "engine_not_armed",
                "message": "POST /live/micro/arm first (after env unlocks)",
                "status": self.status(),
            }

        venue = str(payload.get("venue") or "bitvavo").strip().lower()
        symbol = str(payload.get("symbol") or "BTCEUR").strip().upper()
        side_raw = str(payload.get("side") or "buy").lower()
        side = OpportunitySide.BUY if side_raw.startswith("b") else OpportunitySide.SELL
        limit_price = (
            Decimal(str(payload["limit_price"]))
            if payload.get("limit_price") is not None
            else None
        )
        quantity = (
            Decimal(str(payload["quantity"]))
            if payload.get("quantity") is not None
            else None
        )
        notional = (
            Decimal(str(payload["notional_eur"]))
            if payload.get("notional_eur") is not None
            else None
        )

        try:
            qty, notional_eur, limit_price = await self._resolve_quantity(
                venue=venue,
                symbol=symbol,
                side=side,
                quantity=quantity,
                notional_eur=notional,
                limit_price=limit_price,
            )
        except ExecutionError as exc:
            self._audit.record("order_size_failed", {"error": str(exc)})
            return {
                "submitted": False,
                "executed": False,
                "reason": "sizing_failed",
                "message": str(exc),
            }

        try:
            await self._executor.refresh_open_order_count(venue)
        except Exception:  # noqa: BLE001
            pass
        venue_open = self._executor.open_orders_for(venue)
        ok, detail = self._policy.validate_order(
            venue=venue,
            symbol=symbol,
            notional_eur=notional_eur,
            open_orders=venue_open,
            daily_loss_eur=self._executor._daily_loss,
            side=side,
        )
        if not ok:
            self._audit.record(
                "order_blocked",
                {"venue": venue, "symbol": symbol, "reason": detail},
            )
            return {
                "submitted": False,
                "executed": False,
                "reason": "policy_blocked",
                "message": detail,
            }

        order = OrderRequest(
            opportunity_id=uuid4(),
            symbol=symbol,
            side=side,
            quantity=qty,
            limit_price=limit_price,
            client_order_id=str(uuid4()),
            metadata={
                "venue": venue,
                "exchange": venue,
                "micro_live": True,
                "notional_eur": str(notional_eur),
                "post_only": bool(payload.get("post_only") or payload.get("postOnly")),
            },
        )

        try:
            result: ExecutionResult = await self._executor.execute(order)
        except ExecutionError as exc:
            return {
                "submitted": False,
                "executed": False,
                "reason": "execution_error",
                "message": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Micro order unexpected failure")
            self._audit.record(
                "micro_order_exception",
                {"error": type(exc).__name__, "message": str(exc)[:300]},
            )
            return {
                "submitted": False,
                "executed": False,
                "reason": "unexpected_error",
                "message": f"{type(exc).__name__}: {exc}",
            }

        dry = bool((result.metadata or {}).get("dry_run"))
        row = {
            "order_id": str(result.order_id),
            "venue": venue,
            "symbol": symbol,
            "side": side.value,
            "quantity": str(qty),
            "notional_eur": str(notional_eur),
            "status": str(result.status.value if hasattr(result.status, "value") else result.status),
            "filled_quantity": str(result.filled_quantity),
            "average_price": str(result.average_price) if result.average_price else None,
            "message": result.message,
            "dry_run": dry,
            "exchange_order_id": (result.metadata or {}).get("exchange_order_id"),
        }
        self._session_orders.append(row)
        self._audit.record("micro_order_result", row)
        return {
            "submitted": True,
            "executed": not dry,
            "dry_run": dry,
            "order": row,
            "withdrawals_supported": False,
        }


@lru_cache
def get_micro_engine() -> LiveMicroEngine:
    return LiveMicroEngine()


def reset_micro_engine() -> None:
    get_micro_engine.cache_clear()

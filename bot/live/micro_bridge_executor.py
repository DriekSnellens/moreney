"""Bridge PaperRunner's PaperExecutor path to LiveMicroEngine with a hard budget.

PaperRunner stays paper-only in source. This adapter is wired only by the
full-bot micro session: same strategy → GOE → profitability → risk pipeline,
but marketable fills on allowlisted venues go live (capped). Maker/post-only
quotes stay paper so the full quoting stack still runs without resting live
orders by default.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderSide, OrderStatus, OrderType
from bot.core.models import ExecutionResult, OrderRequest
from bot.execution.paper_executor import PaperExecutor
from bot.live.micro_engine import LiveMicroEngine
from bot.portfolio.models import Fill, Order
from bot.portfolio.portfolio import PaperPortfolio
from bot.portfolio.venue_ledger import infer_base_asset

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_MIN_LIVE_NOTIONAL = Decimal("5")


class MicroBudgetLiveExecutor(PaperExecutor):
    """PaperExecutor subclass: live taker fills + paper maker, € budget cap."""

    name = "micro_budget_live"

    def __init__(
        self,
        settings: Settings,
        *,
        portfolio: PaperPortfolio,
        live_engine: LiveMicroEngine,
        budget_eur: Decimal,
        execute_venues: set[str] | None = None,
        exclude_bases: set[str] | None = None,
        live_maker: bool = False,
    ) -> None:
        super().__init__(settings, portfolio=portfolio)
        self._live = live_engine
        self._budget = Decimal(str(budget_eur))
        self._spent = _ZERO
        self._execute_venues = {
            v.strip().lower() for v in (execute_venues or {"bitvavo"}) if v.strip()
        }
        self._exclude_bases = {
            b.strip().upper() for b in (exclude_bases or {"BTC"}) if b.strip()
        }
        self._live_maker = bool(live_maker)
        self.skips: dict[str, int] = {}
        self.live_trades: list[dict[str, Any]] = []

    @property
    def budget_remaining(self) -> Decimal:
        left = self._budget - self._spent
        return left if left > 0 else _ZERO

    def snapshot_bridge(self) -> dict[str, Any]:
        return {
            "budget_eur": str(self._budget),
            "spent_eur": str(self._spent),
            "remaining_eur": str(self.budget_remaining),
            "execute_venues": sorted(self._execute_venues),
            "exclude_bases": sorted(self._exclude_bases),
            "live_maker": self._live_maker,
            "skips": dict(self.skips),
            "live_trade_count": len(self.live_trades),
        }

    def _bump_skip(self, key: str) -> None:
        self.skips[key] = self.skips.get(key, 0) + 1

    def _resolve_venue(self, order_request: OrderRequest) -> str:
        meta = order_request.metadata or {}
        venue = str(meta.get("venue") or meta.get("exchange") or "").strip().lower()
        if venue:
            return venue
        # Single-venue / desk paths often omit venue; EUR pairs → Bitvavo pocket.
        sym = order_request.symbol.upper().replace("/", "").replace("-", "")
        if sym.endswith("EUR"):
            return "bitvavo"
        return ""

    async def execute(
        self,
        order_request: OrderRequest,
        *,
        order_book: Any = None,
        strategy: str = "",
        order_type: OrderType = OrderType.LIMIT,
    ) -> ExecutionResult:
        meta = dict(order_request.metadata or {})
        post_only = bool(meta.get("post_only"))
        if post_only and not self._live_maker:
            # Full maker stack still runs; fills stay simulated.
            return await super().execute(
                order_request,
                order_book=order_book,
                strategy=strategy,
                order_type=order_type,
            )

        symbol = order_request.symbol.upper().replace("/", "").replace("-", "")
        base = infer_base_asset(symbol)
        if base in self._exclude_bases or symbol.startswith("BTC"):
            self._bump_skip("excluded_base")
            return await self._reject_before_live(
                order_request, reason="EXCLUDED_BASE", message=f"base {base} excluded"
            )

        venue = self._resolve_venue(order_request)
        if venue not in self._execute_venues:
            self._bump_skip("venue_not_live")
            return await self._reject_before_live(
                order_request,
                reason="VENUE_NOT_LIVE",
                message=f"venue {venue or 'unknown'} has no live keys in this session",
            )

        remaining = self.budget_remaining
        if remaining < _MIN_LIVE_NOTIONAL:
            self._bump_skip("budget_exhausted")
            return await self._reject_before_live(
                order_request,
                reason="BUDGET_EXHAUSTED",
                message=f"micro budget remaining {remaining}",
            )

        px = Decimal(str(order_request.limit_price or 0))
        qty = Decimal(str(order_request.quantity or 0))
        if px <= 0 or qty <= 0:
            self._bump_skip("bad_size")
            return await self._reject_before_live(
                order_request, reason="BAD_SIZE", message="quantity/price required"
            )

        notional = qty * px
        if notional > remaining:
            qty = (remaining / px).quantize(Decimal("0.00000001"))
            notional = qty * px
            if qty <= 0 or notional < _MIN_LIVE_NOTIONAL:
                self._bump_skip("budget_too_small")
                return await self._reject_before_live(
                    order_request,
                    reason="BUDGET_TOO_SMALL",
                    message="resized quantity below live minimum",
                )
            order_request = order_request.model_copy(update={"quantity": qty})

        side = "buy" if order_request.side == OpportunitySide.BUY else "sell"
        payload = {
            "venue": venue,
            "symbol": symbol,
            "side": side,
            "quantity": str(qty),
            "limit_price": str(px) if px > 0 else None,
            "notional_eur": str(notional.quantize(Decimal("0.01"))),
            "confirm": True,
        }
        out = await self._live.submit(payload, confirm=True)
        row = {
            "symbol": symbol,
            "venue": venue,
            "side": side,
            "requested_qty": str(qty),
            "requested_notional": str(notional),
            "result": out,
        }
        self.live_trades.append(row)

        if not out.get("executed"):
            self._bump_skip(str(out.get("reason") or "live_not_executed"))
            return await self._reject_before_live(
                order_request,
                reason="LIVE_NOT_EXECUTED",
                message=str(out.get("message") or out.get("reason") or "not executed"),
            )

        order_row = out.get("order") or {}
        filled = Decimal(str(order_row.get("filled_quantity") or 0))
        avg = Decimal(str(order_row.get("average_price") or px or 0))
        if filled <= 0 or avg <= 0:
            self._bump_skip("live_zero_fill")
            return await self._reject_before_live(
                order_request,
                reason="LIVE_ZERO_FILL",
                message="live reported executed but zero fill",
            )

        fill_notional = filled * avg
        self._spent += fill_notional
        return await self._mirror_live_fill(
            order_request,
            filled_qty=filled,
            average_price=avg,
            venue=venue,
            strategy=strategy,
            exchange_order_id=order_row.get("exchange_order_id"),
        )

    async def _reject_before_live(
        self,
        order_request: OrderRequest,
        *,
        reason: str,
        message: str,
    ) -> ExecutionResult:
        side = (
            OrderSide.BUY
            if order_request.side == OpportunitySide.BUY
            else OrderSide.SELL
        )
        order = Order(
            id=order_request.id,
            strategy=str((order_request.metadata or {}).get("strategy") or ""),
            symbol=order_request.symbol,
            side=side,
            order_type=OrderType.LIMIT,
            requested_quantity=order_request.quantity,
            requested_price=order_request.limit_price,
            status=OrderStatus.PENDING,
            exchange="micro_bridge",
            opportunity_id=order_request.opportunity_id,
            client_order_id=order_request.client_order_id,
            metadata={**(order_request.metadata or {}), "executor": self.name},
        )
        self._orders.add(order)
        return self._reject(order, order_request, reason=reason, message=message)

    async def _mirror_live_fill(
        self,
        order_request: OrderRequest,
        *,
        filled_qty: Decimal,
        average_price: Decimal,
        venue: str,
        strategy: str,
        exchange_order_id: Any,
    ) -> ExecutionResult:
        """Keep paper portfolio/risk in sync with the live pocket."""
        side = (
            OrderSide.BUY
            if order_request.side == OpportunitySide.BUY
            else OrderSide.SELL
        )
        order = Order(
            id=order_request.id,
            strategy=strategy or str((order_request.metadata or {}).get("strategy") or ""),
            symbol=order_request.symbol,
            side=side,
            order_type=OrderType.MARKET,
            requested_quantity=filled_qty,
            requested_price=average_price,
            status=OrderStatus.PENDING,
            exchange=venue,
            opportunity_id=order_request.opportunity_id,
            client_order_id=order_request.client_order_id
            or f"micro-mirror-{uuid4().hex[:12]}",
            metadata={
                **(order_request.metadata or {}),
                "executor": self.name,
                "live_mirrored": True,
                "venue": venue,
            },
        )
        self._orders.add(order)
        self._orders.set_status(order.id, OrderStatus.OPEN)

        fee_rate = self._fee_rate_for(order)
        fee = filled_qty * average_price * fee_rate
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=filled_qty,
            price=average_price,
            fee=fee,
            fee_asset=self._quote_asset_for(order),
            slippage=_ZERO,
            exchange=venue,
            metadata={
                "live_mirrored": True,
                "fee_rate": str(fee_rate),
                "exchange_order_id": exchange_order_id,
            },
        )
        self._orders.attach_fill(order.id, fill)
        self._fills.apply(order, fill)
        try:
            self._apply_venue_ledger(order, filled_qty, average_price, fee)
        except Exception:  # noqa: BLE001
            logger.exception("micro_bridge venue ledger sync failed")
        self._portfolio.set_mark_price(order.symbol, average_price)

        result = ExecutionResult(
            order_id=order.id,
            opportunity_id=order_request.opportunity_id,
            status=OrderStatus.FILLED,
            filled_quantity=filled_qty,
            average_price=average_price,
            fees_usd=fee,
            message="Live micro fill mirrored into paper pocket",
            metadata={
                "executor": self.name,
                "exchange": venue,
                "real_exchange_order": True,
                "micro_live": True,
                "fee": str(fee),
                "exchange_order_id": exchange_order_id,
            },
        )
        self.history.append(result)
        logger.info(
            "MICRO_LIVE_FILL symbol=%s venue=%s qty=%s px=%s spent=%s remaining=%s",
            order.symbol,
            venue,
            filled_qty,
            average_price,
            self._spent,
            self.budget_remaining,
        )
        return result

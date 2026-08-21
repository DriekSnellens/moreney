"""Bridge PaperRunner's PaperExecutor path to LiveMicroEngine with a capital pocket.

PaperRunner stays paper-only in source. This adapter is wired only by the
full-bot micro session: same strategy → GOE → profitability → risk pipeline,
but marketable fills on allowlisted venues go live within a € pocket that
recycles after sells (not a one-shot spend counter). Maker/post-only quotes
stay paper unless live_maker is enabled.
"""

from __future__ import annotations

import asyncio
import logging
import time
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
_FILL_POLL_SECONDS = 2.5
_FILL_POLL_INTERVAL = 0.3
_DEFAULT_RESTING_MAX_AGE_SEC = 90.0


class MicroBudgetLiveExecutor(PaperExecutor):
    """PaperExecutor subclass: live taker fills + paper maker, € capital pocket."""

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
        allowed_bases: set[str] | None = None,
    ) -> None:
        super().__init__(settings, portfolio=portfolio)
        self._live = live_engine
        self._budget = Decimal(str(budget_eur))
        self._turnover = _ZERO  # lifetime traded notional (stats only)
        self._execute_venues = {
            v.strip().lower() for v in (execute_venues or {"bitvavo"}) if v.strip()
        }
        self._exclude_bases = {
            b.strip().upper() for b in (exclude_bases or {"BTC"}) if b.strip()
        }
        self._allowed_bases = (
            {b.strip().upper() for b in allowed_bases if b and str(b).strip()}
            if allowed_bases is not None
            else None
        )
        self._live_maker = bool(live_maker)
        self.skips: dict[str, int] = {}
        self.live_trades: list[dict[str, Any]] = []
        self._last_sync: dict[str, Any] | None = None
        self._resting: list[dict[str, Any]] = []
        self.live_fill_count = 0
        self.live_transaction_count = 0  # +1 per buy of sell fill
        self.portfolio_value_eur: Decimal | None = None
        self.starting_portfolio_eur: Decimal | None = None
        self._resting_max_age_sec = float(
            getattr(settings, "live_micro_resting_max_age_sec", _DEFAULT_RESTING_MAX_AGE_SEC)
            or _DEFAULT_RESTING_MAX_AGE_SEC
        )
        self._bal_cache: list[Any] | None = None
        self._bal_cache_mono = 0.0
        self._bal_cache_sec = 2.5
        self._mark_fetched_at: dict[str, float] = {}
        self._mark_ttl_sec = 30.0
        self._last_orphan_sweep_mono = 0.0
        self._orphan_sweep_sec = 60.0

    def _invalidate_bal_cache(self) -> None:
        self._bal_cache = None
        self._bal_cache_mono = 0.0

    @property
    def free_quote_eur(self) -> Decimal:
        """Available EUR cash in the micro pocket (recycles after sells)."""
        try:
            return Decimal(str(self._portfolio.available(self._quote)))
        except Exception:  # noqa: BLE001
            return _ZERO

    @property
    def budget_remaining(self) -> Decimal:
        """Capital still free to deploy on buys — not a one-shot spend counter."""
        free = self.free_quote_eur
        if free < 0:
            return _ZERO
        return free if free <= self._budget else self._budget

    def snapshot_bridge(self) -> dict[str, Any]:
        pnl = None
        if self.portfolio_value_eur is not None and self.starting_portfolio_eur is not None:
            pnl = self.portfolio_value_eur - self.starting_portfolio_eur
        return {
            "budget_eur": str(self._budget),
            "free_quote_eur": str(self.free_quote_eur),
            "remaining_eur": str(self.budget_remaining),
            "turnover_eur": str(self._turnover),
            "portfolio_value_eur": (
                str(self.portfolio_value_eur) if self.portfolio_value_eur is not None else None
            ),
            "starting_portfolio_eur": (
                str(self.starting_portfolio_eur)
                if self.starting_portfolio_eur is not None
                else None
            ),
            "netto_winst_eur": str(pnl) if pnl is not None else None,
            "execute_venues": sorted(self._execute_venues),
            "exclude_bases": sorted(self._exclude_bases),
            "live_maker": self._live_maker,
            "skips": dict(self.skips),
            "live_trade_count": len(self.live_trades),
            "live_fill_count": int(self.live_fill_count),
            "live_transaction_count": int(self.live_transaction_count),
            "resting_orders": len(self._resting),
            "capital_model": "pocket",
            "last_sync": self._last_sync,
        }

    def _bump_skip(self, key: str) -> None:
        self.skips[key] = self.skips.get(key, 0) + 1

    def _resolve_venue(self, order_request: OrderRequest) -> str:
        meta = order_request.metadata or {}
        venue = str(meta.get("venue") or meta.get("exchange") or "").strip().lower()
        if venue:
            return venue
        sym = order_request.symbol.upper().replace("/", "").replace("-", "")
        if sym.endswith("EUR"):
            return "bitvavo"
        return ""

    def _trading_client(self, venue: str) -> Any | None:
        registry = getattr(self._live, "_registry", None)
        if registry is None:
            return None
        return registry.get_client(venue, enable_trading=True)

    async def reconcile_from_exchange(self, venue: str = "bitvavo") -> dict[str, Any]:
        """Pull live balances into the paper pocket + venue ledger for strategy sizing."""
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client", "venue": venue}
        try:
            snap = await client.get_balances()
        except Exception as exc:  # noqa: BLE001
            logger.warning("micro reconcile balance fetch failed: %s", type(exc).__name__)
            return {"ok": False, "reason": "balance_fetch_failed", "error": str(exc)[:200]}

        bals = list(snap.balances or [])
        self._bal_cache = bals
        self._bal_cache_mono = time.monotonic()
        mapped = self._portfolio.sync_live_balances(
            bals,
            quote_available_cap=self._budget,
            allowed_bases=self._allowed_bases,
            exclude_bases=self._exclude_bases,
        )
        # Maker strategy sizes sell legs via venue_ledger.available(venue, base).
        if self._portfolio.venue_ledger is None:
            self._portfolio.init_venue_ledger(
                [venue], starting_quote=_ZERO
            )
        ledger_balances: dict[str, Decimal] = {}
        for bal in bals:
            asset = str(bal.asset or "").upper()
            if not asset:
                continue
            if asset != self._quote:
                if asset in self._exclude_bases:
                    continue
                if self._allowed_bases is not None and asset not in self._allowed_bases:
                    continue
            free = Decimal(str(bal.free or 0))
            if free > 0:
                if asset == self._quote:
                    ledger_balances[asset] = min(free, self._budget)
                else:
                    ledger_balances[asset] = free
        self._portfolio.venue_ledger.replace_balances(venue, ledger_balances)
        portfolio_value = await self.refresh_portfolio_value(venue=venue, balances=bals)
        if self.starting_portfolio_eur is None and portfolio_value is not None:
            self.starting_portfolio_eur = portfolio_value

        self._last_sync = {
            "ok": True,
            "venue": venue,
            "balances": mapped,
            "ledger": {k: str(v) for k, v in sorted(ledger_balances.items())},
            "free_quote_eur": str(self.free_quote_eur),
            "remaining_eur": str(self.budget_remaining),
            "portfolio_value_eur": (
                str(self.portfolio_value_eur) if self.portfolio_value_eur is not None else None
            ),
        }
        logger.info(
            "MICRO_SYNC venue=%s free_eur=%s portfolio=%s remaining=%s assets=%s ledger=%s",
            venue,
            self.free_quote_eur,
            self.portfolio_value_eur,
            self.budget_remaining,
            sorted(mapped.keys()),
            sorted(ledger_balances.keys()),
        )
        return dict(self._last_sync)

    async def _fetch_balances_cached(self, venue: str) -> list[Any]:
        now = time.monotonic()
        if (
            self._bal_cache is not None
            and now - self._bal_cache_mono < self._bal_cache_sec
        ):
            return self._bal_cache
        client = self._trading_client(venue)
        if client is None:
            return self._bal_cache or []
        snap = await client.get_balances()
        self._bal_cache = list(snap.balances or [])
        self._bal_cache_mono = now
        return self._bal_cache

    async def refresh_portfolio_value(
        self,
        *,
        venue: str = "bitvavo",
        balances: list[Any] | None = None,
    ) -> Decimal | None:
        """Mark Bitvavo portfolio to EUR (cash + crypto × last/bid)."""
        client = self._trading_client(venue)
        if client is None:
            return self.portfolio_value_eur
        bals = balances
        if bals is None:
            try:
                bals = await self._fetch_balances_cached(venue)
            except Exception:  # noqa: BLE001
                return self.portfolio_value_eur

        total = _ZERO
        now = time.monotonic()
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset:
                continue
            qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                str(getattr(bal, "locked", 0) or 0)
            )
            if qty <= 0:
                continue
            if asset == self._quote:
                total += qty
                continue
            symbol = f"{asset}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol)
            fetched_at = self._mark_fetched_at.get(symbol, 0.0)
            stale = now - fetched_at >= self._mark_ttl_sec
            if mark is None or mark <= 0 or stale:
                try:
                    ticker = await client.fetch_ticker(symbol)
                    mark = Decimal(
                        str(ticker.last or ticker.bid or ticker.ask or 0)
                    )
                    if mark > 0:
                        self._portfolio.set_mark_price(symbol, mark)
                        self._mark_fetched_at[symbol] = now
                except Exception:  # noqa: BLE001
                    mark = self._portfolio.state.mark_prices.get(symbol)
            if mark is not None and mark > 0:
                total += qty * mark
        self.portfolio_value_eur = total
        return total

    async def _live_free(self, venue: str, asset: str) -> Decimal:
        try:
            bals = await self._fetch_balances_cached(venue)
        except Exception:  # noqa: BLE001
            return _ZERO
        key = asset.upper()
        for bal in bals:
            if str(getattr(bal, "asset", "")).upper() == key:
                return Decimal(str(getattr(bal, "free", 0) or 0))
        return _ZERO

    async def _poll_fill(
        self,
        *,
        venue: str,
        symbol: str,
        exchange_order_id: str | None,
        fallback_price: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Poll exchange order briefly; return (filled_qty, avg_price)."""
        if not exchange_order_id:
            return _ZERO, fallback_price
        client = self._trading_client(venue)
        if client is None or not hasattr(client, "fetch_order"):
            return _ZERO, fallback_price

        deadline = time.monotonic() + _FILL_POLL_SECONDS
        last_filled = _ZERO
        last_avg = fallback_price
        while True:
            try:
                order = await client.fetch_order(str(exchange_order_id), symbol)
            except Exception:  # noqa: BLE001
                break
            last_filled = Decimal(str(order.filled_quantity or 0))
            avg = order.average_price or order.price or fallback_price
            last_avg = Decimal(str(avg or fallback_price))
            status = order.status
            status_val = status.value if hasattr(status, "value") else str(status)
            if last_filled > 0:
                return last_filled, last_avg if last_avg > 0 else fallback_price
            if str(status_val).lower() in {
                "filled",
                "closed",
                "cancelled",
                "canceled",
                "rejected",
                "failed",
            }:
                return last_filled, last_avg if last_avg > 0 else fallback_price
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(_FILL_POLL_INTERVAL)
        return last_filled, last_avg if last_avg > 0 else fallback_price

    def _track_resting(
        self,
        *,
        venue: str,
        symbol: str,
        side: str,
        exchange_order_id: str | None,
        quantity: Decimal,
        price: Decimal,
        strategy: str,
        opportunity_id: Any,
    ) -> None:
        if not exchange_order_id:
            return
        self._resting.append(
            {
                "venue": venue,
                "symbol": symbol,
                "side": side,
                "exchange_order_id": str(exchange_order_id),
                "quantity": Decimal(str(quantity)),
                "price": Decimal(str(price)),
                "strategy": strategy,
                "opportunity_id": opportunity_id,
                "placed_mono": time.monotonic(),
            }
        )

    async def manage_resting_orders(self, venue: str = "bitvavo") -> dict[str, Any]:
        """Poll resting live orders: mirror fills, cancel stale quotes, free capital."""
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client"}
        mirrored = 0
        cancelled = 0
        still: list[dict[str, Any]] = []
        now = time.monotonic()
        max_age = self._resting_max_age_sec
        tracked_ids = {str(r.get("exchange_order_id")) for r in self._resting}

        for row in list(self._resting):
            oid = str(row.get("exchange_order_id") or "")
            symbol = str(row.get("symbol") or "")
            if not oid or not symbol:
                continue
            filled = _ZERO
            avg = Decimal(str(row.get("price") or 0))
            status_val = "open"
            try:
                order = await client.fetch_order(oid, symbol)
                filled = Decimal(str(order.filled_quantity or 0))
                avg = Decimal(
                    str(order.average_price or order.price or row.get("price") or 0)
                )
                status = order.status
                status_val = status.value if hasattr(status, "value") else str(status)
            except Exception:  # noqa: BLE001
                logger.warning("resting fetch_order failed id=%s", oid)

            if filled > 0 and avg > 0:
                from bot.core.models import OrderRequest
                from uuid import UUID

                side_raw = str(row.get("side") or "buy").lower()
                opp_side = (
                    OpportunitySide.BUY if side_raw.startswith("b") else OpportunitySide.SELL
                )
                opp_id = row.get("opportunity_id")
                try:
                    opp_uuid = opp_id if isinstance(opp_id, UUID) else uuid4()
                except Exception:  # noqa: BLE001
                    opp_uuid = uuid4()
                req = OrderRequest(
                    opportunity_id=opp_uuid,
                    symbol=symbol,
                    side=opp_side,
                    quantity=filled,
                    limit_price=avg,
                    metadata={"venue": venue, "exchange": venue},
                )
                await self._mirror_live_fill(
                    req,
                    filled_qty=filled,
                    average_price=avg,
                    venue=venue,
                    strategy=str(row.get("strategy") or "maker_inventory"),
                    exchange_order_id=oid,
                )
                mirrored += 1
                remaining_open = str(status_val).lower() in {"open", "submitted", "pending", "partial"}
                if remaining_open and filled < Decimal(str(row.get("quantity") or filled)):
                    # partial — keep tracking remainder
                    row["quantity"] = Decimal(str(row.get("quantity") or 0)) - filled
                    if row["quantity"] > 0:
                        still.append(row)
                continue

            age = now - float(row.get("placed_mono") or now)
            terminal = str(status_val).lower() in {
                "cancelled",
                "canceled",
                "rejected",
                "failed",
                "expired",
                "filled",
                "closed",
            }
            if terminal:
                continue
            if age >= max_age:
                try:
                    await client.cancel_order(oid, symbol)
                    cancelled += 1
                    self._invalidate_bal_cache()
                    self._bump_skip("stale_quote_cancelled")
                    logger.info(
                        "MICRO_STALE_CANCEL venue=%s symbol=%s id=%s age=%.1fs",
                        venue,
                        symbol,
                        oid,
                        age,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("stale cancel failed id=%s", oid)
                    still.append(row)
                continue
            still.append(row)

        self._resting = still
        tracked_ids = {str(r.get("exchange_order_id")) for r in self._resting}

        # Orphan sweep is throttled — avoid cancelling fresh quotes we just tracked.
        do_orphan = (
            max_age <= 0
            or now - self._last_orphan_sweep_mono >= self._orphan_sweep_sec
        )
        if do_orphan:
            self._last_orphan_sweep_mono = now
            try:
                open_orders = await client.fetch_open_orders()
            except Exception:  # noqa: BLE001
                open_orders = []
            for order in open_orders or []:
                oid = str(getattr(order, "id", None) or "")
                if not oid or oid in tracked_ids:
                    continue
                symbol = (
                    str(getattr(order, "symbol", "") or "")
                    .upper()
                    .replace("/", "")
                    .replace("-", "")
                )
                if not symbol:
                    continue
                try:
                    await client.cancel_order(oid, symbol)
                    cancelled += 1
                    self._invalidate_bal_cache()
                    self._bump_skip("orphan_open_cancelled")
                except Exception:  # noqa: BLE001
                    logger.warning("orphan cancel failed id=%s", oid)

        live_exec = getattr(self._live, "executor", None)
        if live_exec is not None and hasattr(live_exec, "refresh_open_order_count"):
            try:
                if cancelled and hasattr(live_exec, "note_open_orders"):
                    live_exec.note_open_orders(len(self._resting))
                await live_exec.refresh_open_order_count(venue, force=bool(cancelled))
            except Exception:  # noqa: BLE001
                pass
        if mirrored or cancelled:
            await self.reconcile_from_exchange(venue)
        return {
            "ok": True,
            "mirrored": mirrored,
            "cancelled": cancelled,
            "resting": len(self._resting),
        }

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
        side_is_buy = order_request.side == OpportunitySide.BUY
        if side_is_buy and remaining < _MIN_LIVE_NOTIONAL:
            self._bump_skip("budget_exhausted")
            return await self._reject_before_live(
                order_request,
                reason="BUDGET_EXHAUSTED",
                message=f"micro pocket free EUR {remaining}",
            )

        px = Decimal(str(order_request.limit_price or 0))
        qty = Decimal(str(order_request.quantity or 0))
        if px <= 0 or qty <= 0:
            self._bump_skip("bad_size")
            return await self._reject_before_live(
                order_request, reason="BAD_SIZE", message="quantity/price required"
            )

        # Size against live Bitvavo free balances (paper pocket can lag fills).
        if side_is_buy:
            live_eur = await self._live_free(venue, self._quote)
            spend_cap = min(remaining, live_eur) if live_eur > 0 else remaining
            notional = qty * px
            if notional > spend_cap:
                qty = (spend_cap / px).quantize(Decimal("0.00000001"))
                notional = qty * px
                if qty <= 0 or notional < _MIN_LIVE_NOTIONAL:
                    self._bump_skip("insufficient_live_quote")
                    return await self._reject_before_live(
                        order_request,
                        reason="INSUFFICIENT_LIVE_QUOTE",
                        message=f"live {self._quote} free {live_eur} pocket {remaining}",
                    )
                order_request = order_request.model_copy(update={"quantity": qty})
        else:
            live_base = await self._live_free(venue, base)
            if live_base <= 0:
                self._bump_skip("insufficient_live_base")
                return await self._reject_before_live(
                    order_request,
                    reason="INSUFFICIENT_LIVE_BASE",
                    message=f"live {base} free {live_base}",
                )
            if qty > live_base:
                qty = live_base.quantize(Decimal("0.00000001"))
                order_request = order_request.model_copy(update={"quantity": qty})
            notional = qty * px
            if notional < _MIN_LIVE_NOTIONAL:
                self._bump_skip("sell_below_min_notional")
                return await self._reject_before_live(
                    order_request,
                    reason="SELL_BELOW_MIN",
                    message=f"sell notional {notional} below {_MIN_LIVE_NOTIONAL}",
                )

        side = "buy" if side_is_buy else "sell"
        payload = {
            "venue": venue,
            "symbol": symbol,
            "side": side,
            "quantity": str(qty),
            "limit_price": str(px) if px > 0 else None,
            "notional_eur": str(notional.quantize(Decimal("0.01"))),
            "confirm": True,
            "post_only": post_only,
        }
        out = await self._live.submit(payload, confirm=True)
        self._invalidate_bal_cache()
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
            # Keep pocket honest after rejected/failed attempts.
            await self.reconcile_from_exchange(venue)
            return await self._reject_before_live(
                order_request,
                reason="LIVE_NOT_EXECUTED",
                message=str(out.get("message") or out.get("reason") or "not executed"),
            )

        order_row = out.get("order") or {}
        filled = Decimal(str(order_row.get("filled_quantity") or 0))
        avg = Decimal(str(order_row.get("average_price") or px or 0))
        if filled <= 0:
            filled, avg = await self._poll_fill(
                venue=venue,
                symbol=symbol,
                exchange_order_id=(
                    str(order_row.get("exchange_order_id"))
                    if order_row.get("exchange_order_id")
                    else None
                ),
                fallback_price=px,
            )

        if filled <= 0 or avg <= 0:
            # Resting order accepted — track for fill/cancel; sync locked balances.
            self._bump_skip("live_resting")
            self._track_resting(
                venue=venue,
                symbol=symbol,
                side=side,
                exchange_order_id=(
                    str(order_row.get("exchange_order_id"))
                    if order_row.get("exchange_order_id")
                    else None
                ),
                quantity=qty,
                price=px,
                strategy=strategy,
                opportunity_id=order_request.opportunity_id,
            )
            await self.reconcile_from_exchange(venue)
            return await self._reject_before_live(
                order_request,
                reason="LIVE_RESTING",
                message="order accepted on exchange; waiting for fill (pocket synced)",
            )

        fill_notional = filled * avg
        self._turnover += fill_notional
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
        self._invalidate_bal_cache()
        self.live_fill_count += 1
        self.live_transaction_count += 1

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
            "MICRO_LIVE_FILL symbol=%s venue=%s qty=%s px=%s turnover=%s free=%s",
            order.symbol,
            venue,
            filled_qty,
            average_price,
            self._turnover,
            self.budget_remaining,
        )
        return result

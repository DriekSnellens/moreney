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
        self.live_transaction_count = 0  # +1 per buy or sell fill
        self.realized_trade_pnl_eur = _ZERO  # closed-trade PnL after fees
        self.portfolio_value_eur: Decimal | None = None
        self.starting_portfolio_eur: Decimal | None = None
        # FIFO lots for realized PnL: base -> [(qty, unit_cost_eur)]
        self._cost_lots: dict[str, list[list[Decimal]]] = {}
        self._lots_seeded = False
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
        # Trailing take-profit: base -> {armed, peak, cost, triggered}
        self._trail: dict[str, dict[str, Any]] = {}
        self._trail_enabled = bool(
            getattr(settings, "paper_trail_take_profit_enabled", False)
        )
        self._trail_arm_gain = Decimal(
            str(getattr(settings, "paper_trail_arm_gain_pct", 0.30) or 0.30)
        )
        self._trail_drawdown = Decimal(
            str(getattr(settings, "paper_trail_drawdown_pct", 0.10) or 0.10)
        )
        self._trail_partial_enabled = bool(
            getattr(settings, "paper_trail_partial_enabled", True)
        )
        self._trail_partial_pct = Decimal(
            str(getattr(settings, "paper_trail_partial_pct", 0.50) or 0.50)
        )
        self._ladder_enabled = bool(
            getattr(settings, "paper_ladder_buy_enabled", False)
        )
        raw_ladder = str(
            getattr(settings, "paper_ladder_buy_pcts", "0.01,0.02,0.03") or ""
        )
        self._ladder_pcts: list[Decimal] = []
        for part in raw_ladder.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                self._ladder_pcts.append(Decimal(part))
            except Exception:  # noqa: BLE001
                continue
        if not self._ladder_pcts:
            self._ladder_pcts = [Decimal("0.01"), Decimal("0.02"), Decimal("0.03")]
        self._time_stop_enabled = bool(
            getattr(settings, "paper_time_stop_enabled", False)
        )
        self._time_stop_sec = float(
            getattr(settings, "paper_time_stop_sec", 86400) or 86400
        )
        self._dust_policy = str(
            getattr(settings, "paper_dust_policy", "off") or "off"
        ).strip().lower()
        self._regime_block_buys = bool(
            getattr(settings, "paper_regime_block_buys", True)
        )
        self._buys_blocked = False
        self._position_opened_mono: dict[str, float] = {}
        self._max_alt_bases = int(
            getattr(settings, "live_micro_max_alt_bases", 0) or 0
        )

    def set_buys_blocked(self, blocked: bool) -> None:
        """Regime guard: when True, reject new BUY orders (sells/trails still run)."""
        self._buys_blocked = bool(blocked)

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
            # Operator PnL = realized trade profit after fees (not mark-to-market).
            "netto_winst_eur": str(self.realized_trade_pnl_eur),
            "realized_trade_pnl_eur": str(self.realized_trade_pnl_eur),
            "execute_venues": sorted(self._execute_venues),
            "exclude_bases": sorted(self._exclude_bases),
            "live_maker": self._live_maker,
            "skips": dict(self.skips),
            "live_trade_count": len(self.live_trades),
            "live_fill_count": int(self.live_fill_count),
            "live_transaction_count": int(self.live_transaction_count),
            "resting_orders": len(self._resting),
            "capital_model": "pocket",
            "trail_take_profit": {
                "enabled": self._trail_enabled,
                "arm_gain_pct": str(self._trail_arm_gain),
                "drawdown_pct": str(self._trail_drawdown),
                "partial_enabled": self._trail_partial_enabled,
                "partial_pct": str(self._trail_partial_pct),
                "time_stop_sec": self._time_stop_sec if self._time_stop_enabled else None,
                "ladder_buy": self._ladder_enabled,
                "buys_blocked": self._buys_blocked,
                "dust_policy": self._dust_policy,
                "states": self._trail_states_public(),
            },
            "max_alt_bases": self._max_alt_bases,
            "held_alt_bases": sorted(self._held_alt_bases()),
            "last_sync": self._last_sync,
        }

    def _trail_states_public(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for base, st in sorted(self._trail.items()):
            cost = Decimal(str(st.get("cost") or 0))
            mark = Decimal(str(st.get("last_mark") or 0))
            gain = ((mark - cost) / cost) if cost > 0 and mark > 0 else _ZERO
            to_arm = self._trail_arm_gain - gain
            opened = self._position_opened_mono.get(base)
            age = (time.monotonic() - opened) if opened else None
            out[base] = {
                "armed": bool(st.get("armed")),
                "partial_done": bool(st.get("partial_done")),
                "peak": str(st.get("peak") or ""),
                "cost": str(cost) if cost > 0 else "",
                "mark": str(mark) if mark > 0 else "",
                "gain_pct": f"{float(gain * 100):.2f}",
                "pct_to_arm": f"{float(to_arm * 100):.2f}",
                "triggered": bool(st.get("triggered")),
                "age_sec": round(age, 1) if age is not None else None,
            }
        return out

    def _note_position_opened(self, base: str) -> None:
        key = base.upper()
        if key not in self._position_opened_mono:
            self._position_opened_mono[key] = time.monotonic()

    def _held_alt_bases(self) -> set[str]:
        """Distinct non-quote assets with meaningful live/paper inventory."""
        held: set[str] = set()
        min_notional = Decimal(
            str(getattr(self._settings, "paper_maker_min_notional_eur", 10) or 10)
        )
        for symbol, pos in self._portfolio.state.positions.items():
            if pos.quantity <= 0:
                continue
            base = infer_base_asset(symbol)
            if not base or base == self._quote or base in self._exclude_bases:
                continue
            mark = self._portfolio.state.mark_prices.get(symbol) or pos.average_entry_price
            if mark and pos.quantity * mark >= min_notional:
                held.add(base)
            elif pos.quantity > 0 and (mark is None or mark <= 0):
                held.add(base)
        # Also count balances that may not yet have a position row.
        for asset, bal in self._portfolio.state.balances.items():
            a = str(asset or "").upper()
            if not a or a == self._quote or a in self._exclude_bases:
                continue
            if bal.total <= 0:
                continue
            symbol = f"{a}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol) or _ZERO
            if mark > 0 and bal.total * mark < min_notional:
                continue  # dust — don't burn a concentration slot
            if mark <= 0 and bal.total < Decimal("0.001"):
                continue
            held.add(a)
        return held

    def _seed_cost_lots_from_balances(self, bals: list[Any]) -> None:
        """Seed FIFO cost basis at current marks so pre-session inventory isn't 'free'."""
        if self._lots_seeded:
            return
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote:
                continue
            qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                str(getattr(bal, "locked", 0) or 0)
            )
            if qty <= 0:
                continue
            symbol = f"{asset}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol)
            if mark is None or mark <= 0:
                continue
            self._cost_lots.setdefault(asset, []).append([qty, Decimal(str(mark))])
            self._note_position_opened(asset)
        self._lots_seeded = True

    def _record_realized_fill(
        self,
        *,
        side: OrderSide,
        symbol: str,
        qty: Decimal,
        price: Decimal,
        fee: Decimal,
    ) -> None:
        """Update FIFO lots / realized PnL for a live mirrored fill."""
        if qty <= 0 or price <= 0:
            return
        base = infer_base_asset(symbol)
        lots = self._cost_lots.setdefault(base, [])
        if side == OrderSide.BUY:
            # Unit cost includes fee so sells net fee-correct PnL.
            unit = (qty * price + fee) / qty
            lots.append([qty, unit])
            self._note_position_opened(base)
            return
        remaining = qty
        proceeds = qty * price - fee
        cost = _ZERO
        while remaining > 0 and lots:
            lot_qty, lot_cost = lots[0]
            take = min(remaining, lot_qty)
            cost += take * lot_cost
            lot_qty -= take
            remaining -= take
            if lot_qty <= 0:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty
        if remaining > 0:
            # Sold more than tracked lots — cost unknown; treat leftover at fill price
            # so PnL for that slice is ≈ −fee only.
            cost += remaining * price
        self.realized_trade_pnl_eur += proceeds - cost
        if not lots:
            self._position_opened_mono.pop(base, None)
            self._trail.pop(base, None)

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
            self._seed_cost_lots_from_balances(bals)

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

    def _unit_cost(self, base: str) -> Decimal | None:
        lots = self._cost_lots.get(base.upper()) or []
        total_qty = _ZERO
        total_cost = _ZERO
        for qty, unit in lots:
            if qty <= 0 or unit <= 0:
                continue
            total_qty += qty
            total_cost += qty * unit
        if total_qty > 0:
            return total_cost / total_qty
        symbol = f"{base.upper()}{self._quote}"
        pos = self._portfolio.state.positions.get(symbol)
        if pos is not None and pos.quantity > 0 and pos.average_entry_price > 0:
            return Decimal(str(pos.average_entry_price))
        return None

    def _break_even_sell_price(self, base: str) -> Decimal | None:
        unit = self._unit_cost(base)
        if unit is None or unit <= 0:
            return None
        from bot.core.venue_fees import venue_maker_fee

        fee = venue_maker_fee("bitvavo")
        denom = Decimal("1") - fee
        if denom <= 0:
            return None
        be = unit / denom
        buffer_bps = Decimal(
            str(getattr(self._settings, "paper_maker_sell_profit_buffer_bps", 0) or 0)
        )
        if buffer_bps > 0:
            be *= Decimal("1") + buffer_bps / Decimal("10000")
        return be

    def _trail_update_state(
        self, base: str, *, cost: Decimal, mark: Decimal
    ) -> dict[str, Any]:
        """Arm at +arm_gain vs cost; track peak; return state (may set trigger)."""
        st = self._trail.setdefault(
            base,
            {
                "armed": False,
                "peak": _ZERO,
                "cost": cost,
                "last_mark": mark,
                "triggered": False,
                "partial_done": False,
                "newly_armed": False,
                "time_stop_due": False,
            },
        )
        st["newly_armed"] = False
        st["time_stop_due"] = False
        if st.get("triggered"):
            st["last_mark"] = mark
            return st
        st["cost"] = cost
        st["last_mark"] = mark
        if cost <= 0 or mark <= 0:
            return st
        gain = (mark - cost) / cost
        if not st.get("armed"):
            if gain >= self._trail_arm_gain:
                st["armed"] = True
                st["peak"] = mark
                st["newly_armed"] = True
                st["partial_done"] = False
                logger.info(
                    "TRAIL_ARM base=%s cost=%s mark=%s gain=%.2f%%",
                    base,
                    cost,
                    mark,
                    float(gain * 100),
                )
            elif self._time_stop_enabled:
                opened = self._position_opened_mono.get(base)
                if opened is not None and (
                    time.monotonic() - opened >= self._time_stop_sec
                ):
                    st["time_stop_due"] = True
            return st
        peak = Decimal(str(st.get("peak") or 0))
        if mark > peak:
            st["peak"] = mark
            peak = mark
        if peak > 0 and mark <= peak * (Decimal("1") - self._trail_drawdown):
            st["triggered"] = True
            logger.info(
                "TRAIL_TRIGGER base=%s cost=%s peak=%s mark=%s dd=%.2f%%",
                base,
                cost,
                peak,
                mark,
                float((Decimal("1") - mark / peak) * 100),
            )
        return st

    async def _mark_price(self, venue: str, symbol: str) -> Decimal | None:
        mark = self._portfolio.state.mark_prices.get(symbol)
        now = time.monotonic()
        fetched_at = self._mark_fetched_at.get(symbol, 0.0)
        if mark is not None and mark > 0 and now - fetched_at < self._mark_ttl_sec:
            return Decimal(str(mark))
        client = self._trading_client(venue)
        if client is None:
            return Decimal(str(mark)) if mark and mark > 0 else None
        try:
            ticker = await client.fetch_ticker(symbol)
            mark = Decimal(str(ticker.last or ticker.bid or ticker.ask or 0))
            if mark > 0:
                self._portfolio.set_mark_price(symbol, mark)
                self._mark_fetched_at[symbol] = now
                return mark
        except Exception:  # noqa: BLE001
            pass
        return Decimal(str(mark)) if mark and mark > 0 else None

    async def _cancel_resting_for_symbol(self, venue: str, symbol: str) -> int:
        client = self._trading_client(venue)
        if client is None:
            return 0
        cancelled = 0
        still: list[dict[str, Any]] = []
        for row in list(self._resting):
            if str(row.get("symbol") or "").upper() != symbol.upper():
                still.append(row)
                continue
            oid = str(row.get("exchange_order_id") or "")
            if not oid:
                continue
            try:
                await client.cancel_order(oid, symbol)
                cancelled += 1
                self._invalidate_bal_cache()
            except Exception:  # noqa: BLE001
                still.append(row)
        self._resting = still
        return cancelled

    async def _submit_exit_sell(
        self,
        *,
        venue: str,
        symbol: str,
        qty: Decimal,
        mark: Decimal,
        reason: str,
        limit_price: Decimal | None = None,
        post_only: bool = False,
    ) -> ExecutionResult:
        px = limit_price
        if px is None or px <= 0:
            px = (mark * Decimal("0.998")).quantize(Decimal("0.00000001"))
        req = OrderRequest(
            opportunity_id=uuid4(),
            symbol=symbol,
            side=OpportunitySide.SELL,
            quantity=qty,
            limit_price=px,
            metadata={
                "venue": venue,
                "exchange": venue,
                "trail_take_profit": True,
                "post_only": post_only,
                "strategy": reason,
                "exit_reason": reason,
            },
        )
        return await self.execute(
            req, strategy=reason, order_type=OrderType.LIMIT
        )

    async def check_trailing_take_profits(
        self, venue: str = "bitvavo"
    ) -> dict[str, Any]:
        """Partial at +arm, full exit on peak drawdown; time-stop at break-even."""
        if not self._trail_enabled and not self._time_stop_enabled:
            return {"ok": True, "enabled": False, "triggered": []}
        bals = await self._fetch_balances_cached(venue)
        triggered: list[dict[str, Any]] = []
        armed_now: list[str] = []
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote:
                continue
            if asset in self._exclude_bases:
                continue
            if self._allowed_bases is not None and asset not in self._allowed_bases:
                continue
            free = Decimal(str(getattr(bal, "free", 0) or 0))
            locked = Decimal(str(getattr(bal, "locked", 0) or 0))
            if free + locked <= 0:
                self._trail.pop(asset, None)
                self._position_opened_mono.pop(asset, None)
                continue
            cost = self._unit_cost(asset)
            if cost is None or cost <= 0:
                continue
            symbol = f"{asset}{self._quote}"
            mark = await self._mark_price(venue, symbol)
            if mark is None or mark <= 0:
                continue
            self._note_position_opened(asset)
            st = self._trail_update_state(asset, cost=cost, mark=mark)
            if st.get("armed") and not st.get("triggered"):
                armed_now.append(asset)

            sell_qty = _ZERO
            reason = ""
            limit_px: Decimal | None = None
            post_only = False

            # Partial take-profit right when trail arms.
            if (
                st.get("newly_armed")
                and self._trail_partial_enabled
                and not st.get("partial_done")
            ):
                if locked > 0:
                    await self._cancel_resting_for_symbol(venue, symbol)
                    self._invalidate_bal_cache()
                    free = await self._live_free(venue, asset)
                sell_qty = (free * self._trail_partial_pct).quantize(
                    Decimal("0.00000001")
                )
                reason = "trail_partial"
                st["partial_done"] = True
            elif st.get("triggered"):
                if locked > 0:
                    await self._cancel_resting_for_symbol(venue, symbol)
                    self._invalidate_bal_cache()
                    free = await self._live_free(venue, asset)
                sell_qty = free
                reason = "trail_drawdown"
            elif st.get("time_stop_due"):
                be = self._break_even_sell_price(asset)
                if be is not None and mark >= be:
                    if locked > 0:
                        await self._cancel_resting_for_symbol(venue, symbol)
                        self._invalidate_bal_cache()
                        free = await self._live_free(venue, asset)
                    sell_qty = free
                    reason = "time_stop_breakeven"
                    limit_px = max(be, mark * Decimal("0.999"))
                    post_only = True
                else:
                    self._bump_skip("time_stop_below_be")
                    continue
            else:
                continue

            if sell_qty <= 0 or sell_qty * mark < _MIN_LIVE_NOTIONAL:
                self._bump_skip("trail_dust")
                if reason == "trail_drawdown":
                    st["triggered"] = False
                continue

            result = await self._submit_exit_sell(
                venue=venue,
                symbol=symbol,
                qty=sell_qty,
                mark=mark,
                reason=reason,
                limit_price=limit_px,
                post_only=post_only,
            )
            row = {
                "base": asset,
                "symbol": symbol,
                "reason": reason,
                "qty": str(sell_qty),
                "mark": str(mark),
                "peak": str(st.get("peak")),
                "cost": str(cost),
                "status": str(result.status),
                "filled": str(result.filled_quantity),
            }
            triggered.append(row)
            self._bump_skip(reason)
            if reason == "trail_drawdown" and (
                result.status == OrderStatus.FILLED or result.filled_quantity > 0
            ):
                self._trail.pop(asset, None)
            elif reason == "trail_drawdown":
                st["triggered"] = True
            elif reason == "time_stop_breakeven" and (
                result.status == OrderStatus.FILLED or result.filled_quantity > 0
            ):
                self._trail.pop(asset, None)
            logger.info("TRAIL_SELL %s", row)
        return {
            "ok": True,
            "enabled": True,
            "armed": armed_now,
            "triggered": triggered,
            "states": self._trail_states_public(),
        }

    async def manage_dust_positions(
        self, venue: str = "bitvavo"
    ) -> dict[str, Any]:
        """Top up sub-min positions toward min notional, else exit at break-even."""
        policy = self._dust_policy
        if policy in {"", "off", "none"}:
            return {"ok": True, "policy": policy, "actions": []}
        min_notional = Decimal(
            str(getattr(self._settings, "paper_maker_min_notional_eur", 40) or 40)
        )
        bals = await self._fetch_balances_cached(venue)
        actions: list[dict[str, Any]] = []
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote or asset in self._exclude_bases:
                continue
            if self._allowed_bases is not None and asset not in self._allowed_bases:
                continue
            free = Decimal(str(getattr(bal, "free", 0) or 0))
            if free <= 0:
                continue
            symbol = f"{asset}{self._quote}"
            mark = await self._mark_price(venue, symbol)
            if mark is None or mark <= 0:
                continue
            notional = free * mark
            if notional < _MIN_LIVE_NOTIONAL:
                continue  # true dust — leave
            if notional >= min_notional:
                continue
            need_eur = min_notional - notional
            did = None
            if policy in {"top_up", "top_up_or_exit"} and not self._buys_blocked:
                held = self._held_alt_bases()
                can_add = asset in held or (
                    self._max_alt_bases <= 0 or len(held) < self._max_alt_bases
                )
                live_eur = await self._live_free(venue, self._quote)
                spend = min(need_eur * Decimal("1.01"), live_eur, self.budget_remaining)
                if can_add and spend >= _MIN_LIVE_NOTIONAL:
                    qty = (spend / mark).quantize(Decimal("0.00000001"))
                    px = (mark * Decimal("0.999")).quantize(Decimal("0.00000001"))
                    req = OrderRequest(
                        opportunity_id=uuid4(),
                        symbol=symbol,
                        side=OpportunitySide.BUY,
                        quantity=qty,
                        limit_price=px,
                        metadata={
                            "venue": venue,
                            "exchange": venue,
                            "post_only": True,
                            "dust_top_up": True,
                            "ladder_leg": True,
                            "strategy": "dust_top_up",
                        },
                    )
                    result = await self.execute(
                        req, strategy="dust_top_up", order_type=OrderType.LIMIT
                    )
                    did = {
                        "action": "top_up",
                        "base": asset,
                        "status": str(result.status),
                        "qty": str(qty),
                    }
                    self._bump_skip("dust_top_up")
            if did is None and policy in {"exit_breakeven", "top_up_or_exit"}:
                be = self._break_even_sell_price(asset)
                if be is not None and mark >= be:
                    result = await self._submit_exit_sell(
                        venue=venue,
                        symbol=symbol,
                        qty=free,
                        mark=mark,
                        reason="dust_exit_breakeven",
                        limit_price=max(be, mark * Decimal("0.999")),
                        post_only=True,
                    )
                    did = {
                        "action": "exit_breakeven",
                        "base": asset,
                        "status": str(result.status),
                        "qty": str(free),
                    }
                    self._bump_skip("dust_exit_breakeven")
            if did is not None:
                actions.append(did)
                logger.info("DUST_POLICY %s", did)
        return {"ok": True, "policy": policy, "actions": actions}

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

        if meta.get("buy_only") and not bool(
            getattr(self._settings, "paper_maker_allow_buy_only", True)
        ):
            self._bump_skip("buy_only_disabled")
            return await self._reject_before_live(
                order_request,
                reason="BUY_ONLY_DISABLED",
                message="winst-mode rejects buy-only quotes",
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
        if (
            side_is_buy
            and self._regime_block_buys
            and self._buys_blocked
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
        ):
            self._bump_skip("regime_block_buys")
            return await self._reject_before_live(
                order_request,
                reason="REGIME_BLOCK_BUYS",
                message="buys blocked while regime is reduce-only/toxic",
            )
        if side_is_buy and remaining < _MIN_LIVE_NOTIONAL:
            self._bump_skip("budget_exhausted")
            return await self._reject_before_live(
                order_request,
                reason="BUDGET_EXHAUSTED",
                message=f"micro pocket free EUR {remaining}",
            )
        # Trend profile: at most N distinct alt bases — add to existing, don't spray.
        if side_is_buy and self._max_alt_bases > 0:
            held = self._held_alt_bases()
            if base not in held and len(held) >= self._max_alt_bases:
                self._bump_skip("max_alt_bases")
                return await self._reject_before_live(
                    order_request,
                    reason="MAX_ALT_BASES",
                    message=(
                        f"already holding {sorted(held)} "
                        f"(max {self._max_alt_bases} bases for trail concentration)"
                    ),
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
            # Ladder entries: 3 post-only bids at −1/−2/−3% vs mark (⅓ size each).
            if (
                self._ladder_enabled
                and post_only
                and not meta.get("ladder_leg")
                and not meta.get("dust_top_up")
                and len(self._ladder_pcts) >= 2
            ):
                mark = await self._mark_price(venue, symbol)
                ref = mark if mark and mark > 0 else px
                leg_qty = (qty / Decimal(len(self._ladder_pcts))).quantize(
                    Decimal("0.00000001")
                )
                if leg_qty * ref >= _MIN_LIVE_NOTIONAL:
                    last_result: ExecutionResult | None = None
                    for dip in self._ladder_pcts:
                        leg_px = (ref * (Decimal("1") - dip)).quantize(
                            Decimal("0.00000001")
                        )
                        if leg_px <= 0:
                            continue
                        leg_req = order_request.model_copy(
                            update={
                                "id": uuid4(),
                                "quantity": leg_qty,
                                "limit_price": leg_px,
                                "metadata": {
                                    **meta,
                                    "ladder_leg": True,
                                    "ladder_dip_pct": str(dip),
                                    "post_only": True,
                                    "venue": venue,
                                },
                            }
                        )
                        last_result = await self.execute(
                            leg_req,
                            order_book=order_book,
                            strategy=strategy or "ladder_buy",
                            order_type=order_type,
                        )
                    self._bump_skip("ladder_buy")
                    if last_result is not None:
                        return last_result
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
            # Hard floor: never sell below fee-adjusted cost + profit buffer
            # (trail take-profit exits are already +30% then −10% from peak).
            if not bool(meta.get("trail_take_profit")):
                be = self._break_even_sell_price(base)
                if be is not None and be > px:
                    px = be
                    order_request = order_request.model_copy(update={"limit_price": px})
                if order_book is not None:
                    try:
                        best_bid = (
                            Decimal(str(order_book.bids[0].price))
                            if order_book.bids
                            else _ZERO
                        )
                    except Exception:  # noqa: BLE001
                        best_bid = _ZERO
                    if best_bid > 0 and px <= best_bid:
                        self._bump_skip("sell_below_break_even")
                        return await self._reject_before_live(
                            order_request,
                            reason="SELL_BELOW_BREAK_EVEN",
                            message=(
                                f"break-even ask {px} would cross bid {best_bid}; "
                                "waiting for profitable exit"
                            ),
                        )
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
        self._record_realized_fill(
            side=side,
            symbol=order.symbol,
            qty=filled_qty,
            price=average_price,
            fee=fee,
        )
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

"""Continuous 24/7 paper-trading runner.

Pipeline (async, WebSocket-driven market data — no REST polling):
  Market Data → Strategy → Profitability → Risk → Paper Execution
  → Portfolio → Statistics

HARD SAFETY:
* Never places real exchange orders
* Never enables live trading
* Never bypasses RiskEngine
* Skips new trades when market data is stale/unsynchronized
* Skips new trades when RiskEngine is PAUSED or EMERGENCY_STOP
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from bot.core.config import Settings
from bot.core.enums import ExecutionMode, KillSwitchState, OrderStatus
from bot.engine.orchestrator import TradeCycleResult, TradingEngine
from bot.execution.paper_executor import PaperExecutor
from bot.market_data.provider_realtime import RealtimeMarketDataProvider
from bot.market_data.service import MarketDataService
from bot.paper.store import PaperTradingStore
from bot.paper.tracker import PerformanceTracker
from bot.portfolio.portfolio import PaperPortfolio
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.risk.risk_engine import RiskEngine
from bot.strategies.arbitrage import CrossExchangeArbitrageStrategy
from bot.strategies.maker_inventory import MakerInventoryStrategy

logger = logging.getLogger(__name__)


class PaperRunner:
    """Async paper session controller + continuous evaluation loop."""

    def __init__(
        self,
        settings: Settings,
        *,
        market_data: MarketDataService,
        risk_engine: RiskEngine,
        store: PaperTradingStore | None = None,
        portfolio: PaperPortfolio | None = None,
        tracker: PerformanceTracker | None = None,
    ) -> None:
        if settings.execution_mode != ExecutionMode.PAPER:
            raise RuntimeError(
                "PaperRunner refuses to start unless EXECUTION_MODE=paper"
            )
        self._settings = settings
        self._market_data = market_data
        self._risk = risk_engine
        self._store = store or PaperTradingStore(settings)

        starting = Decimal(str(settings.paper_starting_eur))
        loaded = self._store.load(settings)
        if loaded is not None:
            self._portfolio, self._tracker, meta = loaded
            self._session_started_at = meta.get("session_started_at")
            self._errors = list(meta.get("errors") or [])
            self._accumulated_runtime = float(meta.get("runtime_seconds") or 0)
        else:
            self._portfolio = portfolio or PaperPortfolio(settings, starting_eur=starting)
            self._tracker = tracker or PerformanceTracker(
                starting_equity=self._portfolio.state.total_equity
            )
            self._session_started_at = None
            self._errors = []
            self._accumulated_runtime = 0.0

        self._configure_venue_inventory()

        self._executor = PaperExecutor(settings, portfolio=self._portfolio)
        self._strategy = self._build_strategy()
        self._profitability = DefaultProfitabilityEngine(settings)
        self._provider = RealtimeMarketDataProvider(market_data)
        self._engine = TradingEngine(
            market_data=self._provider,
            strategy=self._strategy,
            profitability=self._profitability,
            risk=self._risk,
            portfolio=self._portfolio,
            executor=self._executor,
        )

        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_cycle: dict[str, Any] | None = None
        self._cycle_count = 0
        self._run_started_monotonic: float | None = None
        self._symbols = [
            s.strip().upper().replace("-", "").replace("/", "")
            for s in settings.market_data_symbols.split(",")
            if s.strip()
        ]
        self._interval = max(0.05, settings.paper_cycle_interval_ms / 1000.0)

    def _build_strategy(self) -> CrossExchangeArbitrageStrategy | MakerInventoryStrategy:
        if self._settings.paper_maker_enabled:
            return MakerInventoryStrategy(self._settings)
        return CrossExchangeArbitrageStrategy(self._settings)

    def _configure_venue_inventory(self) -> None:
        if not self._settings.paper_venue_inventory:
            return
        if self._portfolio.venue_ledger is not None:
            return
        venues = [
            part.strip()
            for part in self._settings.market_data_exchanges.split(",")
            if part.strip()
        ]
        self._portfolio.init_venue_ledger(
            venues,
            starting_quote=Decimal(str(self._settings.paper_starting_eur)),
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def portfolio(self) -> PaperPortfolio:
        return self._portfolio

    @property
    def tracker(self) -> PerformanceTracker:
        return self._tracker

    @property
    def store(self) -> PaperTradingStore:
        return self._store

    @property
    def last_cycle(self) -> dict[str, Any] | None:
        return self._last_cycle

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def runtime_seconds(self) -> float:
        extra = 0.0
        if self._running and self._run_started_monotonic is not None:
            extra = time.monotonic() - self._run_started_monotonic
        return self._accumulated_runtime + extra

    # ------------------------------------------------------------------
    # Session control
    # ------------------------------------------------------------------

    async def start(self) -> dict[str, Any]:
        if not self._settings.paper_trading_enabled:
            return {"started": False, "reason": "PAPER_TRADING_ENABLED=false"}
        if self._running:
            return {"started": True, "already_running": True, **self.status()}

        # Shared mode: hydrate from the publisher via Redis (no local WebSockets).
        # Local mode: connect public feeds when no synchronized books exist yet.
        if self._market_data.shared_mode:
            if not self._market_data._started:  # noqa: SLF001
                await self._market_data.start_shared_consumer()
        elif not self._market_data_tradeable():
            if not self._market_data._adapters:  # noqa: SLF001
                self._market_data._adapters = self._market_data._build_live_adapters()  # noqa: SLF001
            if not self._market_data._started:  # noqa: SLF001
                await self._market_data.start()

        self._running = True
        self._session_started_at = datetime.now(UTC).isoformat()
        self._run_started_monotonic = time.monotonic()
        self._task = asyncio.create_task(self._loop(), name="paper-runner")
        self._persist()
        logger.info(
            "PAPER_SESSION_STARTED starting_equity=%s symbols=%s",
            self._portfolio.state.total_equity,
            self._symbols,
        )
        return {"started": True, "already_running": False, **self.status()}

    async def stop(self) -> dict[str, Any]:
        if not self._running:
            return {"stopped": True, "already_stopped": True, **self.status()}
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._run_started_monotonic is not None:
            self._accumulated_runtime += time.monotonic() - self._run_started_monotonic
            self._run_started_monotonic = None
        self._persist()
        logger.info("PAPER_SESSION_STOPPED runtime_s=%s", self.runtime_seconds())
        return {"stopped": True, "already_stopped": False, **self.status()}

    async def reset(self, *, confirm: bool) -> dict[str, Any]:
        """Reset paper portfolio and statistics. Never touches real exchange accounts."""
        if not confirm:
            return {
                "reset": False,
                "reason": "confirmation_required",
                "message": "POST /paper/reset requires {\"confirm\": true}",
            }
        await self.stop()
        starting = Decimal(str(self._settings.paper_starting_eur))
        self._store.clear()
        self._portfolio = PaperPortfolio(self._settings, starting_eur=starting)
        self._tracker = PerformanceTracker(starting_equity=starting)
        self._configure_venue_inventory()
        self._executor = PaperExecutor(self._settings, portfolio=self._portfolio)
        self._strategy = self._build_strategy()
        self._engine = TradingEngine(
            market_data=self._provider,
            strategy=self._strategy,
            profitability=self._profitability,
            risk=self._risk,
            portfolio=self._portfolio,
            executor=self._executor,
        )
        self._last_cycle = None
        self._cycle_count = 0
        self._errors = []
        self._accumulated_runtime = 0.0
        self._session_started_at = None
        self._persist()
        logger.info("PAPER_SESSION_RESET starting_eur=%s", starting)
        return {
            "reset": True,
            "starting_equity": str(starting),
            "real_exchange_accounts_affected": False,
            **self.status(),
        }

    async def shutdown(self) -> None:
        """Graceful shutdown: stop loop and persist state."""
        await self.stop()
        self._persist()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — keep runner alive
                msg = f"{type(exc).__name__}: {exc}"
                self._errors.append(msg)
                logger.exception("PAPER_CYCLE_ERROR %s", msg)
            await asyncio.sleep(self._interval)

    async def _run_cycle(self) -> None:
        # Safety: do not trade when kill switch blocks new orders.
        ks = self._risk.kill_switch.status() if hasattr(self._risk, "kill_switch") else None
        if ks is not None and ks.state in {
            KillSwitchState.PAUSED,
            KillSwitchState.EMERGENCY_STOP,
        }:
            self._last_cycle = {
                "blocked": True,
                "reason": f"risk_engine_{ks.state.value}",
                "cycle": self._cycle_count,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            return

        # Safety: require at least one synchronized, non-stale venue.
        if not self._market_data_tradeable():
            self._last_cycle = {
                "blocked": True,
                "reason": "stale_or_unsynchronized_market_data",
                "cycle": self._cycle_count,
                "timestamp": datetime.now(UTC).isoformat(),
                "market_data": self._market_data.status(),
            }
            return

        await self._match_and_expire_quotes()

        cycle_results: list[dict[str, Any]] = []
        for symbol in self._symbols:
            equity_before = self._portfolio.state.total_equity
            result = await self._engine.run_once(symbol)
            self._ingest_cycle(result, equity_before=equity_before)
            cycle_results.append(self._summarize_cycle(result))

        scan = {}
        if hasattr(self._strategy, "scan_stats"):
            scan = self._strategy.scan_stats()
            self._tracker.record_scan_stats(scan)
        self._tracker.sync_portfolio(self._portfolio)
        self._cycle_count += 1
        self._last_cycle = {
            "blocked": False,
            "cycle": self._cycle_count,
            "timestamp": datetime.now(UTC).isoformat(),
            "symbols": cycle_results,
            "equity": str(self._portfolio.state.total_equity),
            "scan": scan,
            "real_exchange_order": False,
            "execution_mode": ExecutionMode.PAPER.value,
        }
        if self._cycle_count % 5 == 0:
            self._persist()

    def _market_data_tradeable(self) -> bool:
        status = self._market_data.status()
        for health in status.values():
            if (
                health.get("connected")
                and health.get("synchronized")
                and not health.get("stale")
            ):
                return True
        # Injected / test books may report synchronized without a live manager.
        for symbol in self._symbols:
            snaps = self._market_data.snapshots_for_arbitrage(symbol)
            if len(snaps) >= 2:
                return True
        return False

    def _ingest_cycle(self, result: TradeCycleResult, *, equity_before: Decimal) -> None:
        # Map profitability / risk by opportunity id
        profit_by_id = {p.opportunity_id: p for p in result.profitability}
        risk_by_id = {d.opportunity_id: d for d in result.risk_decisions}
        exec_by_opp: dict[UUID, list[ExecutionResult]] = {}
        for execution in result.executions:
            exec_by_opp.setdefault(execution.opportunity_id, []).append(execution)
        orders_by_opp = {
            o.opportunity_id: o for o in result.orders if o.opportunity_id is not None
        }
        fills_by_opp: dict[UUID, list] = {}
        for order in result.orders:
            if order.opportunity_id is None:
                continue
            for fill in result.fills:
                if fill.order_id == order.id:
                    fills_by_opp.setdefault(order.opportunity_id, []).append(fill)

        for opportunity in result.opportunities:
            profit = profit_by_id.get(opportunity.id)
            self._tracker.record_detected(opportunity, profit)
            decision = risk_by_id.get(opportunity.id)
            if decision is not None:
                self._tracker.record_risk(opportunity.id, decision)
            executions = exec_by_opp.get(opportunity.id) or []
            if executions:
                # Primary execution is the buy leg (first); all legs share opportunity fills.
                execution = executions[0]
                opp_orders = [
                    o for o in result.orders if o.opportunity_id == opportunity.id
                ]
                fills = fills_by_opp.get(opportunity.id, [])
                for order in opp_orders:
                    self._store.save_order(order)
                for fill in fills:
                    self._store.save_fill(fill)
                equity_after = self._portfolio.state.total_equity
                self._tracker.record_execution(
                    opportunity.id,
                    execution,
                    orders=opp_orders or None,
                    fills=fills,
                    equity_before=equity_before,
                    equity_after=equity_after,
                )
                if execution.status not in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}:
                    if execution.status in {OrderStatus.REJECTED, OrderStatus.FAILED}:
                        pass

        self._tracker.sync_portfolio(self._portfolio)
        self._store.save_portfolio(self._portfolio)

    @staticmethod
    def _summarize_cycle(result: TradeCycleResult) -> dict[str, Any]:
        return {
            "symbol": result.symbol,
            "opportunities": len(result.opportunities),
            "approved": sum(1 for d in result.risk_decisions if d.approved),
            "rejected": len(result.rejected),
            "executions": len(result.executions),
            "fills": len(result.fills),
            "equity": str(result.portfolio_equity) if result.portfolio_equity is not None else None,
        }

    def _persist(self) -> None:
        self._store.persist(
            portfolio=self._portfolio,
            tracker=self._tracker,
            session_running=self._running,
            session_started_at=self._session_started_at,
            errors=self._errors,
            runtime_seconds=self.runtime_seconds(),
        )

    def status(self) -> dict[str, Any]:
        snap = self._tracker.snapshot()
        ks = None
        if hasattr(self._risk, "kill_switch"):
            ks = self._risk.kill_switch.status().model_dump(mode="json")
        return {
            "paper_trading_enabled": self._settings.paper_trading_enabled,
            "running": self._running,
            "session_started_at": self._session_started_at,
            "cycle_count": self._cycle_count,
            "runtime_seconds": self.runtime_seconds(),
            "starting_equity": str(snap.starting_equity),
            "current_equity": str(snap.current_equity),
            "net_pnl": str(snap.net_pnl),
            "total_opportunities": snap.total_opportunities,
            "approved_opportunities": snap.approved_opportunities,
            "rejected_opportunities": snap.rejected_opportunities,
            "executed_opportunities": snap.executed_opportunities,
            "pairs_evaluated": snap.pairs_evaluated,
            "depth_edges_found": snap.depth_edges_found,
            "scan_rejections": snap.scan_rejections,
            "reject_counts": (
                self._strategy.scan_stats().get("reject_counts")
                if hasattr(self._strategy, "scan_stats")
                else {}
            ),
            "trade_count": snap.trade_count,
            "errors": self._errors[-20:],
            "kill_switch": ks,
            "execution_mode": ExecutionMode.PAPER.value,
            "real_orders_placed": 0,
            "withdrawals": 0,
            "leverage": 0,
            "market_data": self._market_data.status(),
            "last_cycle": self._last_cycle,
            "open_maker_quotes": self._open_maker_quote_count(),
            "strategy": getattr(self._strategy, "name", ""),
            "live_forecast": self._live_forecast(snap),
        }

    def _live_forecast(self, snap: Any) -> dict[str, Any]:
        """Project live-equivalent PnL from the conservative paper model."""
        runtime = max(self.runtime_seconds(), 1.0)
        hours = Decimal(str(runtime)) / Decimal("3600")
        net = snap.net_pnl
        per_hour = net / hours if hours > 0 else Decimal("0")
        per_day = per_hour * Decimal("24")
        start = snap.starting_equity
        day_ret = (per_day / start * Decimal("100")) if start > 0 else Decimal("0")
        if snap.trade_count >= 20 and runtime >= 1800:
            confidence = "medium"
            note = (
                "Gebaseerd op trade-through fills, maker-fees, max 30 bps edge, "
                "eenzijdige fills afgesloten als taker. Geen garantie."
            )
        elif snap.trade_count == 0 and runtime >= 120:
            confidence = "medium"
            note = (
                "Geen live-haalbare maker-deals gevonden: retail-fees (≈16–30 bps "
                "heen-en-weer) eten de echte edges op publieke EUR-boeken. "
                "Verwachte winst met echt geld ≈ €0 tot licht negatief."
            )
            per_hour = Decimal("0")
            per_day = Decimal("0")
            day_ret = Decimal("0")
        elif snap.trade_count >= 5 and runtime >= 600:
            confidence = "low"
            note = "Nog weinig data; richtinggevende live-inschatting."
        else:
            confidence = "very_low"
            note = (
                "Te vroeg om te extrapoleren. Getoonde winst is wel live-conservatief "
                "(trade-through + one-leg taker exits)."
            )
            # Do not annualize a handful of fills into a fake daily forecast.
            per_hour = Decimal("0")
            per_day = Decimal("0")
            day_ret = Decimal("0")
        return {
            "label": "Live-inschatting (haalbaar met echt geld)",
            "realized_live_eur": _dec_str(net),
            "projected_per_hour_eur": _dec_str(per_hour),
            "projected_per_day_eur": _dec_str(per_day),
            "projected_day_return_pct": _dec_str(day_ret),
            "projection_ready": confidence in {"low", "medium", "high"},
            "confidence": confidence,
            "note": note,
            "runtime_seconds": runtime,
            "assumptions": [
                "Alleen maker-fills als de markt door je prijs heen handelt",
                "Geen at-touch queue fills",
                "Edges > 30 bps afgewezen als stale feed",
                "Geen same-venue MM op publieke L2",
                "Eén been gevuld → tegengestelde exit met taker + 6 bps adverse",
            ],
        }

    def _open_maker_quote_count(self) -> int:
        manager = self._executor.order_manager
        ids = {
            o.opportunity_id
            for o in manager.open_orders()
            if (o.metadata or {}).get("post_only") and o.opportunity_id is not None
        }
        return len(ids)

    def _collect_books(self) -> dict[str, dict[str, Any]]:
        books: dict[str, dict[str, Any]] = {}
        exchanges = [
            part.strip()
            for part in self._settings.market_data_exchanges.split(",")
            if part.strip()
        ]
        for symbol in self._symbols:
            for exchange in exchanges:
                book = self._provider.get_order_book(exchange, symbol)
                if book is not None:
                    books.setdefault(exchange, {})[symbol] = book
        return books

    async def _match_and_expire_quotes(self) -> None:
        books = self._collect_books()
        fills = self._executor.match_resting(books)
        if fills:
            self._ingest_delayed_fills(fills)
        await self._cancel_stale_quotes()

    def _ingest_delayed_fills(self, executions: list[Any]) -> None:
        by_opp: dict[UUID, list[Any]] = {}
        for execution in executions:
            by_opp.setdefault(execution.opportunity_id, []).append(execution)
        manager = self._executor.order_manager
        for opp_id, execs in by_opp.items():
            orders = [o for o in manager.list_orders() if o.opportunity_id == opp_id]
            fills = [fill for order in orders for fill in order.fills]
            execution = execs[-1]
            for candidate in execs:
                if candidate.filled_quantity > 0:
                    execution = candidate
            for order in orders:
                self._store.save_order(order)
            for fill in fills:
                self._store.save_fill(fill)
            self._tracker.record_execution(
                opp_id,
                execution,
                orders=orders or None,
                fills=fills,
            )
        self._tracker.sync_portfolio(self._portfolio)
        self._store.save_portfolio(self._portfolio)

    async def _cancel_stale_quotes(self) -> None:
        max_age = float(getattr(self._settings, "paper_maker_max_age_ms", 0) or 0)
        if max_age <= 0:
            return
        now_ms = int(time.time() * 1000)
        expired_opps: set[UUID] = set()
        for order in list(self._executor.order_manager.open_orders()):
            if not (order.metadata or {}).get("post_only"):
                continue
            placed = float((order.metadata or {}).get("placed_ms") or now_ms)
            if now_ms - placed < max_age:
                continue
            await self._executor.cancel(order.id, reason="maker_quote_expired")
            if order.opportunity_id is not None:
                expired_opps.add(order.opportunity_id)
        for opp_id in expired_opps:
            await self._close_orphaned_maker_legs(opp_id)

    async def _close_orphaned_maker_legs(self, opportunity_id: UUID) -> None:
        """If only one maker leg filled, exit leftover inventory as taker (live risk)."""
        if not getattr(self._settings, "paper_maker_one_leg_exit", True):
            return
        from bot.core.enums import OrderSide

        manager = self._executor.order_manager
        orders = [o for o in manager.list_orders() if o.opportunity_id == opportunity_id]
        buy_filled = sum(
            (o.filled_quantity for o in orders if o.side == OrderSide.BUY),
            Decimal("0"),
        )
        sell_filled = sum(
            (o.filled_quantity for o in orders if o.side == OrderSide.SELL),
            Decimal("0"),
        )
        matched = min(buy_filled, sell_filled)
        buy_left = buy_filled - matched
        sell_left = sell_filled - matched
        if buy_left <= 0 and sell_left <= 0:
            return

        exits: list[Any] = []
        books = self._collect_books()
        if buy_left > 0:
            buy_order = next((o for o in orders if o.side == OrderSide.BUY), None)
            venue = str((buy_order.metadata or {}).get("venue") or "") if buy_order else ""
            symbol = buy_order.symbol if buy_order else ""
            book = (books.get(venue) or {}).get(symbol)
            result = await self._executor.close_one_leg(
                opportunity_id=opportunity_id,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=buy_left,
                venue=venue,
                order_book=book,
                reason="one_leg_exit_buy_filled",
            )
            if result is not None:
                exits.append(result)
        if sell_left > 0:
            sell_order = next((o for o in orders if o.side == OrderSide.SELL), None)
            venue = str((sell_order.metadata or {}).get("venue") or "") if sell_order else ""
            symbol = sell_order.symbol if sell_order else ""
            book = (books.get(venue) or {}).get(symbol)
            result = await self._executor.close_one_leg(
                opportunity_id=opportunity_id,
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=sell_left,
                venue=venue,
                order_book=book,
                reason="one_leg_exit_sell_filled",
            )
            if result is not None:
                exits.append(result)
        if exits:
            self._ingest_delayed_fills(exits)


def _dec_str(value: Decimal) -> str:
    """Stable decimal string for dashboards (no 0E+30 surprises)."""
    quantized = value.quantize(Decimal("0.0001"))
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"

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

        self._executor = PaperExecutor(settings, portfolio=self._portfolio)
        self._strategy = CrossExchangeArbitrageStrategy(settings)
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
        self._executor = PaperExecutor(self._settings, portfolio=self._portfolio)
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
        }

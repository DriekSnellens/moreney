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
from bot.strategies.triangle_bridge import CompositeDeskStrategy, TriangleBridgeStrategy
from bot.strategies.global_composite import GlobalCompositeStrategy
from bot.strategies.funding_basis import FundingBasisStrategy
from bot.strategies.fx_relative_value import FxRelativeValueStrategy
from bot.strategies.equity_mean_reversion import EquityMeanReversionStrategy
from bot.paper.markout import MarkoutTracker
from bot.core.venue_fees import set_fee_tier
from bot.portfolio.venue_ledger import infer_quote_asset
from bot.opportunity.engine import GlobalOpportunityEngine
from bot.opportunity.decision_log import OpportunityDecisionLogger
from bot.opportunity.scanner import TieredScanScheduler
from bot.markets.registry import InstrumentRegistry
from bot.markets.calendar import MarketCalendarService
from bot.regime.detector import RegimeDetector

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
        self._decision_log = OpportunityDecisionLogger()
        loaded = self._store.load(settings)
        if loaded is not None:
            self._portfolio, self._tracker, meta = loaded
            self._session_started_at = meta.get("session_started_at")
            self._errors = list(meta.get("errors") or [])
            self._accumulated_runtime = float(meta.get("runtime_seconds") or 0)
            if meta.get("decision_log"):
                self._decision_log.import_entries(meta["decision_log"])
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
        gate = self._gate_settings()
        self._profitability = DefaultProfitabilityEngine(gate)
        if settings.paper_maker_enabled:
            # Align risk absolute floor with maker NET threshold (not taker-arb defaults).
            self._risk = RiskEngine(gate, kill_switch=risk_engine.kill_switch)
        self._provider = RealtimeMarketDataProvider(market_data)
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
        self._markout = MarkoutTracker()
        self._fx_refilled: set[UUID] = set()
        self._last_markout_bps: float | None = None
        self._instrument_registry = InstrumentRegistry(settings)
        self._market_calendar = MarketCalendarService()
        self._regime = RegimeDetector()
        self._scan_scheduler = TieredScanScheduler(
            settings, self._instrument_registry, self._market_calendar
        )
        self._opportunity_engine = self._build_opportunity_engine(gate)
        set_fee_tier(getattr(settings, "paper_fee_tier", "retail"))
        self._engine = TradingEngine(
            market_data=self._provider,
            strategy=self._strategy,
            profitability=self._profitability,
            risk=self._risk,
            portfolio=self._portfolio,
            executor=self._executor,
            opportunity_engine=(
                self._opportunity_engine
                if settings.global_opportunity_engine_enabled
                else None
            ),
        )

    def _build_opportunity_engine(self, gate: Settings) -> GlobalOpportunityEngine:
        win_rate = None
        if self._markout.snapshot().get("samples", 0):
            try:
                samples = int(self._markout.snapshot().get("samples", 0))
                if samples > 0:
                    win_rate = 0.55
            except (TypeError, ValueError):
                pass
        engine = GlobalOpportunityEngine(
            gate,
            profitability=self._profitability,
            risk=self._risk,
            registry=self._instrument_registry,
            calendar=self._market_calendar,
            regime=self._regime,
            decision_log=self._decision_log,
            markout_win_rate=win_rate,
        )
        return engine

    def _build_strategy(self):
        if getattr(self._settings, "global_use_global_composite", True):
            children: list = []
            if self._settings.paper_maker_enabled:
                maker = MakerInventoryStrategy(self._settings)
                children.append(maker)
                if getattr(self._settings, "paper_triangle_enabled", False):
                    children.append(TriangleBridgeStrategy(self._settings))
            else:
                children.append(CrossExchangeArbitrageStrategy(self._settings))
            if getattr(self._settings, "global_funding_strategy_enabled", True):
                children.append(FundingBasisStrategy(self._settings))
            if getattr(self._settings, "global_fx_enabled", False):
                children.append(FxRelativeValueStrategy(self._settings))
            if getattr(self._settings, "global_equity_enabled", False):
                children.append(EquityMeanReversionStrategy(self._settings))
            if len(children) == 1:
                return children[0]
            return GlobalCompositeStrategy(self._settings, children=children)
        if self._settings.paper_maker_enabled:
            maker = MakerInventoryStrategy(self._settings)
            if getattr(self._settings, "paper_triangle_enabled", False):
                return CompositeDeskStrategy(
                    self._settings,
                    maker=maker,
                    triangle=TriangleBridgeStrategy(self._settings),
                )
            return maker
        return CrossExchangeArbitrageStrategy(self._settings)

    def _gate_settings(self) -> Settings:
        """Profitability/risk thresholds used after strategy emit (must match maker gate)."""
        settings = self._settings
        if not settings.paper_maker_enabled:
            return settings
        return settings.model_copy(
            update={
                "profitability_min_net_profit_usd": settings.paper_maker_min_profit_eur,
                "profitability_min_net_return": settings.arbitrage_min_profit_pct,
                "profitability_apply_funding": False,
                "profitability_slippage_bps": 0.0,
                "profitability_thin_book_penalty_bps": 0.0,
                "profitability_execution_buffer_bps": 1.0
                + float(getattr(settings, "paper_maker_adverse_bps", 0) or 0),
                "risk_min_net_profit_usd": settings.paper_maker_min_profit_eur,
            }
        )

    def _configure_venue_inventory(self) -> None:
        if not self._settings.paper_venue_inventory:
            return
        if self._portfolio.venue_ledger is not None:
            return
        raw_maker = str(getattr(self._settings, "paper_maker_venues", "") or "")
        maker_venues = [part.strip() for part in raw_maker.split(",") if part.strip()]
        if maker_venues and self._settings.paper_maker_enabled:
            venues = maker_venues
        else:
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
        gate = self._gate_settings()
        self._profitability = DefaultProfitabilityEngine(gate)
        if self._settings.paper_maker_enabled:
            self._risk = RiskEngine(gate, kill_switch=self._risk.kill_switch)
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
        self._fx_refilled = set()
        self._markout = MarkoutTracker()
        self._last_markout_bps = None
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

        equity_before = self._portfolio.state.total_equity
        scan_symbols = self._scan_scheduler.symbols_for_cycle(all_symbols=self._symbols)
        result = await self._engine.run_universe(scan_symbols)
        self._ingest_cycle(result, equity_before=equity_before)

        risk_by_id = {d.opportunity_id: d for d in result.risk_decisions}
        by_symbol: dict[str, dict[str, Any]] = {
            symbol: {
                "symbol": symbol,
                "opportunities": 0,
                "approved": 0,
                "rejected": 0,
                "executions": 0,
                "fills": 0,
                "equity": str(self._portfolio.state.total_equity),
            }
            for symbol in self._symbols
        }
        for opp in result.opportunities:
            row = by_symbol.setdefault(
                opp.symbol,
                {
                    "symbol": opp.symbol,
                    "opportunities": 0,
                    "approved": 0,
                    "rejected": 0,
                    "executions": 0,
                    "fills": 0,
                    "equity": str(self._portfolio.state.total_equity),
                },
            )
            row["opportunities"] += 1
            decision = risk_by_id.get(opp.id)
            if decision is not None and decision.approved:
                row["approved"] += 1
            elif decision is not None:
                row["rejected"] += 1
        for execution in result.executions:
            symbol = next(
                (
                    o.symbol
                    for o in result.opportunities
                    if o.id == execution.opportunity_id
                ),
                result.symbol,
            )
            row = by_symbol.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "opportunities": 0,
                    "approved": 0,
                    "rejected": 0,
                    "executions": 0,
                    "fills": 0,
                    "equity": str(self._portfolio.state.total_equity),
                },
            )
            row["executions"] += 1
        cycle_results = [by_symbol[s] for s in self._symbols if s in by_symbol]
        for symbol, row in by_symbol.items():
            if symbol not in self._symbols:
                cycle_results.append(row)

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
            "ranking": result.opportunity_ranking,
            "real_exchange_order": False,
            "execution_mode": ExecutionMode.PAPER.value,
            "universe_scan": True,
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
            decision_log=self._decision_log.export(),
        )


    def _inventory_snapshot(self) -> dict[str, Any]:
        ledger = self._portfolio.venue_ledger
        if ledger is None:
            return {"venues": {}, "seeded_assets": []}
        return {
            "venues": {
                venue: {asset: str(amount) for asset, amount in assets.items()}
                for venue, assets in (ledger.export().get("balances") or {}).items()
            },
            "seeded_assets": list(ledger.export().get("seeded_assets") or []),
            "fx_refilled": len(getattr(self, "_fx_refilled", set())),
        }

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
            "markout": self._markout.snapshot() if hasattr(self, "_markout") else {},
            "inventory": self._inventory_snapshot(),
            "desk_scan": (
                (self._last_cycle or {}).get("scan") or {}
            ),
            "fee_tier": getattr(self._settings, "paper_fee_tier", "retail"),
            "live_forecast": self._live_forecast(snap),
            "global_engine": {
                "enabled": self._settings.global_opportunity_engine_enabled,
                "regime": (
                    self._opportunity_engine.global_regime.value
                    if hasattr(self._opportunity_engine, "global_regime")
                    else "normal"
                ),
                "active_sessions": [
                    c.value for c in self._market_calendar.active_asset_classes()
                ],
                "portfolio_exposure": self._opportunity_engine.portfolio_gate.snapshot(),
                "recent_decisions": self._decision_log.export()[-10:],
                "last_ranking": (self._last_cycle or {}).get("ranking"),
                "funding": self._market_data.funding.snapshot(),
                "funding_scan": self._funding_scan_stats(),
            },
        }

    def _funding_scan_stats(self) -> dict[str, object]:
        scan = (self._last_cycle or {}).get("scan") or {}
        children = scan.get("children") or {}
        if isinstance(children, dict):
            stats = children.get("funding_basis")
            if isinstance(stats, dict):
                return stats
        return {}

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
                "Same-venue MM alleen als spread fees + buffer cleart",
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
            self._record_markouts(fills, books)
            self._fx_refill_completed_triangles(books)
        self._update_markouts(books)
        await self._hybrid_hedge_if_needed(books)
        await self._cancel_stale_quotes()
        self._maybe_rebalance_venues()
        self._maybe_seed_usdt(books)
        self._apply_markout_adverse()


    def _mark_triangle_fx(self, opp_id: UUID, tracked: Any | None, fx_cost_eur: Decimal) -> None:
        """Always mark FX handled so triangle PnL can lock (even on skip/zero take)."""
        self._fx_refilled.add(opp_id)
        if tracked is None:
            return
        meta = dict(tracked.metadata or {})
        meta["fx_refilled"] = True
        meta["fx_refill_cost_eur"] = str(fx_cost_eur)
        tracked.metadata = meta

    def _finalize_triangle_pnl(
        self,
        tracked: Any | None,
        orders: list[Any],
        *,
        fx_cost_eur: Decimal,
    ) -> None:
        if tracked is None:
            return
        fills = [f for o in orders for f in (getattr(o, "fills", None) or [])]
        if not fills:
            return
        self._tracker.finalize_triangle_pnl(
            tracked, fills, fx_refill_cost_eur=fx_cost_eur
        )

    def _fx_refill_completed_triangles(self, books: dict[str, dict[str, Any]]) -> None:
        """After both triangle legs fill, convert leftover quote via EURUSDT taker.

        Always marks ``fx_refilled`` once both legs have fills so EUR PnL can lock,
        even when inventory is too thin to actually convert.
        """
        from bot.core.enums import OrderSide
        from bot.core.venue_fees import venue_taker_fee
        from bot.portfolio.models import AssetBalance

        ledger = self._portfolio.venue_ledger
        if ledger is None:
            return
        fx_symbol = str(getattr(self._settings, "paper_maker_fx_symbol", "EURUSDT") or "EURUSDT")
        manager = self._executor.order_manager
        by_opp: dict[UUID, list[Any]] = {}
        for order in manager.list_orders():
            if order.opportunity_id is None:
                continue
            if not (order.metadata or {}).get("triangle"):
                continue
            by_opp.setdefault(order.opportunity_id, []).append(order)

        for opp_id, orders in by_opp.items():
            if opp_id in self._fx_refilled:
                continue
            buy_orders = [o for o in orders if o.side == OrderSide.BUY and o.filled_quantity > 0]
            sell_orders = [o for o in orders if o.side == OrderSide.SELL and o.filled_quantity > 0]
            if not buy_orders or not sell_orders:
                continue
            tracked = self._tracker._by_id.get(opp_id)  # noqa: SLF001
            meta = dict((tracked.metadata if tracked else None) or (buy_orders[0].metadata or {}))
            direction = str(meta.get("direction") or "")
            fx_mid = Decimal(str(meta.get("fx_mid") or "0"))
            fx_bid = fx_ask = None
            for venue_books in books.values():
                book = venue_books.get(fx_symbol)
                if book is not None and book.bids and book.asks:
                    fx_bid = book.bids[0].price
                    fx_ask = book.asks[0].price
                    break
            if fx_bid is None or fx_ask is None:
                if fx_mid <= 0:
                    # No FX yet — retry next cycle; do not mark.
                    continue
                fx_bid = fx_ask = fx_mid

            buy = buy_orders[0]
            sell = sell_orders[0]
            matched = min(buy.filled_quantity, sell.filled_quantity)
            if matched <= 0:
                continue
            buy_venue = str((buy.metadata or {}).get("venue") or "")
            sell_venue = str((sell.metadata or {}).get("venue") or "")
            fx_cost_eur = Decimal("0")
            skipped = False

            if direction == "usdt_to_eur":
                # Spent USDT on buy venue; refill USDT from EUR (sell EURUSDT @ bid).
                usdt_spent = matched * (buy.average_fill_price or buy.requested_price or Decimal("0"))
                if usdt_spent <= 0 or not buy_venue:
                    skipped = True
                else:
                    eur_needed = usdt_spent / fx_bid
                    fee_rate = venue_taker_fee(buy_venue)
                    eur_needed *= Decimal("1") + fee_rate
                    take = min(eur_needed, ledger.available(buy_venue, "EUR"))
                    if take <= 0:
                        skipped = True
                    else:
                        usdt_got = (take / (Decimal("1") + fee_rate)) * fx_bid
                        fee_eur = take - (take / (Decimal("1") + fee_rate))
                        ledger._add(buy_venue, "EUR", -take)
                        ledger.credit(buy_venue, "USDT", usdt_got)
                        fx_cost_eur = fee_eur
                        eur_bal = self._portfolio.state.balances.setdefault(
                            "EUR",
                            AssetBalance(
                                asset="EUR", available=Decimal("0"), reserved=Decimal("0")
                            ),
                        )
                        usdt_bal = self._portfolio.state.balances.setdefault(
                            "USDT",
                            AssetBalance(
                                asset="USDT", available=Decimal("0"), reserved=Decimal("0")
                            ),
                        )
                        moved = min(take, eur_bal.available)
                        eur_bal.available -= moved
                        usdt_bal.available += usdt_got
            elif direction == "eur_to_usdt":
                # Received USDT on sell venue; convert to EUR (buy EURUSDT @ ask).
                usdt_got = matched * (
                    sell.average_fill_price or sell.requested_price or Decimal("0")
                )
                if usdt_got <= 0 or not sell_venue:
                    skipped = True
                else:
                    take = min(usdt_got, ledger.available(sell_venue, "USDT"))
                    if take <= 0:
                        skipped = True
                    else:
                        fee_rate = venue_taker_fee(sell_venue)
                        eur_got = (take / fx_ask) * (Decimal("1") - fee_rate)
                        fee_eur = (take / fx_ask) - eur_got
                        ledger._add(sell_venue, "USDT", -take)
                        ledger.credit(sell_venue, "EUR", eur_got)
                        fx_cost_eur = fee_eur
                        eur_bal = self._portfolio.state.balances.setdefault(
                            "EUR",
                            AssetBalance(
                                asset="EUR", available=Decimal("0"), reserved=Decimal("0")
                            ),
                        )
                        usdt_bal = self._portfolio.state.balances.setdefault(
                            "USDT",
                            AssetBalance(
                                asset="USDT", available=Decimal("0"), reserved=Decimal("0")
                            ),
                        )
                        moved = min(take, usdt_bal.available)
                        usdt_bal.available -= moved
                        eur_bal.available += eur_got
            else:
                skipped = True

            self._mark_triangle_fx(opp_id, tracked, fx_cost_eur)
            self._finalize_triangle_pnl(tracked, orders, fx_cost_eur=fx_cost_eur)
            logger.info(
                "TRIANGLE_FX_REFILL opp=%s direction=%s fx_cost_eur=%s skipped=%s",
                opp_id,
                direction,
                fx_cost_eur,
                skipped,
            )

    def _record_markouts(self, executions: list[Any], books: dict[str, dict[str, Any]]) -> None:
        if not getattr(self._settings, "paper_markout_enabled", True):
            return
        from bot.core.enums import OrderSide

        manager = self._executor.order_manager
        for execution in executions:
            if execution.filled_quantity <= 0:
                continue
            order = next(
                (o for o in manager.list_orders() if o.id == execution.order_id),
                None,
            )
            if order is None:
                continue
            venue = str((order.metadata or {}).get("venue") or "")
            book = (books.get(venue) or {}).get(order.symbol)
            mid = None
            if book is not None and book.bids and book.asks:
                mid = (book.bids[0].price + book.asks[0].price) / Decimal("2")
            side = "buy" if order.side == OrderSide.BUY else "sell"
            px = execution.average_price or order.average_fill_price or order.requested_price
            if px is None:
                continue
            self._markout.record_fill(
                fill_id=str(execution.order_id),
                opportunity_id=execution.opportunity_id,
                symbol=order.symbol,
                side=side,
                fill_price=Decimal(str(px)),
                mid=mid,
            )

    def _update_markouts(self, books: dict[str, dict[str, Any]]) -> None:
        if not getattr(self._settings, "paper_markout_enabled", True):
            return
        mids: dict[str, Decimal] = {}
        for venue_books in books.values():
            for symbol, book in venue_books.items():
                if book is None or not book.bids or not book.asks:
                    continue
                mid = (book.bids[0].price + book.asks[0].price) / Decimal("2")
                prev = mids.get(symbol)
                mids[symbol] = mid if prev is None else (prev + mid) / Decimal("2")
        self._markout.update(mids)

    def _apply_markout_adverse(self) -> None:
        if not getattr(self._settings, "paper_markout_enabled", True):
            return
        floor = Decimal(str(getattr(self._settings, "paper_markout_floor_bps", 2) or 2))
        ceiling = Decimal(str(getattr(self._settings, "paper_markout_ceiling_bps", 15) or 15))
        suggested = self._markout.suggested_adverse_bps(floor=floor, ceiling=ceiling)
        suggested_f = float(suggested)
        if self._last_markout_bps is not None and abs(self._last_markout_bps - suggested_f) < 0.05:
            return
        self._last_markout_bps = suggested_f
        try:
            self._settings.paper_maker_adverse_bps = suggested_f
        except Exception:
            object.__setattr__(self._settings, "paper_maker_adverse_bps", suggested_f)
        # Rebuild strategy + engine gates so markout is hard in approval path.
        if hasattr(self._strategy, "update_adverse_bps"):
            self._strategy.update_adverse_bps(suggested)  # type: ignore[attr-defined]
        gate = self._gate_settings()
        self._profitability = DefaultProfitabilityEngine(gate)
        self._risk = RiskEngine(gate, kill_switch=self._risk.kill_switch)
        self._opportunity_engine = self._build_opportunity_engine(gate)
        self._engine = TradingEngine(
            market_data=self._provider,
            strategy=self._strategy,
            profitability=self._profitability,
            risk=self._risk,
            portfolio=self._portfolio,
            executor=self._executor,
            opportunity_engine=(
                self._opportunity_engine
                if self._settings.global_opportunity_engine_enabled
                else None
            ),
        )
        logger.info("MARKOUT_GATE_REBUILT adverse_bps=%s", suggested)

    async def _hybrid_hedge_if_needed(self, books: dict[str, dict[str, Any]]) -> None:
        if not getattr(self._settings, "paper_hybrid_hedge", True):
            return
        from bot.core.enums import OrderSide

        threshold = Decimal(str(getattr(self._settings, "paper_hybrid_adverse_bps", 8) or 8))
        manager = self._executor.order_manager
        by_opp: dict[UUID, list[Any]] = {}
        for order in manager.list_orders():
            if order.opportunity_id is None or not (order.metadata or {}).get("post_only"):
                continue
            by_opp.setdefault(order.opportunity_id, []).append(order)
        for opp_id, orders in by_opp.items():
            open_legs = [o for o in orders if str(o.status.value) == "open"]
            filled_legs = [o for o in orders if o.filled_quantity > 0]
            if not open_legs or not filled_legs:
                continue
            for resting in open_legs:
                venue = str((resting.metadata or {}).get("venue") or "")
                book = (books.get(venue) or {}).get(resting.symbol)
                if book is None or resting.requested_price is None:
                    continue
                if resting.side == OrderSide.SELL:
                    if not book.bids:
                        continue
                    move = (
                        (resting.requested_price - book.bids[0].price)
                        / resting.requested_price
                        * Decimal("10000")
                    )
                else:
                    if not book.asks:
                        continue
                    move = (
                        (book.asks[0].price - resting.requested_price)
                        / resting.requested_price
                        * Decimal("10000")
                    )
                if move < threshold:
                    continue
                await self._executor.cancel(resting.id, reason="hybrid_hedge_adverse")
                await self._close_orphaned_maker_legs(opp_id)
                break

    def _maybe_rebalance_venues(self) -> None:
        if not getattr(self._settings, "paper_rebalance_enabled", True):
            return
        every = int(getattr(self._settings, "paper_rebalance_every_cycles", 120) or 120)
        if self._cycle_count <= 0 or self._cycle_count % every != 0:
            return
        ledger = self._portfolio.venue_ledger
        if ledger is None:
            return
        fee = Decimal(str(getattr(self._settings, "paper_rebalance_fee_bps", 5) or 5))
        moves = ledger.rebalance_quote(fee_bps=fee)
        if moves:
            logger.info("PAPER_VENUE_REBALANCE moves=%s", moves)

    def _maybe_seed_usdt(self, books: dict[str, dict[str, Any]]) -> None:
        pct = Decimal(str(getattr(self._settings, "paper_seed_usdt_pct", 0) or 0))
        if pct <= 0:
            return
        ledger = self._portfolio.venue_ledger
        if ledger is None or "USDT" in ledger.seeded_assets:
            return
        fx = str(getattr(self._settings, "paper_maker_fx_symbol", "EURUSDT") or "EURUSDT")
        fx_mid = None
        for venue_books in books.values():
            book = venue_books.get(fx)
            if book is not None and book.bids and book.asks:
                fx_mid = (book.bids[0].price + book.asks[0].price) / Decimal("2")
                break
        if fx_mid is None or fx_mid <= 0:
            return
        start_each = ledger.start_quote_each
        if start_each <= 0:
            return
        budget = start_each * (pct / Decimal("100"))
        moved = []
        for venue in ledger.venues:
            take = min(budget, ledger.available(venue, ledger.quote))
            if take <= 0:
                continue
            qty = take * fx_mid
            ledger._add(venue, ledger.quote, -take)
            ledger.credit(venue, "USDT", qty)
            moved.append((venue, qty, take))
        if not moved:
            return
        ledger.seeded_assets.add("USDT")
        total_cost = sum((c for _, _, c in moved), Decimal("0"))
        total_qty = sum((q for _, q, _ in moved), Decimal("0"))
        from bot.portfolio.models import AssetBalance
        eur = self._portfolio.state.balances.setdefault(
            "EUR", AssetBalance(asset="EUR", available=Decimal("0"), reserved=Decimal("0"))
        )
        take = min(total_cost, eur.available)
        eur.available -= take
        usdt = self._portfolio.state.balances.setdefault(
            "USDT", AssetBalance(asset="USDT", available=Decimal("0"), reserved=Decimal("0"))
        )
        usdt.available += total_qty
        self._portfolio.set_mark_price(fx, fx_mid)
        logger.info(
            "PAPER_USDT_SEEDED venues=%s usdt=%s eur_spent=%s fx=%s",
            len(moved), total_qty, total_cost, fx_mid,
        )

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
        grace_ms = float(getattr(self._settings, "paper_maker_sibling_grace_ms", 0) or 0)
        for order in list(self._executor.order_manager.open_orders()):
            if not (order.metadata or {}).get("post_only"):
                continue
            placed = float((order.metadata or {}).get("placed_ms") or now_ms)
            age = now_ms - placed
            limit = max_age
            if grace_ms > 0 and order.opportunity_id is not None:
                siblings = [
                    o
                    for o in self._executor.order_manager.list_orders()
                    if o.opportunity_id == order.opportunity_id and o.id != order.id
                ]
                if any(s.filled_quantity > 0 for s in siblings):
                    limit = max_age + grace_ms
            if age < limit:
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

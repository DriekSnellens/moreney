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
from bot.paper.capital_policy import (
    HoldingTimeController,
    portfolio_base_balances,
    portfolio_entry_prices,
)
from bot.core.venue_fees import set_fee_tier
from bot.portfolio.venue_ledger import infer_quote_asset
from bot.opportunity.engine import GlobalOpportunityEngine
from bot.opportunity.decision_log import OpportunityDecisionLogger
from bot.opportunity.calibration import EvCalibrator
from bot.opportunity.missed import MissedOpportunityTracker
from bot.opportunity.parameter_log import PARAMETER_CHANGES
from bot.perf.cycle_metrics import CycleLatencyTracker
from bot.opportunity.scanner import TieredScanScheduler
from bot.markets.registry import InstrumentRegistry
from bot.markets.calendar import MarketCalendarService
from bot.regime.detector import RegimeDetector
from bot.regime.market_regime import MarketRegimeDetector, RegimePrediction

logger = logging.getLogger(__name__)


def _verdict_tone(verdict: object) -> str:
    text = str(verdict or "").upper()
    if any(
        x in text
        for x in (
            "READY_FOR",
            "PROMISING",
            "KEEP TRADE-THROUGH",
            "READY",
        )
    ) and "NOT_READY" not in text and "PARTIALLY" not in text:
        return "ok"
    if any(
        x in text
        for x in (
            "NOT_READY",
            "INSUFFICIENT",
            "UNSUPPORTED",
            "REQUIRE BETTER",
            "ABANDON",
            "REJECT",
        )
    ):
        return "warn"
    if "PARTIAL" in text or "CAUTION" in text or "SHADOW" in text:
        return "warn"
    return "muted"


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
            extra_state = meta
        else:
            extra_state = {}
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
        equity_symbols = []
        if getattr(settings, "global_equity_enabled", False):
            equity_symbols = [
                part.strip().upper()
                for part in str(getattr(settings, "global_equity_symbols", "") or "").split(",")
                if part.strip()
            ]
        self._scan_universe = list(dict.fromkeys([*self._symbols, *equity_symbols]))
        self._markout = MarkoutTracker()
        self._calibrator = EvCalibrator(
            prior_strength=int(getattr(settings, "ev_calibration_prior_strength", 40) or 40),
            min_samples=int(getattr(settings, "ev_calibration_min_samples", 20) or 20),
        )
        self._missed = MissedOpportunityTracker()
        if extra_state.get("markout"):
            self._markout.import_state(extra_state.get("markout"))
        if extra_state.get("missed"):
            self._missed.import_state(extra_state.get("missed"))
        self._seed_calibrator()
        self._fx_refilled: set[UUID] = set()
        self._last_markout_bps: float | None = None
        self._holding = HoldingTimeController(
            max_holding_sec=float(
                getattr(settings, "paper_max_holding_sec", 7200.0) or 7200.0
            )
        )
        self._hmm_enabled = bool(getattr(settings, "paper_hmm_enabled", True))
        atr = int(
            getattr(settings, "paper_hmm_atr_window", None)
            or getattr(settings, "paper_hmm_vol_window", 14)
            or 14
        )
        self._hmm = MarketRegimeDetector(
            atr_window=atr,
            min_samples=int(getattr(settings, "paper_hmm_min_samples", 80) or 80),
            history_len=int(getattr(settings, "paper_hmm_history_len", 750) or 750),
            candle_timeframe_sec=float(
                getattr(settings, "paper_hmm_candle_sec", 300) or 300
            ),
            refit_every_sec=float(
                getattr(settings, "paper_hmm_refit_every_sec", 18000) or 18000
            ),
            toxic_confirm_steps=int(
                getattr(settings, "paper_hmm_toxic_confirm_steps", 2) or 2
            ),
            toxic_proba_threshold=float(
                getattr(settings, "paper_hmm_toxic_proba_threshold", 0.70) or 0.70
            ),
            normal_inventory_pct=float(
                getattr(settings, "paper_hmm_normal_inventory_pct", 0.30) or 0.30
            ),
            toxic_inventory_pct=float(
                getattr(settings, "paper_hmm_toxic_inventory_pct", 0.10) or 0.10
            ),
        )
        self._hmm_reduce_only = False
        self._hmm_last: RegimePrediction | None = None
        self._hmm_inventory_target = float(
            getattr(settings, "paper_hmm_normal_inventory_pct", 0.30) or 0.30
        )
        self._instrument_registry = InstrumentRegistry(settings)
        self._market_calendar = MarketCalendarService()
        self._regime = RegimeDetector()
        self._scan_scheduler = TieredScanScheduler(
            settings, self._instrument_registry, self._market_calendar
        )
        self._cycle_metrics = CycleLatencyTracker(
            enabled=bool(getattr(settings, "perf_instrumentation_enabled", False)),
            window=int(getattr(settings, "perf_instrumentation_window", 512) or 512),
        )
        self._opportunity_engine = self._build_opportunity_engine(gate)
        set_fee_tier(getattr(settings, "paper_fee_tier", "retail"))
        self._lead_lag_observer = None
        if getattr(settings, "lead_lag_enabled", True):
            from bot.opportunity.lead_lag.observer import LeadLagObserver

            self._lead_lag_observer = LeadLagObserver()
            # Hard safety: observation never executes
            self._lead_lag_observer.alters_execution = False
            if getattr(settings, "lead_lag_execution_enabled", False) and not getattr(
                settings, "lead_lag_shadow_only", True
            ):
                # Phase D remains off unless both flags intentionally flipped;
                # still do not auto-execute from observer.
                pass
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

    def _seed_calibrator(self) -> None:
        """Fit capture ratios from already-completed paper fills only."""
        for row in self._tracker.calibration_observations():
            self._calibrator.observe(
                key=str(row["key"]),
                route=str(row["route"]),
                strategy=str(row["strategy"]),
                expected_net=row["expected_net"],
                realized_net=row["realized_net"],
            )
        # Avoid double-counting rows already reflected above.
        if hasattr(self._tracker, "drain_calibration_observations"):
            self._tracker.drain_calibration_observations()

    def _build_opportunity_engine(self, gate: Settings) -> GlobalOpportunityEngine:
        self._calibrator = EvCalibrator(
            prior_strength=int(getattr(self._settings, "ev_calibration_prior_strength", 40) or 40),
            min_samples=int(getattr(self._settings, "ev_calibration_min_samples", 20) or 20),
            early_stop_samples=int(
                getattr(self._settings, "ev_calibration_early_stop_samples", 8) or 8
            ),
            early_stop_capture=Decimal(
                str(getattr(self._settings, "ev_calibration_early_stop_capture", "-0.25") or "-0.25")
            ),
            early_stop_min_loss_eur=Decimal(
                str(getattr(self._settings, "ev_calibration_early_stop_min_loss_eur", "5") or "5")
            ),
        )
        self._seed_calibrator()
        snap = self._markout.snapshot()
        samples = int(snap.get("samples", 0) or 0)
        win_rate = self._markout.empirical_win_rate(
            min_samples=int(getattr(self._settings, "paper_markout_min_samples", 20) or 20)
        )
        floor = Decimal(str(getattr(self._settings, "paper_markout_floor_bps", 2) or 2))
        ceiling = Decimal(str(getattr(self._settings, "paper_markout_ceiling_bps", 15) or 15))

        def _venue_adverse(venue: str, symbol: str, side: str) -> Decimal:
            # Decision-time belief: trade-through is the default fill regime when
            # queue fills are disabled — look up that bucket first.
            from bot.core.enums import FillType

            queue = float(getattr(self._settings, "paper_maker_queue_fill_pct", 0) or 0)
            fill_type = "" if queue > 0 else FillType.TRADE_THROUGH.value
            return self._markout.suggested_adverse_bps(
                floor=floor,
                ceiling=ceiling,
                venue=venue,
                symbol=symbol,
                side=side,
                fill_type=fill_type,
            )

        engine = GlobalOpportunityEngine(
            gate,
            profitability=self._profitability,
            risk=self._risk,
            registry=self._instrument_registry,
            calendar=self._market_calendar,
            regime=self._regime,
            decision_log=self._decision_log,
            markout_win_rate=win_rate,
            markout_samples=samples,
            calibrator=self._calibrator,
            missed=self._missed,
            venue_adverse_lookup=_venue_adverse,
        )
        engine.attach_latency_tracker(getattr(self, "_cycle_metrics", None))
        self._seed_toxicity(engine)
        return engine

    def _seed_toxicity(self, engine: GlobalOpportunityEngine) -> None:
        """Causal seed from past completed fills only (no rejects)."""
        if not hasattr(engine, "observe_toxicity"):
            return
        from bot.opportunity.toxicity.dataset import (
            adverse_bps_from_trade,
            estimate_notional_eur,
            features_from_trade,
        )

        for trade in list(getattr(self._tracker, "_trades", []) or []):
            feats = features_from_trade(trade)
            adv = adverse_bps_from_trade(trade, estimate_notional_eur(trade))
            engine.observe_toxicity(features=feats, adverse_bps=adv)

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
        adverse = float(getattr(settings, "paper_maker_adverse_bps", 0) or 0)
        gate_buf = getattr(settings, "paper_maker_gate_buffer_bps", None)
        exec_buffer = (
            float(gate_buf) + adverse
            if gate_buf is not None
            else 1.0 + adverse
        )
        return settings.model_copy(
            update={
                "profitability_min_net_profit_usd": settings.paper_maker_min_profit_eur,
                "profitability_min_net_return": float(
                    getattr(
                        settings,
                        "paper_maker_min_net_return",
                        settings.arbitrage_min_profit_pct,
                    )
                    or settings.arbitrage_min_profit_pct
                ),
                "profitability_apply_funding": False,
                "profitability_slippage_bps": 0.0,
                "profitability_thin_book_penalty_bps": 0.0,
                "profitability_execution_buffer_bps": exec_buffer,
                "risk_min_net_profit_usd": settings.paper_maker_min_profit_eur,
            }
        )

    def _configure_venue_inventory(self) -> None:
        if not self._settings.paper_venue_inventory:
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
        if self._portfolio.venue_ledger is not None:
            added = self._portfolio.venue_ledger.ensure_venues(venues)
            if added:
                logger.info("PAPER_VENUES_ADDED venues=%s", added)
            return
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
        await self._bootstrap_inventory()
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
        # Paper reset must clear the shared kill switch (stale drawdown from a
        # prior session would otherwise block all new quotes forever).
        if hasattr(self._risk, "kill_switch"):
            await self._risk.kill_switch.recover(force=True)
        if self._settings.paper_maker_enabled:
            self._risk = RiskEngine(gate, kill_switch=self._risk.kill_switch)
        self._decision_log = OpportunityDecisionLogger()
        self._markout = MarkoutTracker()
        self._calibrator = EvCalibrator(
            prior_strength=int(getattr(self._settings, "ev_calibration_prior_strength", 40) or 40),
            min_samples=int(getattr(self._settings, "ev_calibration_min_samples", 20) or 20),
        )
        self._missed = MissedOpportunityTracker()
        self._last_markout_bps = None
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
        self._last_cycle = None
        self._cycle_count = 0
        self._errors = []
        self._fx_refilled = set()
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
        metrics = self._cycle_metrics
        if hasattr(self, "_engine") and hasattr(self._engine, "attach_latency_tracker"):
            self._engine.attach_latency_tracker(metrics)
        cycle_t0 = time.perf_counter()
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

        # One immutable-for-this-cycle book snapshot shared by HMM + match/expire.
        with metrics.span("collect_books"):
            books = self._collect_books()

        # Lead-lag Phase A: observation only (no orders, no ranking effect).
        with metrics.span("lead_lag_observe"):
            self._lead_lag_observe(books)

        # HMM guardrail: toxic dump regime → cancel bids + REDUCE_ONLY before quoting.
        with metrics.span("hmm_regime"):
            await self._apply_hmm_regime_guardrail(books=books)

        with metrics.span("match_expire"):
            await self._match_and_expire_quotes(books=books)

        equity_before = self._portfolio.state.total_equity
        scan_symbols = self._scan_scheduler.symbols_for_cycle(all_symbols=self._scan_universe)
        with metrics.span("strategy_scan"):
            result = await self._engine.run_universe(scan_symbols)
        with metrics.span("ingest_cycle"):
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
        metrics.record("total_cycle", time.perf_counter() - cycle_t0)
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
            "hmm_regime": self._hmm.snapshot() if self._hmm_enabled else None,
            "reduce_only": self._hmm_reduce_only,
            "inventory_target_pct": self._hmm_inventory_target,
            "latency": metrics.report() if metrics.enabled else None,
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
                self._observe_calibration()
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
            markout=self._markout.export_state(),
            calibration=self._calibrator.export_state(),
            missed=self._missed.export_state(),
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
            "hmm_regime": self._hmm.snapshot() if self._hmm_enabled else {"enabled": False},
            "desk_scan": (
                (self._last_cycle or {}).get("scan") or {}
            ),
            "fee_tier": getattr(self._settings, "paper_fee_tier", "retail"),
            "live_forecast": self._live_forecast(snap),
            "net_kpis": self._net_kpis(snap),
            "edge_decomposition": self._edge_decomposition(),
            "cost_ownership": self._cost_ownership_snapshot(),
            "why_not_trade": self._missed.why_not_trade() if hasattr(self, "_missed") else {},
            "ev_calibration": self._calibrator.snapshot() if hasattr(self, "_calibrator") else {},
            "toxicity_shadow": (
                self._opportunity_engine.shadow_snapshot()
                if hasattr(self, "_opportunity_engine")
                and hasattr(self._opportunity_engine, "shadow_snapshot")
                else {"enabled": False, "alters_execution": False}
            ),
            "fill_model_lab": self._fill_model_lab_snapshot(),
            "lead_lag_lab": self._lead_lag_lab_snapshot(),
            "market_data_lab": self._market_data_lab_snapshot(),
            "research_findings": self._research_findings_snapshot(),
            "research_tournament": self._research_tournament_snapshot(),
            "concentration_forensics": self._concentration_forensics_snapshot(),
            "regime_hypothesis_lab": self._regime_hypothesis_lab_snapshot(),
            "autonomous_research": self._autonomous_research_snapshot(),
            "parameter_changes": PARAMETER_CHANGES,
            "latency": self._cycle_metrics.report(),
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
                "equity": self._market_data.equity.snapshot(),
                "funding_scan": self._funding_scan_stats(),
            },
        }

    def _lead_lag_lab_snapshot(self) -> dict[str, Any]:
        """Research-only lead-lag panel — never merges into live-equivalent PnL."""
        from pathlib import Path

        base: dict[str, Any] = {
            "enabled": bool(getattr(self._settings, "lead_lag_enabled", True)),
            "shadow_only": bool(getattr(self._settings, "lead_lag_shadow_only", True)),
            "execution_enabled": bool(
                getattr(self._settings, "lead_lag_execution_enabled", False)
            ),
            "alters_execution": False,
            "affects_production_pnl": False,
            "label": "RESEARCH_ONLY",
            "verdict": "INSUFFICIENT_DATA",
            "headline": "Onvoldoende data voor causale lead/lag",
            "finding": "Geen gesynchroniseerde book-tape — geen alpha-claim.",
            "data_quality": "UNSUPPORTED",
            "panel": [],
            "observer": (
                self._lead_lag_observer.snapshot()
                if getattr(self, "_lead_lag_observer", None) is not None
                else {"enabled": False, "n_observations": 0}
            ),
            "source": None,
        }
        report_path = Path("data/lead_lag_report.json")
        if report_path.exists():
            try:
                import json

                report = json.loads(report_path.read_text(encoding="utf-8"))
                base.update(
                    {
                        "verdict": report.get("O_final_verdict"),
                        "data_quality": (report.get("C_timestamp_audit") or {}).get(
                            "overall_quality"
                        ),
                        "panel": report.get("lead_lag_lab_panel") or [],
                        "source": str(report_path),
                        "headline": {
                            "INSUFFICIENT_DATA": "Onvoldoende data voor causale lead/lag",
                            "NO_STABLE_PREDICTIVE_RELATIONSHIP": "Geen stabiele predictieve relatie",
                            "PREDICTIVE_BUT_NOT_EXECUTABLE": "Predictief maar niet executable",
                            "EXECUTABLE_IN_SAMPLE_ONLY": "Alleen in-sample executable",
                            "PROMISING_OOS_RESEARCH_SIGNAL": "Veelbelovend OOS research-signaal",
                        }.get(
                            str(report.get("O_final_verdict")),
                            str(report.get("O_final_verdict")),
                        ),
                        "finding": (
                            ((report.get("C_timestamp_audit") or {}).get("reason"))
                            or ((report.get("G_trade_through_toxicity_selector") or {}).get("detail"))
                            or "Zie lead-lag rapport"
                        ),
                    }
                )
            except Exception:
                pass
        if base["shadow_only"] or not base["execution_enabled"]:
            base["execution_enabled"] = False
            base["alters_execution"] = False
        return base

    def _market_data_lab_snapshot(self) -> dict[str, Any]:
        """MARKET DATA LAB — research infrastructure only."""
        from pathlib import Path

        from bot.market_data.research.venue_audit import venue_capability_report

        recorder = (
            self._market_data.research_recorder_status()
            if hasattr(self._market_data, "research_recorder_status")
            else {"enabled": False}
        )
        venues = venue_capability_report(("binance", "bitvavo", "okx"))
        venue_rows = []
        for name, cap in (venues.get("venues") or {}).items():
            venue_rows.append(
                {
                    "venue": name,
                    "exchange_ts": (
                        "Ja" if cap.get("exchange_timestamp_available") else "Nee"
                    ),
                    "quality": cap.get("timestamp_quality"),
                    "sequence": "Ja" if cap.get("sequence_available") else "Nee",
                    "note": cap.get("notes") or "",
                    "events": 0,
                    "exchange_ts_coverage": 0,
                    "receive_ts_coverage": 0,
                    "sequence_coverage": 0,
                    "p50_ms": None,
                    "p95_ms": None,
                    "p99_ms": None,
                    "quality_grade": cap.get("timestamp_quality"),
                }
            )

        base: dict[str, Any] = {
            "label": "RESEARCH_INFRASTRUCTURE",
            "affects_trading": False,
            "recorder": recorder,
            "verdict": "NO_REAL_TAPE",
            "headline": "Nog geen research-tape — lead/lag is niet klaar",
            "findings": [
                "Geen gesynchroniseerde market-data opname op schijf.",
                "Bitvavo levert geen exchange-timestamp (UNSUPPORTED).",
                "Recorder staat klaar op de publisher (vóór Redis).",
            ],
            "next_step": (
                "Start moreney-marketdata met RESEARCH_MARKETDATA_RECORDING_ENABLED=true, "
                "verzamel uren tape, run: python -m bot.market_data.research.runner"
            ),
            "horizon_scores": {
                f"LEAD_LAG_{h}MS": "NOT_READY"
                for h in (50, 100, 250, 500, 1000, 2000, 5000)
            },
            "horizon_rows": [
                {"horizon": f"{h} ms", "status": "NOT_READY"}
                for h in (50, 100, 250, 500, 1000, 2000, 5000)
            ],
            "panel": venue_rows,
            "sync": {},
            "event_count": 0,
            "source": None,
            "research_data_status": {
                "CURRENT_STATE": "NO_REAL_TAPE",
                "RECORDER_ENABLED": bool(recorder.get("enabled")),
                "RECORDER_RUNNING": bool(recorder.get("RECORDER_RUNNING")),
                "EVENTS_WRITTEN": recorder.get("written") or recorder.get("EVENTS_WRITTEN") or 0,
                "EVENTS_DROPPED": recorder.get("dropped") or 0,
                "WRITE_ERRORS": recorder.get("write_errors") or 0,
                "QUEUE_DEPTH": recorder.get("queue_depth") or 0,
                "LAST_WRITE": recorder.get("last_write_ns"),
                "ACTIVE_DATASET": None,
                "DATASET_EVENT_COUNT": 0,
                "DATASET_DURATION": None,
                "VENUES": [],
                "TIMESTAMP_COVERAGE": {},
                "FINAL_ACCEPTANCE_VERDICT": "NO_REAL_TAPE",
            },
        }
        report_path = Path("data/market_data_research_report.json")
        if report_path.exists():
            try:
                import json

                report = json.loads(report_path.read_text(encoding="utf-8"))
                verdict = report.get("final_verdict") or "DATA_NOT_READY"
                scores = (report.get("J_horizon_readiness") or {}).get(
                    "horizon_scores"
                ) or base["horizon_scores"]
                panel = report.get("market_data_lab_panel") or venue_rows
                # Merge capability notes onto panel rows
                by_name = {r["venue"]: r for r in venue_rows}
                for row in panel:
                    v = str(row.get("venue") or "")
                    if v in by_name:
                        row.setdefault("exchange_ts", by_name[v].get("exchange_ts"))
                        row.setdefault("note", by_name[v].get("note"))
                        row.setdefault("quality", by_name[v].get("quality"))
                findings = [
                    f"Verdict: {verdict}",
                    str((report.get("A_problem") or "")[:220]),
                    str((report.get("C_venue_capabilities") or {}).get("critical_finding") or "")[
                        :220
                    ],
                ]
                findings = [f for f in findings if f and f != "Verdict: "]
                headline = {
                    "DATA_READY_FOR_FAST_HORIZONS": "Tape bruikbaar voor snelle horizons",
                    "DATA_READY_FOR_SLOW_HORIZONS": "Tape bruikbaar voor trage research-horizons",
                    "DATA_READY_FOR_LEAD_LAG": "Data klaar voor lead/lag research",
                    "DATA_PARTIALLY_READY": "Data deels klaar — niet alle horizons",
                    "DATA_NOT_READY": "Tape aanwezig maar niet acceptabel",
                    "NO_REAL_TAPE": "Nog geen echte research-tape",
                    "RECORDER_DISABLED": "Recorder uitgeschakeld",
                    "RECORDER_BROKEN": "Recorder defect (schrijffouten)",
                }.get(str(verdict), str(verdict))
                rds = {
                    "CURRENT_STATE": report.get("operational_state")
                    or report.get("RECORDER_STATUS")
                    or verdict,
                    "RECORDER_ENABLED": bool(recorder.get("enabled")),
                    "RECORDER_RUNNING": bool(
                        recorder.get("RECORDER_RUNNING", recorder.get("enabled"))
                    ),
                    "EVENTS_WRITTEN": recorder.get("written")
                    or recorder.get("EVENTS_WRITTEN")
                    or 0,
                    "EVENTS_DROPPED": report.get("RECORDER_DROPS")
                    or recorder.get("dropped")
                    or 0,
                    "WRITE_ERRORS": report.get("WRITE_ERRORS")
                    or recorder.get("write_errors")
                    or 0,
                    "QUEUE_DEPTH": recorder.get("queue_depth") or 0,
                    "LAST_WRITE": recorder.get("last_write_ns"),
                    "ACTIVE_DATASET": report.get("DATASET_ID"),
                    "DATASET_EVENT_COUNT": report.get("event_count") or 0,
                    "DATASET_DURATION": report.get("DURATION"),
                    "VENUES": report.get("VENUES") or [],
                    "TIMESTAMP_COVERAGE": report.get("TIMESTAMP_COVERAGE") or {},
                    "FINAL_ACCEPTANCE_VERDICT": verdict,
                }
                base.update(
                    {
                        "verdict": verdict,
                        "headline": headline,
                        "findings": findings
                        or base["findings"],
                        "next_step": report.get("O_next_step_for_lead_lag")
                        or report.get("NEXT_ACTION")
                        or base["next_step"],
                        "horizon_scores": scores,
                        "horizon_rows": [
                            {
                                "horizon": k.replace("LEAD_LAG_", "").replace("MS", " ms"),
                                "status": v,
                            }
                            for k, v in scores.items()
                        ],
                        "panel": panel,
                        "sync": report.get("H_synchronization") or {},
                        "event_count": report.get("event_count") or 0,
                        "source": str(report_path),
                        "supported_horizons": report.get("supported_horizons") or [],
                        "unsupported_horizons": report.get("unsupported_horizons") or [],
                        "research_data_status": rds,
                        "dataset_id": report.get("DATASET_ID"),
                    }
                )
            except Exception:
                pass
        return base

    def _research_findings_snapshot(self) -> dict[str, Any]:
        """One-glance research board for the dashboard (not production PnL)."""
        md = self._market_data_lab_snapshot()
        ll = self._lead_lag_lab_snapshot()
        fill = self._fill_model_lab_snapshot()
        tox = (
            self._opportunity_engine.shadow_snapshot()
            if hasattr(self, "_opportunity_engine")
            and hasattr(self._opportunity_engine, "shadow_snapshot")
            else {}
        )
        cards = [
            {
                "id": "market_data",
                "title": "Market data",
                "verdict": md.get("verdict") or "DATA_NOT_READY",
                "headline": md.get("headline"),
                "tone": _verdict_tone(md.get("verdict")),
                "detail": (md.get("findings") or [None])[0],
            },
            {
                "id": "lead_lag",
                "title": "Lead-lag",
                "verdict": ll.get("verdict") or "INSUFFICIENT_DATA",
                "headline": ll.get("headline")
                or "Onvoldoende data voor causale lead/lag",
                "tone": _verdict_tone(ll.get("verdict")),
                "detail": ll.get("finding"),
            },
            {
                "id": "fill_lab",
                "title": "Fill model",
                "verdict": fill.get("recommendation")
                or fill.get("success_letter")
                or "REQUIRE BETTER DATA",
                "headline": fill.get("headline")
                or "Trade-through baseline behouden",
                "tone": _verdict_tone(fill.get("recommendation") or fill.get("success_letter")),
                "detail": ((fill.get("toxicity_selector") or {}).get("answer")),
            },
            {
                "id": "toxicity",
                "title": "Toxicity",
                "verdict": "SHADOW_ONLY",
                "headline": "Niet predictive voor live blocking",
                "tone": "warn",
                "detail": "Shadow only — wijzigt geen fills",
            },
        ]
        return {
            "title": "Research findings",
            "subtitle": "Geen productie-PnL — alleen onderzoeksconclusies",
            "cards": cards,
            "next_step": md.get("next_step"),
            "production_pnl_untouched": True,
        }

    def _research_tournament_snapshot(self) -> dict[str, Any]:
        """RESEARCH TOURNAMENT — separated from Live-equivalent / paper MTM."""
        from pathlib import Path

        base: dict[str, Any] = {
            "label": "STRATEGY_RESEARCH_TOURNAMENT",
            "affects_trading": False,
            "execution_enabled": False,
            "CURRENT_DATASET": None,
            "DATA_READINESS": {},
            "DEV_WINDOW": None,
            "FREEZE_BOUNDARY": None,
            "OOS_WINDOW": None,
            "scoreboard": [],
            "PAPER_CANDIDATES": [],
            "ALL_STRATEGIES_REJECTED": True,
            "headline": "Nog geen tournament-run — python -m bot.research.tournament.runner",
            "disclaimer": "RESEARCH CANDIDATE — NOT PROVEN LIVE PROFITABLE",
        }
        path = Path("data/research_tournament_report.json")
        if not path.exists():
            return base
        try:
            import json

            report = json.loads(path.read_text(encoding="utf-8"))
            paper = report.get("PAPER_CANDIDATES") or []
            rejected = bool(report.get("ALL_STRATEGIES_REJECTED", not paper))
            headline = (
                f"{len(paper)} PAPER_CANDIDATE(s) — not proven live profitable"
                if paper
                else "ALL STRATEGIES REJECTED — valid research result"
            )
            base.update(
                {
                    "CURRENT_DATASET": report.get("DATASET_ID"),
                    "DATA_READINESS": report.get("DATA_READINESS") or {},
                    "DEV_WINDOW": report.get("DEVELOPMENT_WINDOW"),
                    "FREEZE_BOUNDARY": report.get("FREEZE_BOUNDARY"),
                    "OOS_WINDOW": report.get("OOS_WINDOW"),
                    "scoreboard": report.get("scoreboard") or [],
                    "candidates": report.get("candidates") or {},
                    "PAPER_CANDIDATES": paper,
                    "ALL_STRATEGIES_REJECTED": rejected,
                    "headline": headline,
                    "STATUS": report.get("STATUS"),
                    "PERFORMANCE": report.get("PERFORMANCE"),
                    "source": str(path),
                }
            )
        except Exception:
            pass
        return base

    def _concentration_forensics_snapshot(self) -> dict[str, Any]:
        """CONCENTRATION FORENSICS — descriptive; does not alter trading."""
        from pathlib import Path

        base: dict[str, Any] = {
            "label": "CONCENTRATION_FORENSICS",
            "affects_trading": False,
            "execution_enabled": False,
            "DATASET": None,
            "rows": [],
            "NEW_HYPOTHESES_CREATED": [],
            "LLM_USED": "NO",
            "PRODUCTION_TRADING_CHANGED": False,
            "headline": "Nog geen forensics-run — python -m bot.research.forensics.runner",
            "disclaimer": "Descriptive forensics. Parents remain REJECTED. Not alpha.",
        }
        path = Path("data/concentration_forensics_report.json")
        if not path.exists():
            return base
        try:
            import json

            report = json.loads(path.read_text(encoding="utf-8"))
            base.update(
                {
                    "DATASET": report.get("DATASET"),
                    "rows": report.get("rows") or [],
                    "NEW_HYPOTHESES_CREATED": report.get("NEW_HYPOTHESES_CREATED") or [],
                    "LLM_USED": report.get("LLM_USED"),
                    "NEXT_RESEARCH_ACTION": report.get("NEXT_RESEARCH_ACTION"),
                    "headline": (
                        "Concentration forensics on STABILITY-rejected cost-positive families"
                    ),
                    "STATUS": report.get("STATUS"),
                    "source": str(path),
                }
            )
        except Exception:
            pass
        return base

    def _regime_hypothesis_lab_snapshot(self) -> dict[str, Any]:
        """REGIME HYPOTHESIS LAB — independent H-0005/H-0007; not production."""
        from pathlib import Path

        base: dict[str, Any] = {
            "label": "REGIME_HYPOTHESIS_LAB",
            "affects_trading": False,
            "execution_enabled": False,
            "rows": [],
            "LLM_USED": "NO",
            "PRODUCTION_EXECUTION": "DISABLED",
            "headline": "Nog geen regime-lab — python -m bot.research.regime_lab.runner",
            "disclaimer": (
                "OBSERVED / DEV / OOS / HYPOTHESIS are separate. "
                "Forensic NET is not strategy profitability."
            ),
        }
        path = Path("data/regime_hypothesis_lab_report.json")
        if not path.exists():
            return base
        try:
            import json

            report = json.loads(path.read_text(encoding="utf-8"))
            base.update(
                {
                    "rows": report.get("rows") or [],
                    "DATA_STATUS": report.get("DATA_STATUS"),
                    "LLM_USED": report.get("LLM_USED"),
                    "NEW_HYPOTHESES": report.get("NEW_HYPOTHESES") or [],
                    "NEXT_ACTION": report.get("NEXT_ACTION"),
                    "CONTROL_RESULTS": report.get("CONTROL_RESULTS"),
                    "headline": "Independent H-0005 / H-0007 — parents remain REJECTED",
                    "STATUS": report.get("STATUS"),
                    "source": str(path),
                }
            )
        except Exception:
            pass
        return base

    def _autonomous_research_snapshot(self) -> dict[str, Any]:
        """AUTONOMOUS RESEARCH — local LLM scientist; tournament remains the judge."""
        from pathlib import Path

        settings = self._settings
        base: dict[str, Any] = {
            "label": "AUTONOMOUS_LOCAL_LLM_RESEARCH",
            "affects_trading": False,
            "research_only": True,
            "Provider": "ollama",
            "Model": getattr(settings, "research_llm_model", "qwen3:4b-instruct"),
            "Connection": "UNKNOWN",
            "Autonomous_mode": bool(
                getattr(settings, "research_llm_autonomous_enabled", False)
            ),
            "LLM_STATUS": "UNKNOWN",
            "CURRENT_RESEARCH_ROUND": {},
            "HYPOTHESIS_PIPELINE": [],
            "WHAT_THE_LLM_LEARNED": {
                "label": "NON_AUTHORITATIVE_ANALYSIS",
                "items": [],
            },
            "multiple_testing_exposure": {},
            "disclaimer": (
                "Canonical verdicts come from the deterministic tournament only."
            ),
        }
        path = Path("data/autonomous_research_report.json")
        if path.exists():
            try:
                import json

                report = json.loads(path.read_text(encoding="utf-8"))
                base.update(
                    {
                        "LLM_STATUS": report.get("LLM_STATUS"),
                        "Connection": (
                            "AVAILABLE"
                            if report.get("LLM_STATUS") == "AVAILABLE"
                            else "UNAVAILABLE"
                        ),
                        "Model": report.get("model") or base["Model"],
                        "Autonomous_mode": report.get("autonomous_mode"),
                        "CURRENT_RESEARCH_ROUND": {
                            "Dataset": ((report.get("tournament") or {}).get("DATASET_ID")),
                            "Hypotheses_proposed": report.get("hypotheses_proposed"),
                            "Rejected_as_duplicate": report.get("rejected_duplicate"),
                            "Rejected_by_validator": report.get("rejected_validator"),
                            "Accepted_for_experiment": report.get("accepted_for_experiment"),
                            "Experiments_completed": report.get("experiments_completed"),
                        },
                        "WHAT_THE_LLM_LEARNED": {
                            "label": "NON_AUTHORITATIVE_ANALYSIS",
                            "items": (report.get("llm_analysis") or {}).get("items")
                            or report.get("llm_analysis"),
                            "shared_lessons": (report.get("llm_analysis") or {}).get(
                                "shared_lessons"
                            ),
                        },
                        "multiple_testing_exposure": report.get("multiple_testing_exposure")
                        or {},
                        "proposal": report.get("proposal"),
                        "source": str(path),
                    }
                )
                board = ((report.get("tournament") or {}).get("scoreboard") or [])
                base["HYPOTHESIS_PIPELINE"] = [
                    {
                        "strategy": r.get("STRATEGY"),
                        "verdict": r.get("VERDICT"),
                        "gate": r.get("FAILED_GATE"),
                    }
                    for r in board
                ]
            except Exception:
                pass
        else:
            # Live health probe is optional / cached lightly — avoid hot path cost
            base["Connection"] = "UNCHECKED"
            base["LLM_STATUS"] = "UNCHECKED"
        return base

    def _lead_lag_observe(self, books: dict[str, dict[str, Any]]) -> None:
        """Phase A: record cross-venue TOB pairs. Never places orders."""
        observer = getattr(self, "_lead_lag_observer", None)
        if observer is None or not getattr(self._settings, "lead_lag_enabled", True):
            return
        from bot.opportunity.lead_lag.pairs import directed_pairs
        from bot.opportunity.lead_lag.timestamps import VENUE_EVENT_CLOCK

        now_ms = time.time() * 1000.0
        if len(observer.observations) > 8000:
            observer.observations = observer.observations[-4000:]
        venues = tuple(
            p.strip().lower()
            for p in self._settings.market_data_exchanges.split(",")
            if p.strip()
        )
        for symbol in self._symbols:
            for lead, foll in directed_pairs(venues or None):
                lb = (books.get(lead) or {}).get(symbol)
                fb = (books.get(foll) or {}).get(symbol)
                if lb is None or fb is None:
                    continue
                if not lb.bids or not lb.asks or not fb.bids or not fb.asks:
                    continue
                l_clock = VENUE_EVENT_CLOCK.get(lead, {})
                try:
                    event_ms = lb.timestamp.timestamp() * 1000.0
                except Exception:
                    event_ms = now_ms
                l_depth = sum((lvl.amount for lvl in lb.bids[:5]), Decimal("0")) + sum(
                    (lvl.amount for lvl in lb.asks[:5]), Decimal("0")
                )
                f_depth = sum((lvl.amount for lvl in fb.bids[:5]), Decimal("0")) + sum(
                    (lvl.amount for lvl in fb.asks[:5]), Decimal("0")
                )
                observer.observe_pair(
                    timestamp_ms=event_ms,
                    local_received_ms=now_ms,
                    symbol=symbol,
                    leader_venue=lead,
                    follower_venue=foll,
                    leader_bid=lb.bids[0].price,
                    leader_ask=lb.asks[0].price,
                    follower_bid=fb.bids[0].price,
                    follower_ask=fb.asks[0].price,
                    leader_book_age_ms=float(getattr(lb, "age_ms", 0) or 0),
                    follower_book_age_ms=float(getattr(fb, "age_ms", 0) or 0),
                    leader_depth=l_depth,
                    follower_depth=f_depth,
                    data_quality=str(l_clock.get("quality") or "UNSUPPORTED"),
                    event_ts_source=str(l_clock.get("event_ts") or "unknown"),
                )

    def _fill_model_lab_snapshot(self) -> dict[str, Any]:
        """Experimental fill-model lab panel — never alters production PnL/execution."""
        from pathlib import Path

        report_path = Path("data/fill_mechanism_report.json")
        if report_path.exists():
            try:
                import json

                report = json.loads(report_path.read_text(encoding="utf-8"))
                return {
                    "production_pnl_source": report.get(
                        "production_pnl_source", "TRADE_THROUGH_ONLY"
                    ),
                    "alters_execution": False,
                    "success_letter": report.get("success_letter")
                    or (report.get("H_production_recommendation") or {}).get(
                        "success_criterion"
                    ),
                    "recommendation": report.get("recommendation")
                    or (report.get("H_production_recommendation") or {}).get("primary"),
                    "headline": "Trade-through baseline behouden — betere data nodig",
                    "panel": report.get("fill_model_lab_panel") or [],
                    "toxicity_selector": report.get("G_trade_through_toxicity_selector"),
                    "source": str(report_path),
                }
            except Exception:
                pass
        return {
            "production_pnl_source": "TRADE_THROUGH_ONLY",
            "alters_execution": False,
            "success_letter": "C",
            "recommendation": "REQUIRE BETTER DATA",
            "headline": "Trade-through baseline behouden — betere data nodig",
            "panel": [
                {
                    "model": "TRADE_THROUGH_ONLY",
                    "status": "CONSERVATIVE_BASELINE",
                    "support": "SUPPORTED",
                    "sample_count": None,
                    "notes": ["Production headline uses trade-through only."],
                }
            ],
            "toxicity_selector": {
                "answer": "INSUFFICIENT_DATA",
                "detail": "Run fill_lab study to refresh panel.",
            },
            "source": None,
        }

    def _funding_scan_stats(self) -> dict[str, object]:
        scan = (self._last_cycle or {}).get("scan") or {}
        children = scan.get("children") or {}
        if isinstance(children, dict):
            stats = children.get("funding_basis")
            if isinstance(stats, dict):
                return stats
        return {}

    def _net_kpis(self, snap: Any) -> dict[str, Any]:
        runtime = max(self.runtime_seconds(), 1.0)
        capital = snap.starting_equity or Decimal("1")
        net = snap.net_pnl
        trades = int(snap.trade_count or 0)
        volume = snap.trading_volume or Decimal("0")
        velocity = (
            (net / capital / Decimal(str(runtime))) if capital > 0 else Decimal("0")
        )
        missed_cost = Decimal("0")
        if hasattr(self, "_missed"):
            for row in self._missed.gate_table():
                missed_cost += Decimal(str(row.get("estimated_missed_profit_eur") or 0))
        markout = self._markout.snapshot() if hasattr(self, "_markout") else {}
        return {
            "net_eur_per_fill": str(snap.net_eur_per_fill),
            "net_bps_per_fill": str(snap.net_bps_per_fill),
            "ev_capture": str(snap.ev_capture) if snap.ev_capture is not None else None,
            "fees_per_fill": str(snap.fees_per_fill),
            "slippage_per_fill": str(snap.slippage_per_fill),
            "capital_velocity": str(velocity),
            "rejection_opportunity_cost": str(missed_cost),
            "markout_1s": markout.get("avg_adverse_bps_1s"),
            "markout_5s": markout.get("avg_adverse_bps_5s"),
            "markout_30s": markout.get("avg_adverse_bps_30s"),
            "markout_60s": markout.get("avg_adverse_bps_60s"),
            "trade_count": trades,
            "volume": str(volume),
        }

    def _edge_decomposition(self) -> dict[str, Any]:
        from bot.opportunity.edge_decomposition import edge_decomposition

        trades = self._tracker.trades(limit=500) if hasattr(self._tracker, "trades") else []
        return edge_decomposition(list(trades or []))

    def _cost_ownership_snapshot(self) -> list[dict[str, Any]]:
        from bot.opportunity.cost_ownership import ownership_table

        return ownership_table()

    def _live_forecast(self, snap: Any) -> dict[str, Any]:
        """Sized maker quotes: NET euro per fill, capital recycled quickly."""
        from bot.paper.capacity import project_daily_pnl
        from bot.paper.odds import snapshot as odds_snapshot

        runtime = max(self.runtime_seconds(), 1.0)
        net = snap.net_pnl
        start = snap.starting_equity
        inv_pct = Decimal(str(getattr(self._settings, "paper_seed_inventory_pct", 75) or 75))
        odds = odds_snapshot(start, inventory_pct=inv_pct)
        band_eur = Decimal(str(odds["euro_on_capital"]))
        band_pct = Decimal(str(odds["equity_move_pct"]))
        paper_day = (
            project_daily_pnl(net, runtime)
            if snap.trade_count >= 2 and runtime >= 120
            else Decimal("0")
        )
        mtm = getattr(snap, "paper_equity_pnl", None)
        if mtm is None:
            mtm = getattr(snap, "current_equity", start) - start
        return {
            "label": "Trading-alpha (sized maker, retail fees)",
            "realized_live_eur": _dec_str(net),
            "projected_per_hour_eur": _dec_str(band_eur / Decimal("24")),
            "projected_per_day_eur": _dec_str(band_eur),
            "projected_day_return_pct": _dec_str(band_pct),
            "paper_run_rate_per_day_eur": _dec_str(paper_day),
            "paper_equity_pnl": _dec_str(Decimal(str(mtm or 0))),
            "projection_ready": True,
            "confidence": "low",
            "note": str(odds["note"]),
            "runtime_seconds": runtime,
            "vol_capture": odds,
            "assumptions": [
                "Groei = NET euro per fill × hoe snel kapitaal weer vrij is",
                "Quote-cash eerst; ~30% alts alleen om te kunnen verkopen",
                "Weinig, grotere quotes; stofjes onder de euro-vloer gaan eruit",
                "Retail fees, geen queue-fills, fair-value blijft aan",
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

    async def _bootstrap_inventory(self) -> None:
        """Pre-seed allowlisted base assets + USDT float so maker quotes are not venue-capped."""
        if not self._settings.paper_venue_inventory:
            return
        if self._portfolio.venue_ledger is None:
            return
        symbols = [
            part.strip().upper().replace("-", "").replace("/", "")
            for part in str(getattr(self._settings, "paper_seed_symbols", "") or "").split(",")
            if part.strip()
        ]
        if not symbols:
            symbols = [
                s.strip().upper().replace("-", "").replace("/", "")
                for s in self._settings.market_data_symbols.split(",")
                if s.strip().upper().endswith(self._settings.paper_quote_asset.upper())
            ]
        for symbol in symbols:
            snaps = self._market_data.snapshots_for_arbitrage(symbol)
            price = None
            for snap in snaps:
                if snap.bid > 0 and snap.ask > 0:
                    price = (snap.bid + snap.ask) / Decimal("2")
                    break
            if price is not None:
                self._portfolio.maybe_seed_inventory(symbol, price)
        books = self._collect_books()
        self._maybe_seed_usdt(books)

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

    async def _match_and_expire_quotes(
        self, books: dict[str, dict[str, Any]] | None = None
    ) -> None:
        books = books if books is not None else self._collect_books()
        fills = self._executor.match_resting(books)
        if fills:
            self._ingest_delayed_fills(fills)
            self._record_markouts(fills, books)
            self._fx_refill_completed_triangles(books)
        self._update_markouts(books)
        await self._hybrid_hedge_if_needed(books)
        await self._cancel_buys_on_dump()
        await self._cancel_stale_quotes()
        await self._recycle_overdue_inventory(books)
        self._maybe_rebalance_venues()
        self._maybe_seed_usdt(books)
        self._apply_markout_adverse()

    async def _apply_hmm_regime_guardrail(
        self, books: dict[str, dict[str, Any]] | None = None
    ) -> None:
        """Observe mids → 5m candles; slow refit; toxic → bids off + REDUCE_ONLY."""
        maker = self._maker_strategy()
        if not self._hmm_enabled:
            self._hmm_reduce_only = False
            self._hmm_inventory_target = float(
                getattr(self._settings, "paper_hmm_normal_inventory_pct", 0.30) or 0.30
            )
            if maker is not None:
                maker.set_hmm_regime(None, is_toxic=False)
                maker.enable_mode("NORMAL")
                maker.set_inventory_target_pct(self._hmm_inventory_target)
            return

        books = books if books is not None else self._collect_books()
        for venue_books in books.values():
            for symbol, book in venue_books.items():
                bids = getattr(book, "bids", None) or []
                asks = getattr(book, "asks", None) or []
                if not bids or not asks:
                    continue
                mid = float((bids[0].price + asks[0].price) / 2)
                self._hmm.observe_mid(symbol, mid)

        # Refit on wall-clock cadence (≈4–6h), never every tick.
        pred = self._hmm.update_and_predict(symbols=self._symbols, refit=None)
        self._hmm_last = pred
        toxic = bool(pred is not None and pred.is_toxic_flow)
        self._hmm_reduce_only = toxic
        target = (
            float(pred.inventory_target_pct)
            if pred is not None
            else float(
                getattr(self._settings, "paper_hmm_normal_inventory_pct", 0.30) or 0.30
            )
        )
        self._hmm_inventory_target = target

        if maker is not None:
            maker.set_hmm_regime(
                pred.regime_id if pred is not None else None,
                is_toxic=toxic,
            )
            maker.set_inventory_target_pct(target)
            maker.enable_mode("REDUCE_ONLY" if toxic else "NORMAL")

        if toxic:
            logger.warning(
                "[HMM GUARDRAIL] Toxic flow gedetecteerd "
                "(label=%s confidence=%.1f%% toxic_p=%.1f%%). "
                "Bids geblokkeerd; inventory target=%.0f%%; REDUCE_ONLY.",
                pred.label if pred else "TOXIC_DUMP",
                (pred.confidence * 100.0) if pred else 0.0,
                (pred.toxic_probability * 100.0) if pred else 0.0,
                target * 100.0,
            )
            await self._cancel_all_bids(reason="hmm_toxic_flow")

    async def _cancel_all_bids(self, *, reason: str) -> None:
        from bot.core.enums import OrderSide

        for order in list(self._executor.order_manager.open_orders()):
            if order.side != OrderSide.BUY:
                continue
            if not (order.metadata or {}).get("post_only"):
                continue
            await self._executor.cancel(order.id, reason=reason)

    def _maker_strategy(self) -> MakerInventoryStrategy | None:
        strategy = self._strategy
        if isinstance(strategy, MakerInventoryStrategy):
            return strategy
        if isinstance(strategy, CompositeDeskStrategy):
            maker = getattr(strategy, "_maker", None)
            return maker if isinstance(maker, MakerInventoryStrategy) else None
        if isinstance(strategy, GlobalCompositeStrategy):
            for child in getattr(strategy, "_children", []) or []:
                if isinstance(child, MakerInventoryStrategy):
                    return child
        return None

    async def _cancel_buys_on_dump(self) -> None:
        """Pull resting BUY quotes when dump guard, inventory overweight, or HMM toxic."""
        from bot.core.enums import OrderSide

        maker = self._maker_strategy()
        if maker is None and not self._hmm_reduce_only:
            return
        dump = {s.upper() for s in maker.dump_symbols()} if maker is not None else set()
        skew = maker.active_skew if maker is not None else None
        cancel_all_buys = bool(
            self._hmm_reduce_only
            or (skew is not None and skew.sell_only)
            or (maker is not None and maker.reduce_only)
        )
        if not dump and not cancel_all_buys:
            return
        for order in list(self._executor.order_manager.open_orders()):
            if order.side != OrderSide.BUY:
                continue
            if not (order.metadata or {}).get("post_only"):
                continue
            if not cancel_all_buys and order.symbol.upper() not in dump:
                continue
            if self._hmm_reduce_only:
                reason = "hmm_toxic_flow"
            elif skew is not None and skew.sell_only:
                reason = "inventory_overweight_cancel_buys"
            else:
                reason = "vol_dump_cancel_buys"
            await self._executor.cancel(order.id, reason=reason)

    async def _recycle_overdue_inventory(
        self, books: dict[str, dict[str, Any]]
    ) -> None:
        """Break-even / flat ALT→EUR recycle after max holding time."""
        from bot.core.enums import OrderSide
        from uuid import uuid4

        state = self._portfolio.state
        balances = portfolio_base_balances(state)
        self._holding.note_balances(balances)
        overdue = self._holding.overdue(
            balances,
            mark_prices=state.mark_prices,
            entry_prices=portfolio_entry_prices(state),
            quote=state.quote_asset,
        )
        if not overdue:
            return
        ledger = self._portfolio.venue_ledger
        quote = (state.quote_asset or "EUR").upper()
        exits: list[Any] = []
        for base, qty in overdue:
            if qty <= 0:
                continue
            # Gradual: free ~25% of the tranche each cycle (capital velocity).
            portion = min(qty, qty * Decimal("0.25"))
            if portion <= 0:
                continue
            symbol = f"{base}{quote}"
            venue = ""
            available_qty = portion
            if ledger is not None:
                best_venue = ""
                best_qty = Decimal("0")
                for vname in ledger.venues:
                    have = ledger.available(vname, base)
                    if have > best_qty:
                        best_qty = have
                        best_venue = vname
                venue = best_venue
                available_qty = min(portion, best_qty) if best_qty > 0 else Decimal("0")
            if available_qty <= 0:
                continue
            book = (books.get(venue) or {}).get(symbol) if venue else None
            if book is None:
                for vname, venue_books in books.items():
                    if symbol in venue_books:
                        book = venue_books[symbol]
                        venue = venue or vname
                        break
            if book is None:
                continue
            result = await self._executor.close_one_leg(
                opportunity_id=uuid4(),
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=available_qty,
                venue=venue,
                order_book=book,
                reason="holding_time_recycle",
            )
            if result is not None:
                exits.append(result)
                logger.info(
                    "holding_time_recycle base=%s qty=%s venue=%s symbol=%s",
                    base,
                    available_qty,
                    venue,
                    symbol,
                )
        if exits:
            self._ingest_delayed_fills(exits)


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
                venue=venue,
                strategy=str((order.metadata or {}).get("strategy") or ""),
                fill_type=str(
                    (execution.metadata or {}).get("fill_type")
                    or (order.metadata or {}).get("last_fill_type")
                    or ""
                ),
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
        self._missed.update_mids(mids)

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
            self._observe_calibration()
        self._tracker.sync_portfolio(self._portfolio)
        self._store.save_portfolio(self._portfolio)

    def _observe_calibration(self) -> None:
        """Update route beliefs immediately on each completed round-trip."""
        if not hasattr(self, "_calibrator") or not hasattr(self._tracker, "drain_calibration_observations"):
            return
        rows = self._tracker.drain_calibration_observations()
        for row in rows:
            self._calibrator.observe(
                key=str(row["key"]),
                route=str(row["route"]),
                strategy=str(row["strategy"]),
                expected_net=row["expected_net"],
                realized_net=row["realized_net"],
            )
        # Toxicity model learns from completed fills only (shadow — no live gate).
        engine = getattr(self, "_opportunity_engine", None)
        if engine is None or not hasattr(engine, "observe_toxicity"):
            return
        from bot.opportunity.toxicity.dataset import (
            adverse_bps_from_trade,
            features_from_trade,
            estimate_notional_eur,
        )

        trades = list(getattr(self._tracker, "_trades", []) or [])
        by_id = {str(t.get("opportunity_id")): t for t in trades}
        for row in rows:
            trade = by_id.get(str(row.get("opportunity_id") or ""))
            if not trade:
                continue
            feats = features_from_trade(trade)
            notional = estimate_notional_eur(trade)
            adv_bps = adverse_bps_from_trade(trade, notional)
            engine.observe_toxicity(features=feats, adverse_bps=adv_bps)

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

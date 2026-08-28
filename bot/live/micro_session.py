"""Full-bot live micro session: € capital pocket on Bitvavo.

Uses the shared TradingEngine host (historically PaperRunner) with research
hooks disabled, and routes Bitvavo fills through MicroBudgetLiveExecutor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.core.disk_guard import disk_guard_status
from bot.core.enums import ExecutionMode
from bot.engine.orchestrator import TradingEngine
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor
from bot.live.micro_engine import LiveMicroEngine, reset_micro_engine
from bot.market_data.service import MarketDataService
from bot.paper.runner import PaperRunner
from bot.paper.store import PaperTradingStore
from bot.funding.multi_venue import parse_venue_list
from bot.portfolio.venue_ledger import infer_base_asset
from bot.risk.risk_engine import RiskEngine

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")

# Top liquid Bitvavo/OKX EUR alts — market-first scan (not portfolio-driven).
_LIQUID_EUR_SYMBOLS = (
    "ETHEUR",
    "SOLEUR",
    "XRPEUR",
    "ADAEUR",
    "DOGEUR",
    "LINKEUR",
    "DOTEUR",
    "AVAXEUR",
    "LTCEUR",
    "NEAREUR",
    "ATOMEUR",
    "ARBEUR",
    "OPEUR",
    "INJEUR",
    "SUIEUR",
    "APTEUR",
    "FETEUR",
    "POLEUR",
)

_DEFAULT_BUDGET_EUR = Decimal("2000")


def _non_btc_symbols(settings: Settings) -> list[str]:
    raw = [
        s.strip().upper().replace("-", "").replace("/", "")
        for s in settings.market_data_symbols.split(",")
        if s.strip()
    ]
    return [s for s in raw if not s.startswith("BTC")]


def _liquid_symbols(settings: Settings, *, exclude_btc: bool = True) -> list[str]:
    raw = str(getattr(settings, "live_micro_symbols", "") or "").strip()
    if raw and raw != "*":
        out = [
            s.strip().upper().replace("-", "").replace("/", "")
            for s in raw.split(",")
            if s.strip()
        ]
    else:
        out = list(_LIQUID_EUR_SYMBOLS)
    if exclude_btc:
        out = [s for s in out if not s.upper().startswith("BTC")]
    return out


def _parse_execute_venues(settings: Settings) -> set[str]:
    raw = str(getattr(settings, "live_micro_execute_venues", "bitvavo") or "bitvavo")
    return {v for v in parse_venue_list(raw) if v}


def _cross_venue_market_symbols(eur_symbols: list[str]) -> list[str]:
    """EUR pairs for Bitvavo + USDT/EURUSDT legs for OKX fair-value bridge."""
    out: list[str] = []
    seen: set[str] = set()
    for sym in eur_symbols:
        s = sym.strip().upper().replace("/", "").replace("-", "")
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if s.endswith("EUR") and len(s) > 3:
            usdt = f"{s[:-3]}USDT"
            if usdt not in seen:
                seen.add(usdt)
                out.append(usdt)
    if "EURUSDT" not in seen:
        out.append("EURUSDT")
    return out


def _session_settings(
    base: Settings,
    *,
    budget_eur: Decimal,
    symbols: list[str],
    persist_path: Path,
) -> Settings:
    """Paper mode + micro unlocks already in env; € pocket capital, live arb path."""
    mode = getattr(base, "market_data_mode", "local") or "local"
    # Direct WebSockets for live — do not depend on shared Redis publisher.
    mode = "local"
    budget_f = float(budget_eur)
    cross_venue = bool(getattr(base, "live_micro_cross_venue_enabled", True))
    execute_venues = _parse_execute_venues(base)
    md_symbols = _cross_venue_market_symbols(symbols) if cross_venue else list(symbols)
    maker_venues = "okx,bitvavo" if cross_venue else "bitvavo"
    return base.model_copy(
        update={
            # Internal engine host only — env keeps PAPER_TRADING_ENABLED=false so
            # the API never exposes paper lab UI or auto-start on :8020.
            "execution_mode": ExecutionMode.PAPER,
            "paper_trading_enabled": True,
            "paper_auto_start": False,
            "paper_starting_eur": budget_f,
            "paper_persist_path": str(persist_path),
            # Venue ledger on Bitvavo so maker sizes sell legs to real inventory.
            "paper_venue_inventory": True,
            "paper_seed_inventory_pct": 0.0,
            "paper_seed_max_assets": 0,
            "paper_seed_usdt_pct": 0.0,
            # Live Bitvavo maker quotes inside the € pocket.
            "paper_maker_enabled": True,
            "paper_triangle_enabled": False,
            "paper_maker_venues": maker_venues,
            # Independent same-venue quotes on each exchange alongside cross-venue arb.
            "paper_maker_same_venue": True,
            # One quote per venue per cycle — avoids stacked duplicate resting bids.
            "paper_maker_max_open_quotes": 2 if cross_venue else 1,
            # Fewer concurrent sprays → larger / better trails.
            "arbitrage_max_emits_per_cycle": 4 if cross_venue else 2,
            "paper_cycle_interval_ms": 1200.0,
            # Larger clips: soft-partial of a real bag must clear Bitvavo fees.
            # First entry matches min notional; adds after soft-arm scale up.
            "paper_maker_min_notional_eur": min(70.0, max(55.0, budget_f * 0.03)),
            "live_micro_first_clip_eur": min(70.0, max(55.0, budget_f * 0.03)),
            "live_micro_add_clip_eur": 120.0,
            # More fills while still fee-positive after maker costs.
            "paper_maker_min_profit_eur": 0.05,
            "paper_maker_min_net_return": 0.0005,
            "paper_maker_small_clip_max_eur": 80.0,
            "paper_maker_small_clip_min_profit_eur": 0.03,
            "paper_maker_small_clip_min_net_return": 0.0003,
            "paper_maker_min_spread_bps": 5.0,
            "paper_maker_adverse_bps": 2.0,
            "paper_maker_spread_fee_buffer_bps": 1.0,
            "paper_maker_allow_buy_only": True,
            # Still fee-aware never-loss; thinner buffer = asks clear sooner.
            "paper_maker_sell_profit_buffer_bps": 10.0,
            # Soft harvest earlier / larger partials → more €/day recycles.
            "paper_trail_take_profit_enabled": True,
            # Trail all synced inventory (incl. pre-session ATOM/NEAR bags).
            "paper_trail_session_buys_only": False,
            # Arm at net profit (BE); sell full bag on 0.2% peak retrace.
            "paper_trail_soft_arm_pct": 0.001,
            "paper_trail_soft_drawdown_pct": 0.002,
            "paper_trail_soft_partial_pct": 0.0,
            # Lock part of BE recoveries (Aug-25 +€129 MTM was not harvested).
            "paper_trail_recovery_be_partial_pct": 0.35,
            "paper_trail_be_harvest_partial_pct": 0.35,
            "paper_trail_be_harvest_min_gain_pct": 0.0005,
            "live_micro_be_harvest_cooldown_sec": 15.0,
            "paper_trail_hard_arm_pct": 0.06,
            "paper_trail_hard_drawdown_pct": 0.03,
            "paper_trail_hard_partial_pct": 0.25,
            "paper_trail_arm_gain_pct": 0.06,
            "paper_trail_drawdown_pct": 0.03,
            "paper_trail_partial_enabled": True,
            "paper_trail_partial_pct": 0.40,
            "paper_trail_atr_enabled": False,  # keep fixed harvest levels
            "paper_trail_atr_samples": 48,
            "paper_trail_atr_arm_mult": 2.5,
            "paper_trail_atr_dd_mult": 1.0,
            # Single touch bid — ladder stacked duplicate SOL resting orders.
            "paper_ladder_buy_enabled": False,
            "paper_ladder_buy_pcts": "0,0.0015,0.004",
            "paper_time_stop_enabled": True,
            "paper_time_stop_sec": 3600.0,  # after 1h, recovery-arm at BE (no flat dump)
            "paper_time_stop_min_profit_bps": 25.0,  # diagnostics / floor helper only
            "paper_dust_policy": "top_up_or_exit",
            "paper_dust_exit_slack_bps": 0.0,  # never sell below fee-aware break-even
            "paper_regime_block_buys": True,
            # New-base entries only on rising mark momentum (fits never-loss trail).
            "paper_buy_momentum_enabled": True,
            "paper_buy_momentum_min_return": 0.001,  # ≥+0.10% over rolling samples
            "paper_buy_momentum_samples": 12,
            # Concentrate: correlated spray dilutes €/trail on €2k pockets.
            "live_micro_corr_group": "ETH,SOL,XRP,ADA,LINK,AVAX,ARB,OP,DOT,NEAR",
            "live_micro_max_per_corr_group": 2,
            "paper_daily_kill_eur": 50.0,
            "paper_alert_pct_to_arm": 0.006,
            "paper_hmm_enabled": False,  # unfitted HMM was noise on live
            "paper_maker_one_leg_exit": False,
            "paper_maker_one_leg_adverse_bps": 6.0,
            "paper_maker_max_age_ms": 180_000.0,
            "paper_maker_sibling_grace_ms": 20_000.0,
            "paper_max_holding_sec": 0.0,
            # Prefer cash when bags pile up (skew → sell-only sooner).
            "paper_max_alt_inventory_pct": 35.0,
            "paper_min_alt_inventory_pct": 15.0,
            "paper_inventory_ask_improve_bps": 2.0,
            # Underweight venues buy sooner (OKX cash deployment).
            # Prefer cash / rising entries — do not force underweight dip buys.
            "paper_inventory_buy_dip_bps": 0.0,
            "paper_maker_keep_vs_best_frac": 0.60,  # only near-best NET emits
            "live_micro_underwater_buy_block": 1,
            "live_micro_underwater_block_new_bases_only": True,
            "live_micro_block_underwater_adds": True,
            # Anti-stack uses duplicate-resting + clip floor; allow other bases while SOL held.
            "live_micro_block_buys_when_holding_base": False,
            "live_micro_primary_execute_venue": "bitvavo",
            "live_micro_okx_buy_improve_bps": 1.0,
            "live_micro_underwater_min_notional_eur": 25.0,
            "live_micro_cut_loss_below_be_pct": 0.04,
            "live_micro_cut_loss_new_bases_only": False,
            "live_micro_early_cut_loss_below_be_pct": 0.01,
            "live_micro_momentum_exit_min_return": 0.002,
            "live_micro_momentum_exit_above_be_pct": 0.005,
            "global_max_strategy_exposure_pct": 100.0,
            "global_max_venue_exposure_pct": 100.0,
            "live_micro_cross_venue_min_fill_rate": 0.30,
            "live_micro_cross_venue_min_attempts": 8,
            "live_micro_block_cross_venue_duplicate_bases": True,
            "live_micro_consolidate_duplicate_bases": True,
            "live_micro_consolidate_primary_venue": "bitvavo",
            "live_micro_okx_deploy_bases": str(
                getattr(
                    base,
                    "live_micro_okx_deploy_bases",
                    "ADA,NEAR,DOT,XRP,LINK,ATOM",
                )
                or "ADA,NEAR,DOT,XRP,LINK,ATOM"
            ),
            "live_micro_okx_cash_bias_ratio": float(
                getattr(base, "live_micro_okx_cash_bias_ratio", 1.5) or 1.5
            ),
            "live_micro_trail_partial_min_frac": float(
                getattr(base, "live_micro_trail_partial_min_frac", 0.45) or 0.45
            ),
            "paper_markout_enabled": False,
            "paper_maker_fair_value": True,
            # Live-only: no research CVD/shadow/lead-lag on hot path.
            "live_disable_research_hooks": True,
            "live_allow_without_research_unlock": True,
            "research_marketdata_recording_enabled": False,
            "market_data_recording_enabled": False,
            "lead_lag_enabled": False,
            "toxicity_shadow_enabled": False,
            "global_funding_strategy_enabled": False,
            # Multi-venue: OKX deploys spare EUR; Bitvavo keeps primary rotation slot.
            "global_max_venue_exposure_pct": 50.0 if cross_venue else 100.0,
            "live_micro_execute_venues": ",".join(sorted(execute_venues)),
            "live_micro_cross_venue_enabled": cross_venue,
            "arbitrage_min_profit_eur": 0.08,
            "arbitrage_min_profit_pct": 0.0010,
            "profitability_min_net_profit_usd": 0.05,
            "profitability_min_net_return": 0.0005,
            "profitability_execution_buffer_bps": 2.0,
            "risk_min_net_profit_usd": 0.05,
            # Hard per-trade ceiling: ≤8% of pocket, never above €150 on ~€2k.
            "risk_max_position_usd": min(150.0, max(50.0, budget_f * 0.08)),
            # Size vs aggregate multi-venue equity so clips stay near the €150
            # ceiling (2×€2k pockets must not inflate to ~€260 and fail NET return).
            "arbitrage_position_pct": min(
                6.5,
                max(
                    3.0,
                    (
                        min(150.0, max(50.0, budget_f * 0.08))
                        / max(budget_f * max(len(execute_venues), 1), 1.0)
                    )
                    * 100.0,
                ),
            ),
            "live_micro_ignore_paper_daily_loss": True,
            # Soft daily stop: 10% of pocket (total risk budget remains the pocket).
            "risk_max_daily_loss_usd": max(50.0, budget_f * 0.10),
            # Single-venue Bitvavo live — multi-venue exposure caps would block all size.
            "global_max_venue_exposure_pct": 100.0,
            # Fewer concurrent bases → larger trails → more realized €/day.
            "risk_max_open_positions": 3,
            "max_simultaneous_positions": 3,
            "opportunity_max_executions_per_cycle": 6,
            "opportunity_max_candidates_per_cycle": 12,
            "live_micro_venues": ",".join(sorted(execute_venues)) or "bitvavo",
            "live_micro_symbols": ",".join(symbols)
            if symbols
            else ",".join(_LIQUID_EUR_SYMBOLS),
            "live_micro_max_alt_bases": 2,
            # Cap live order size (env must not silently allow full pocket).
            # Per-venue: each exchange gets its own open-order budget (OKX ≠ Bitvavo).
            "live_micro_max_notional_eur": min(150.0, max(50.0, budget_f * 0.08)),
            "live_micro_max_daily_loss_eur": max(50.0, budget_f * 0.10),
            # Alt-beta book: wider drawdown band than default 5–8% global kill.
            "max_drawdown_percent": float(
                getattr(base, "live_micro_max_drawdown_percent", 12.0) or 12.0
            ),
            "live_micro_reset_drawdown_on_start": bool(
                getattr(base, "live_micro_reset_drawdown_on_start", True)
            ),
            "live_micro_max_open_orders": 2 if cross_venue else 1,
            "live_micro_max_open_orders_per_venue": 1,
            "live_micro_resting_max_age_sec": 480.0,
            "market_data_mode": mode,
            "market_data_symbols": ",".join(md_symbols) if md_symbols else base.market_data_symbols,
            "market_data_exchanges": "binance,kraken,coinbase,bitvavo,okx,bybit"
            if cross_venue
            else base.market_data_exchanges,
        }
    )


def attach_micro_bridge(
    runner: PaperRunner,
    *,
    live_engine: LiveMicroEngine,
    budget_eur: Decimal,
    exclude_bases: set[str] | None = None,
    allowed_bases: set[str] | None = None,
) -> MicroBudgetLiveExecutor:
    """Replace PaperRunner executor with budget-capped live bridge; rebuild engine."""
    settings = runner._settings  # noqa: SLF001
    execute_venues = _parse_execute_venues(settings)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=runner.portfolio,
        live_engine=live_engine,
        budget_eur=budget_eur,
        execute_venues=execute_venues,
        exclude_bases=exclude_bases or {"BTC"},
        allowed_bases=allowed_bases,
        live_maker=True,
    )
    runner._executor = bridge  # noqa: SLF001
    runner._engine = TradingEngine(  # noqa: SLF001
        market_data=runner._provider,  # noqa: SLF001
        strategy=runner._strategy,  # noqa: SLF001
        profitability=runner._profitability,  # noqa: SLF001
        risk=runner._risk,  # noqa: SLF001
        portfolio=runner.portfolio,
        executor=bridge,
        opportunity_engine=(
            runner._opportunity_engine  # noqa: SLF001
            if runner._settings.global_opportunity_engine_enabled  # noqa: SLF001
            else None
        ),
    )
    return bridge


async def run_session(
    *,
    minutes: float | None = None,
    budget_eur: Decimal = _DEFAULT_BUDGET_EUR,
    symbols: list[str] | None = None,
    settings: Settings | None = None,
    report_path: str | Path | None = None,
    exclude_btc: bool = True,
    market_data: MarketDataService | None = None,
    own_market_data: bool | None = None,
    status_callback: Any | None = None,
    should_stop: Any | None = None,
    kill_switch: Any | None = None,
    bridge_holder: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run full PaperRunner cycles with live Bitvavo fills inside a € capital pocket.

    ``minutes=None`` (or <=0) runs continuously until stop is requested.
    ``budget_eur`` is total trading capital, not a per-trade size.
    """
    base = settings or get_settings()
    disk = disk_guard_status(
        "/",
        warn_pct=float(base.disk_guard_warn_pct),
        block_pct=float(base.disk_guard_block_pct),
    )
    if disk.get("blocked"):
        return {
            "ok": False,
            "reason": "disk_full",
            "detail": disk,
            "mode": "full_bot_micro",
            "trades": [],
        }
    if exclude_btc:
        scan_symbols = symbols or _liquid_symbols(base, exclude_btc=True)
        scan_symbols = [s for s in scan_symbols if not s.upper().startswith("BTC")]
    else:
        scan_symbols = symbols or _liquid_symbols(base, exclude_btc=False)
    if not scan_symbols:
        raise ValueError("No symbols configured for micro session")

    persist = Path("./data/live_micro_fullbot_state.json")
    if persist.exists():
        persist.unlink()

    cfg = _session_settings(
        base, budget_eur=budget_eur, symbols=scan_symbols, persist_path=persist
    )
    reset_micro_engine()
    live = LiveMicroEngine(cfg)
    arm = live.arm()
    if not arm.get("armed"):
        return {
            "ok": False,
            "reason": "arm_failed",
            "detail": arm,
            "mode": "full_bot_micro",
            "trades": [],
        }

    owns_md = own_market_data if own_market_data is not None else market_data is None
    if market_data is None:
        from bot.core.redis_client import get_redis
        from bot.market_data.cache import MarketDataCache

        cache = None
        if cfg.market_data_mode in {"shared", "publisher"}:
            cache = MarketDataCache(
                redis_client=get_redis(cfg.redis_url),
                ttl_seconds=cfg.market_data_redis_ttl_seconds,
            )
        md = MarketDataService(
            cfg,
            cache=cache,
            start_websockets=cfg.market_data_mode == "local",
        )
    else:
        md = market_data
    risk = RiskEngine(cfg, kill_switch=kill_switch)
    store = PaperTradingStore(cfg)
    runner = PaperRunner(cfg, market_data=md, risk_engine=risk, store=store)
    allowed_bases = {
        infer_base_asset(sym)
        for sym in scan_symbols
        if sym and not str(sym).upper().startswith("BTC")
    }
    bridge = attach_micro_bridge(
        runner,
        live_engine=live,
        budget_eur=budget_eur,
        exclude_bases={"BTC"} if exclude_btc else set(),
        allowed_bases=allowed_bases,
    )
    if bridge_holder is not None:
        bridge_holder["bridge"] = bridge
    execute_venues = sorted(_parse_execute_venues(cfg))
    if runner.portfolio.venue_ledger is None:
        runner.portfolio.init_venue_ledger(execute_venues, starting_quote=_ZERO)
    else:
        runner.portfolio.venue_ledger.ensure_venues(execute_venues)

    started = await runner.start()
    if not started.get("started"):
        return {
            "ok": False,
            "reason": "paper_runner_start_failed",
            "detail": started,
            "mode": "full_bot_micro",
            "trades": [],
        }
    # After runner builds the Bitvavo venue ledger, mirror live balances so the
    # maker strategy can size sell legs against real inventory (no forced dumps).
    try:
        sync = await bridge.reconcile_from_exchange("bitvavo")
        logger.info("Full-bot micro initial sync bitvavo: %s", sync)
        if "okx" in _parse_execute_venues(cfg):
            okx_sync = await bridge.reconcile_from_exchange("okx")
            logger.info("Full-bot micro initial sync okx: %s", okx_sync)
    except Exception:  # noqa: BLE001
        logger.exception("Full-bot micro initial sync failed")
    try:
        for prune_venue in sorted(_parse_execute_venues(cfg)):
            pruned = await bridge._prune_resting_buys(prune_venue)  # noqa: SLF001
            if pruned:
                logger.info(
                    "Full-bot micro pruned duplicate/held resting buys venue=%s n=%s",
                    prune_venue,
                    pruned,
                )
    except Exception:  # noqa: BLE001
        logger.exception("Full-bot micro initial resting prune failed")

    # Inventory sync is not trading — clear phantom paper PnL and resume if a
    # false paper daily-loss pause was inherited from a prior fill mirror.
    try:
        bridge.reset_paper_realized_after_inventory_sync()
    except Exception:  # noqa: BLE001
        logger.exception("paper realized reset failed")
    if bool(getattr(cfg, "live_micro_reset_drawdown_on_start", True)):
        try:
            peak = bridge.reset_session_risk_baseline(tracker=runner.tracker)
            logger.info("micro session drawdown baseline reset peak=%s", peak)
        except Exception:  # noqa: BLE001
            logger.exception("micro session drawdown baseline reset failed")
    try:
        await bridge.refresh_portfolio_value()
        bridge.mark_session_baseline()
        logger.info(
            "micro session baseline portfolio=%s realized=%s",
            bridge.starting_portfolio_eur,
            bridge.session_start_realized_eur,
        )
    except Exception:  # noqa: BLE001
        logger.exception("micro session baseline mark failed")
    bridge._kill_switch = kill_switch  # noqa: SLF001 — diagnostics only
    if kill_switch is not None:
        try:
            recovered = await kill_switch.recover(force=True)
            logger.info("micro session kill-switch recover force=%s", recovered)
        except Exception:  # noqa: BLE001
            logger.exception("micro session kill-switch recover failed")

    continuous = minutes is None or float(minutes) <= 0
    deadline = (
        None if continuous else time.monotonic() + float(minutes) * 60.0
    )
    started_mono = time.monotonic()
    logger.info(
        "Full-bot micro session start budget=%s symbols=%s minutes=%s continuous=%s mode=%s",
        budget_eur,
        len(scan_symbols),
        minutes,
        continuous,
        cfg.market_data_mode,
    )
    def _tick() -> None:
        if status_callback is None:
            return
        st = runner.status()
        elapsed = time.monotonic() - started_mono
        remaining = (
            None
            if deadline is None
            else round(max(0.0, deadline - time.monotonic()), 1)
        )
        # Live-only KPIs: never publish paper-pocket equity (ghost MTM / false +PnL).
        status_callback(
            {
                "continuous": continuous,
                "minutes": minutes,
                "symbol_count": len(scan_symbols),
                "symbols_sample": scan_symbols[:12],
                "elapsed_seconds": round(elapsed, 1),
                "remaining_seconds": remaining,
                "strategy_cycles": st.get("cycle_count"),
                "strategy": st.get("strategy"),
                "approved_opportunities": st.get("approved_opportunities"),
                "executed_opportunities": st.get("executed_opportunities"),
                "netto_winst_eur": str(bridge.realized_trade_pnl_eur),
                "realized_trade_pnl_eur": str(bridge.realized_trade_pnl_eur),
                "portfolio_value_eur": (
                    str(bridge.portfolio_value_eur)
                    if bridge.portfolio_value_eur is not None
                    else None
                ),
                "starting_portfolio_eur": (
                    str(bridge.starting_portfolio_eur)
                    if bridge.starting_portfolio_eur is not None
                    else None
                ),
                "bridge": bridge.snapshot_bridge(),
                "live_trades_attempted": len(bridge.live_trades),
                "live_trades_executed": len(
                    [t for t in bridge.live_trades if (t.get("result") or {}).get("executed")]
                ),
                "trade_count": int(bridge.session_live_transaction_count),
                "live_fill_count": int(bridge.session_live_fill_count),
                "live_transaction_count": int(bridge.session_live_transaction_count),
                "session_live_fill_count": int(bridge.session_live_fill_count),
                "session_live_transaction_count": int(
                    bridge.session_live_transaction_count
                ),
                "backfill_mirrored_count": int(bridge.backfill_mirrored_count),
                "resting_orders": len(bridge._resting),  # noqa: SLF001
                "last_live_trade": bridge.live_trades[-1] if bridge.live_trades else None,
                "last_cycle": st.get("last_cycle"),
                "why_not_trade": st.get("why_not_trade"),
                "pipeline_funnel": st.get("pipeline_funnel"),
            }
        )

    try:
        _tick()
        last_sync = time.monotonic()
        last_resting = time.monotonic()
        last_trail = time.monotonic()
        last_dust = time.monotonic()
        while True:
            if should_stop is not None and should_stop():
                logger.info("Full-bot micro session stop requested")
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            await asyncio.sleep(1.0)
            # Regime / cash-first: block new buys while reduce-only or toxic HMM.
            # Per-symbol vol dump cool-off is handled inside maker_inventory —
            # do not globally block all buys when memecoins dump.
            try:
                st_now = runner.status()
                reduce_only = bool(st_now.get("reduce_only"))
                hmm = st_now.get("hmm_regime") or {}
                toxic = bool(hmm.get("is_toxic_flow"))
                uw_block = int(
                    getattr(cfg, "live_micro_underwater_buy_block", 3) or 0
                )
                uw_floor = Decimal(
                    str(
                        getattr(cfg, "live_micro_underwater_min_notional_eur", 25)
                        or 25
                    )
                )
                underwater_n = bridge.underwater_bag_count(min_notional_eur=uw_floor)
                cash_throttle = uw_block > 0 and underwater_n >= uw_block
                block_buys_full = reduce_only or toxic
                new_base_only = bool(
                    getattr(cfg, "live_micro_underwater_block_new_bases_only", True)
                )
                block_buys = block_buys_full or cash_throttle
                was_blocked = bool(getattr(bridge, "_buys_blocked", False))
                if block_buys_full:
                    bridge.set_buys_blocked(True, new_bases_only=False)
                elif cash_throttle:
                    bridge.set_buys_blocked(True, new_bases_only=new_base_only)
                else:
                    bridge.set_buys_blocked(False)
                if cash_throttle and not was_blocked:
                    bridge._push_alert(  # noqa: SLF001
                        "underwater_buy_block",
                        f"buys blocked: {underwater_n} bags below cost "
                        f"(threshold {uw_block})",
                    )

                # Cross-venue: pause when live fill rate is chronically poor.
                maker = runner._maker_strategy()  # noqa: SLF001
                if maker is not None and hasattr(maker, "set_cross_venue_paused"):
                    funnel = st_now.get("pipeline_funnel") or {}
                    cv = funnel.get("cross_venue") or {}
                    cv_orders = int(cv.get("live_orders") or 0)
                    cv_fills = int(cv.get("live_fills") or 0)
                    min_att = int(
                        getattr(cfg, "live_micro_cross_venue_min_attempts", 8) or 8
                    )
                    min_rate = float(
                        getattr(cfg, "live_micro_cross_venue_min_fill_rate", 0.30)
                        or 0.30
                    )
                    pause_cv = False
                    if cv_orders >= min_att:
                        rate = cv_fills / max(cv_orders, 1)
                        pause_cv = rate < min_rate
                    prev_pause = bool(getattr(maker, "cross_venue_paused", False))
                    maker.set_cross_venue_paused(pause_cv)
                    if pause_cv and not prev_pause:
                        bridge._push_alert(  # noqa: SLF001
                            "cross_venue_fill_gate",
                            f"cross-venue paused fills={cv_fills}/{cv_orders} "
                            f"(need ≥{min_rate:.0%})",
                        )
            except Exception:  # noqa: BLE001
                pass
            if time.monotonic() - last_resting >= 8.0:
                for resting_venue in sorted(bridge._execute_venues):  # noqa: SLF001
                    try:
                        await bridge.manage_resting_orders(resting_venue)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "resting order management failed venue=%s", resting_venue
                        )
                last_resting = time.monotonic()
            if time.monotonic() - last_trail >= 5.0:
                for trail_venue in sorted(bridge._execute_venues):  # noqa: SLF001
                    try:
                        trail = await bridge.check_trailing_take_profits(trail_venue)
                        if trail.get("triggered"):
                            logger.info(
                                "Trailing take-profit exits venue=%s: %s",
                                trail_venue,
                                trail.get("triggered"),
                            )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "trailing take-profit check failed venue=%s", trail_venue
                        )
                last_trail = time.monotonic()
                try:
                    if bridge.maybe_reset_after_wind_down():  # noqa: SLF001
                        logger.info("Trading cycle reset after wind-down")
                except Exception:  # noqa: BLE001
                    logger.exception("wind-down cycle reset failed")
            if time.monotonic() - last_dust >= 60.0:
                for dust_venue in sorted(bridge._execute_venues):  # noqa: SLF001
                    try:
                        dust = await bridge.manage_dust_positions(dust_venue)
                        if dust.get("actions"):
                            logger.info(
                                "Dust policy actions venue=%s: %s",
                                dust_venue,
                                dust.get("actions"),
                            )
                    except Exception:  # noqa: BLE001
                        logger.exception("dust policy failed venue=%s", dust_venue)
                last_dust = time.monotonic()
            if time.monotonic() - last_sync >= 30.0:
                for sync_venue in sorted(bridge._execute_venues):  # noqa: SLF001
                    try:
                        await bridge.reconcile_from_exchange(sync_venue)
                    except Exception:  # noqa: BLE001
                        logger.exception("periodic micro sync failed venue=%s", sync_venue)
                last_sync = time.monotonic()
            _tick()
            if not runner.running:
                break
    finally:
        if bridge_holder is not None:
            bridge_holder.pop("bridge", None)
        try:
            # Free locked capital: cancel any leftover resting live quotes.
            bridge._resting_max_age_sec = 0.0  # noqa: SLF001
            for cleanup_venue in sorted(bridge._execute_venues):  # noqa: SLF001
                await bridge.manage_resting_orders(cleanup_venue)
        except Exception:  # noqa: BLE001
            logger.exception("final resting cleanup failed")
        try:
            await runner.stop()
        except Exception:  # noqa: BLE001
            logger.exception("runner stop failed")
        if owns_md:
            try:
                await md.stop()
            except Exception:  # noqa: BLE001
                logger.exception("market data stop failed")
        live.disarm()

    end_status = runner.status()
    live_executed = [
        t for t in bridge.live_trades if (t.get("result") or {}).get("executed")
    ]
    report = {
        "ok": True,
        "mode": "full_bot_micro",
        "continuous": continuous,
        "minutes": minutes,
        "budget_eur": str(budget_eur),
        "symbols": scan_symbols,
        "symbol_count": len(scan_symbols),
        "btc_excluded": exclude_btc,
        "btc_traded": False,
        "pipeline": [
            "market_data",
            "strategy",
            "goe",
            "profitability",
            "risk",
            "micro_budget_live_executor",
        ],
        "strategy_cycles": end_status.get("cycle_count"),
        "netto_winst_eur": str(bridge.realized_trade_pnl_eur),
        "realized_trade_pnl_eur": str(bridge.realized_trade_pnl_eur),
        "portfolio_value_eur": (
            str(bridge.portfolio_value_eur)
            if bridge.portfolio_value_eur is not None
            else None
        ),
        "starting_portfolio_eur": (
            str(bridge.starting_portfolio_eur)
            if bridge.starting_portfolio_eur is not None
            else None
        ),
        "bridge": bridge.snapshot_bridge(),
        "live_trades_attempted": len(bridge.live_trades),
        "live_trades_executed": len(live_executed),
        "trades": bridge.live_trades,
        "runner_status": {
            k: end_status.get(k)
            for k in (
                "strategy",
                "approved_opportunities",
                "executed_opportunities",
                "pipeline_funnel",
                "why_not_trade",
            )
        },
        "finished_at": datetime.now(UTC).isoformat(),
        "note": (
            "Micro = live € capital pocket (recycles). Continuous until stop. "
            "KPIs are bridge realized/MTM only — no paper-pocket equity."
        ),
    }

    path = Path(report_path or "./data/live_micro_session_report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info(
        "Full-bot micro done realized=%s live_fills=%s cycles=%s",
        report["realized_trade_pnl_eur"],
        report["live_trades_executed"],
        report["strategy_cycles"],
    )
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Full-bot micro-live: PaperRunner pipeline + € budget cap"
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=0.0,
        help="Session length in minutes; 0 = continuous until stop",
    )
    parser.add_argument("--budget-eur", type=float, default=2000.0)
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="Optional CSV; default = all market_data_symbols except BTC*",
    )
    parser.add_argument("--include-btc", action="store_true")
    parser.add_argument("--report", type=str, default="./data/live_micro_session_report.json")
    args = parser.parse_args()
    get_settings.cache_clear()
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols.strip()
        else None
    )
    minutes = None if args.minutes <= 0 else args.minutes
    report = asyncio.run(
        run_session(
            minutes=minutes,
            budget_eur=Decimal(str(args.budget_eur)),
            symbols=symbols,
            exclude_btc=not args.include_btc,
            report_path=args.report,
        )
    )
    summary = {k: report[k] for k in report if k not in {"trades", "runner_status"}}
    print(json.dumps(summary, indent=2, default=str))
    print(f"trades_detail_count={len(report.get('trades') or [])}")
    print(f"report={args.report}")


if __name__ == "__main__":
    main()

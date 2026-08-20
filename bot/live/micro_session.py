"""Full-bot micro-live session: small capital only, full PaperRunner pipeline.

Micro means a hard € budget (default 25), not a stripped strategy.
Uses PaperRunner (strategy, GOE, profitability, risk, maker stack, labs) and
routes marketable Bitvavo legs through LiveMicroEngine via MicroBudgetLiveExecutor.
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
from bot.core.enums import ExecutionMode
from bot.engine.orchestrator import TradingEngine
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor
from bot.live.micro_engine import LiveMicroEngine, reset_micro_engine
from bot.market_data.service import MarketDataService
from bot.paper.runner import PaperRunner
from bot.paper.store import PaperTradingStore
from bot.risk.risk_engine import RiskEngine

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


def _non_btc_symbols(settings: Settings) -> list[str]:
    raw = [
        s.strip().upper().replace("-", "").replace("/", "")
        for s in settings.market_data_symbols.split(",")
        if s.strip()
    ]
    return [s for s in raw if not s.startswith("BTC")]


def _session_settings(
    base: Settings,
    *,
    budget_eur: Decimal,
    symbols: list[str],
    persist_path: Path,
) -> Settings:
    """Paper mode + micro unlocks already in env; tighten capital and allowlists."""
    mode = getattr(base, "market_data_mode", "local") or "local"
    # Prefer shared Redis feed from the fleet publisher when available.
    if mode == "local":
        mode = "shared"
    return base.model_copy(
        update={
            "execution_mode": ExecutionMode.PAPER,
            "paper_trading_enabled": True,
            "paper_auto_start": False,
            "paper_starting_eur": float(budget_eur),
            "paper_persist_path": str(persist_path),
            "paper_seed_inventory_pct": 0.0,
            "paper_seed_max_assets": 0,
            # Keep maker strategy on (paper quotes); live taker via bridge.
            "paper_maker_min_notional_eur": min(5.0, float(budget_eur)),
            "paper_maker_min_profit_eur": min(0.05, float(budget_eur) * 0.01),
            "risk_max_position_usd": float(budget_eur),
            "risk_max_daily_loss_usd": float(budget_eur),
            "live_micro_venues": "bitvavo",
            "live_micro_symbols": "*",
            "live_micro_max_notional_eur": float(budget_eur),
            "live_micro_max_daily_loss_eur": float(budget_eur),
            "live_micro_max_open_orders": 8,
            "market_data_mode": mode,
            "market_data_symbols": ",".join(symbols) if symbols else base.market_data_symbols,
        }
    )


def attach_micro_bridge(
    runner: PaperRunner,
    *,
    live_engine: LiveMicroEngine,
    budget_eur: Decimal,
    exclude_bases: set[str] | None = None,
) -> MicroBudgetLiveExecutor:
    """Replace PaperRunner executor with budget-capped live bridge; rebuild engine."""
    bridge = MicroBudgetLiveExecutor(
        runner._settings,  # noqa: SLF001
        portfolio=runner.portfolio,
        live_engine=live_engine,
        budget_eur=budget_eur,
        execute_venues={"bitvavo"},
        exclude_bases=exclude_bases or {"BTC"},
        live_maker=False,
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
    minutes: float = 15.0,
    budget_eur: Decimal = Decimal("25"),
    symbols: list[str] | None = None,
    settings: Settings | None = None,
    report_path: str | Path | None = None,
    exclude_btc: bool = True,
    market_data: MarketDataService | None = None,
    own_market_data: bool | None = None,
    status_callback: Any | None = None,
    should_stop: Any | None = None,
) -> dict[str, Any]:
    """Run full PaperRunner cycles with live Bitvavo fills capped at budget_eur."""
    base = settings or get_settings()
    if exclude_btc:
        scan_symbols = symbols or _non_btc_symbols(base)
        scan_symbols = [s for s in scan_symbols if not s.upper().startswith("BTC")]
    else:
        scan_symbols = symbols or [
            s.strip().upper().replace("-", "").replace("/", "")
            for s in base.market_data_symbols.split(",")
            if s.strip()
        ]
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
    risk = RiskEngine(cfg)
    store = PaperTradingStore(cfg)
    runner = PaperRunner(cfg, market_data=md, risk_engine=risk, store=store)
    bridge = attach_micro_bridge(
        runner,
        live_engine=live,
        budget_eur=budget_eur,
        exclude_bases={"BTC"} if exclude_btc else set(),
    )

    started = await runner.start()
    if not started.get("started"):
        return {
            "ok": False,
            "reason": "paper_runner_start_failed",
            "detail": started,
            "mode": "full_bot_micro",
            "trades": [],
        }

    deadline = time.monotonic() + minutes * 60.0
    started_mono = time.monotonic()
    logger.info(
        "Full-bot micro session start budget=%s symbols=%s minutes=%s mode=%s",
        budget_eur,
        len(scan_symbols),
        minutes,
        cfg.market_data_mode,
    )
    start_equity = Decimal(str(runner.portfolio.state.total_equity))
    start_status = runner.status()

    def _tick() -> None:
        if status_callback is None:
            return
        st = runner.status()
        elapsed = time.monotonic() - started_mono
        remaining = max(0.0, deadline - time.monotonic())
        equity = Decimal(str(runner.portfolio.state.total_equity))
        status_callback(
            {
                "symbol_count": len(scan_symbols),
                "symbols_sample": scan_symbols[:12],
                "elapsed_seconds": round(elapsed, 1),
                "remaining_seconds": round(remaining, 1),
                "paper_cycles": st.get("cycle_count"),
                "strategy": st.get("strategy"),
                "approved_opportunities": st.get("approved_opportunities"),
                "executed_opportunities": st.get("executed_opportunities"),
                "trade_count": st.get("trade_count"),
                "starting_equity_eur": str(start_equity),
                "current_equity_eur": str(equity),
                "pnl_paper_pocket_eur": str(equity - start_equity),
                "bridge": bridge.snapshot_bridge(),
                "live_trades_attempted": len(bridge.live_trades),
                "live_trades_executed": len(
                    [t for t in bridge.live_trades if (t.get("result") or {}).get("executed")]
                ),
                "last_live_trade": bridge.live_trades[-1] if bridge.live_trades else None,
                "last_cycle": st.get("last_cycle"),
                "why_not_trade": st.get("why_not_trade"),
                "pipeline_funnel": st.get("pipeline_funnel"),
            }
        )

    try:
        _tick()
        while time.monotonic() < deadline:
            if should_stop is not None and should_stop():
                logger.info("Full-bot micro session stop requested")
                break
            await asyncio.sleep(1.0)
            _tick()
            if not runner.running:
                break
    finally:
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

    end_equity = Decimal(str(runner.portfolio.state.total_equity))
    end_status = runner.status()
    live_executed = [
        t for t in bridge.live_trades if (t.get("result") or {}).get("executed")
    ]
    report = {
        "ok": True,
        "mode": "full_bot_micro",
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
        "paper_cycles": end_status.get("cycle_count"),
        "starting_equity_eur": str(start_equity),
        "ending_equity_eur": str(end_equity),
        "pnl_paper_pocket_eur": str(end_equity - start_equity),
        "bridge": bridge.snapshot_bridge(),
        "live_trades_attempted": len(bridge.live_trades),
        "live_trades_executed": len(live_executed),
        "trades": bridge.live_trades,
        "paper_status_start": {
            k: start_status.get(k)
            for k in (
                "strategy",
                "trade_count",
                "approved_opportunities",
                "executed_opportunities",
                "global_engine",
            )
        },
        "paper_status_end": {
            k: end_status.get(k)
            for k in (
                "strategy",
                "trade_count",
                "approved_opportunities",
                "executed_opportunities",
                "global_engine",
                "pipeline_funnel",
                "why_not_trade",
            )
        },
        "finished_at": datetime.now(UTC).isoformat(),
        "note": (
            "Micro = capital cap only. Full PaperRunner pipeline ran; "
            "marketable Bitvavo legs executed live within budget; "
            "maker/post-only stayed paper; non-Bitvavo venues skipped."
        ),
    }

    path = Path(report_path or "./data/live_micro_session_report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info(
        "Full-bot micro done pnl=%s live_fills=%s cycles=%s",
        report["pnl_paper_pocket_eur"],
        report["live_trades_executed"],
        report["paper_cycles"],
    )
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Full-bot micro-live: PaperRunner pipeline + € budget cap"
    )
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--budget-eur", type=float, default=25.0)
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
    report = asyncio.run(
        run_session(
            minutes=args.minutes,
            budget_eur=Decimal(str(args.budget_eur)),
            symbols=symbols,
            exclude_btc=not args.include_btc,
            report_path=args.report,
        )
    )
    summary = {k: report[k] for k in report if k not in {"trades", "paper_status_end"}}
    print(json.dumps(summary, indent=2, default=str))
    print(f"trades_detail_count={len(report.get('trades') or [])}")
    print(f"report={args.report}")


if __name__ == "__main__":
    main()

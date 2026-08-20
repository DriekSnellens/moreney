"""FastAPI application entrypoint.

Exposes health, status, market-data, risk/kill-switch, and paper-trading endpoints.
A lightweight HTML dashboard consumes the paper APIs. No withdrawal routes exist.
Live trading, withdrawals, and leverage remain disabled.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Security
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette import status as http_status

from bot import __version__
from bot.core.config import Settings, get_settings
from bot.core.enums import ExecutionMode, KillSwitchState, OpportunityLifecycleStatus
from bot.market_data.cache import MarketDataCache
from bot.market_data.service import MarketDataService
from bot.paper.dashboard import (
    render_dashboard,
    render_dashboard_lite,
    render_fleet_dashboard,
)
from bot.strategy_lab.dashboard import (
    load_latest_lab_results,
    render_strategy_lab_dashboard,
)
from bot.paper.fleet import collect_fleet_overview, publicize_instance_urls, reset_fleet
from bot.paper.auth import (
    clear_session_cookie,
    credentials_valid,
    login_redirect,
    render_login_page,
    request_has_valid_session,
    set_session_cookie,
    wants_html,
)
from bot.paper.runner import PaperRunner
from bot.opportunity.parameter_log import PARAMETER_CHANGES
from bot.paper.store import PaperTradingStore
from bot.funding.models import FundingEventType
from bot.funding.service import get_funding_service, reset_funding_service
from bot.live.service import get_live_service, reset_live_service
from bot.live.micro_engine import get_micro_engine, reset_micro_engine
from bot.risk.events import InMemoryRiskEventStore
from bot.risk.kill_switch import KillSwitch
from bot.risk.risk_engine import RiskEngine

_event_store = InMemoryRiskEventStore()
_kill_switch: KillSwitch | None = None
_risk_engine: RiskEngine | None = None
_market_data_service: MarketDataService | None = None
_paper_runner: PaperRunner | None = None
_last_paper_cycle: dict[str, Any] | None = None
_dashboard_basic = HTTPBasic(auto_error=False)


def get_kill_switch() -> KillSwitch:
    global _kill_switch
    if _kill_switch is None:
        settings = get_settings()
        _kill_switch = KillSwitch(settings, on_event=_event_store.record)
    return _kill_switch


def get_risk_engine() -> RiskEngine:
    global _risk_engine
    if _risk_engine is None:
        _risk_engine = RiskEngine(get_settings(), kill_switch=get_kill_switch())
    return _risk_engine


def get_market_data_service() -> MarketDataService:
    global _market_data_service
    if _market_data_service is None:
        settings = get_settings()
        cache = None
        if settings.market_data_mode in {"shared", "publisher"}:
            from bot.core.redis_client import get_redis

            cache = MarketDataCache(
                redis_client=get_redis(settings.redis_url),
                ttl_seconds=settings.market_data_redis_ttl_seconds,
            )
        # Do not auto-start live WebSockets in paper API processes —
        # shared mode hydrates from Redis; local mode connects in PaperRunner.start().
        _market_data_service = MarketDataService(
            settings,
            cache=cache,
            start_websockets=False,
        )
    return _market_data_service


def get_paper_runner() -> PaperRunner:
    global _paper_runner
    if _paper_runner is None:
        settings = get_settings()
        store = PaperTradingStore(settings)
        _paper_runner = PaperRunner(
            settings,
            market_data=get_market_data_service(),
            risk_engine=get_risk_engine(),
            store=store,
        )
    return _paper_runner


def set_last_paper_cycle(payload: dict[str, Any] | None) -> None:
    global _last_paper_cycle
    _last_paper_cycle = payload


def reset_risk_singletons() -> None:
    """Test helper to clear process-local risk / market-data / paper state."""
    global _kill_switch, _risk_engine, _event_store, _market_data_service
    global _paper_runner, _last_paper_cycle
    _kill_switch = None
    _risk_engine = None
    _event_store = InMemoryRiskEventStore()
    _market_data_service = None
    _paper_runner = None
    _last_paper_cycle = None
    reset_funding_service()
    reset_live_service()
    reset_micro_engine()


class DashboardLoginRedirect(Exception):
    """Raised when an HTML dashboard request needs the login page."""

    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def _dashboard_auth_enabled() -> bool:
    return bool(get_settings().dashboard_basic_auth_enabled)


def require_dashboard_access(
    request: Request,
    credentials: HTTPBasicCredentials | None = Security(_dashboard_basic),
) -> None:
    settings = get_settings()
    if not settings.dashboard_basic_auth_enabled:
        return
    configured_password = settings.dashboard_basic_auth_password
    if configured_password is None or not configured_password.get_secret_value():
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dashboard auth enabled but password is not configured",
        )
    if request_has_valid_session(request, settings):
        return
    if credentials is not None and credentials_valid(
        settings, credentials.username, credentials.password
    ):
        return
    if wants_html(request):
        next_path = request.url.path or "/fleet"
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        raise DashboardLoginRedirect(next_path)
    raise HTTPException(
        status_code=http_status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized dashboard access",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if settings.execution_mode == ExecutionMode.LIVE:
        settings.require_live_credentials()
    get_kill_switch()
    md = get_market_data_service()
    runner = get_paper_runner()
    if settings.paper_trading_enabled and settings.paper_auto_start:
        await runner.start()  # connects public WebSockets + starts loop
    yield
    await runner.shutdown()
    await md.stop()


app = FastAPI(
    title="Moreney Trading System",
    description=(
        "Production-oriented cryptocurrency trading API. "
        "Strategies emit opportunities; profitability and risk gate execution. "
        "No withdrawal functionality is exposed. No leverage in this version. "
        "Realtime market data uses public feeds only; execution stays paper."
    ),
    version=__version__,
    lifespan=lifespan,
)

@app.exception_handler(DashboardLoginRedirect)
async def _dashboard_login_redirect(_request: Request, exc: DashboardLoginRedirect):
    return login_redirect(exc.next_path)



@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/status")
async def status() -> dict[str, Any]:
    settings: Settings = get_settings()
    ks = get_kill_switch().status()
    runner = get_paper_runner()
    funding_flags = get_funding_service().public_status_flags()
    return {
        "version": __version__,
        "environment": settings.app_env,
        "execution_mode": settings.execution_mode.value,
        "exchange": settings.exchange_name,
        "paper_mode": settings.execution_mode == ExecutionMode.PAPER,
        "paper_trading_enabled": settings.paper_trading_enabled,
        "paper_running": runner.running,
        "market_data_mode": settings.market_data_mode,
        "live_trading_enabled": False,
        "withdrawals_supported": False,
        "automatic_withdrawals_enabled": False,
        "leverage_supported": False,
        "funding_main_venue": funding_flags["funding_main_venue"],
        "funding_venues": funding_flags["funding_venues"],
        "live_readiness": {
            "active_phase": get_live_service().active_phase().name.lower(),
            "can_place_live_orders": False,
            "observe_enabled": bool(settings.live_observe_enabled),
            "micro_enabled": bool(settings.live_micro_enabled),
            "orders_unlocked": bool(settings.live_orders_unlocked),
        },
        "kill_switch": ks.model_dump(mode="json"),
    }


@app.get("/portfolio")
async def portfolio_overview() -> dict[str, Any]:
    """Multi-venue portfolio summary (paper ledger or live balances)."""
    summary = await get_funding_service().portfolio_summary()
    return summary.model_dump(mode="json")


@app.get("/balances")
async def balances_all() -> dict[str, Any]:
    snaps = await get_funding_service().get_venue_balances()
    return {
        "withdrawals_supported": False,
        "venues": [s.model_dump(mode="json") for s in snaps],
    }


@app.get("/balances/{venue}")
async def balances_venue(venue: str) -> dict[str, Any]:
    snap = await get_funding_service().get_balances_for_venue(venue)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Unknown venue: {venue}")
    return snap.model_dump(mode="json")


@app.get("/funding")
async def funding_overview() -> dict[str, Any]:
    """Funding overview: deposits, tracked exits, pending — no auto-withdraw."""
    svc = get_funding_service()
    summary = await svc.portfolio_summary()
    deposits = svc.funding_events(event_type=FundingEventType.DEPOSIT, limit=100)
    exits = svc.funding_events(event_type=FundingEventType.WITHDRAWAL, limit=100)
    pending = [
        e.model_dump(mode="json")
        for e in svc.funding_events(limit=200)
        if e.status.value == "pending"
    ]
    return {
        "main_funding_venue": svc.main_funding_venue(),
        "total_deposited": str(summary.total_deposited),
        "total_withdrawn": str(summary.total_withdrawn),
        "current_portfolio": str(summary.current_portfolio),
        "pnl": str(summary.pnl),
        "withdrawals_supported": False,
        "automatic_withdrawals_enabled": False,
        "withdraw_instructions": (
            f"To withdraw, use the {svc.main_funding_venue()} exchange UI. "
            "Moreney does not execute withdrawals."
        ),
        "deposits": [e.model_dump(mode="json") for e in deposits],
        "recorded_exits": [e.model_dump(mode="json") for e in exits],
        "pending": pending,
        "note": summary.note,
    }


@app.get("/funding/deposits")
async def funding_deposits(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    rows = get_funding_service().funding_events(
        event_type=FundingEventType.DEPOSIT, limit=limit
    )
    return {"deposits": [e.model_dump(mode="json") for e in rows]}


@app.get("/funding/recorded-exits")
async def funding_recorded_exits(
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """User-recorded cash-outs via exchange UI (tracking only)."""
    rows = get_funding_service().funding_events(
        event_type=FundingEventType.WITHDRAWAL, limit=limit
    )
    return {
        "recorded_exits": [e.model_dump(mode="json") for e in rows],
        "withdrawals_supported": False,
        "bot_executed": False,
    }


@app.post("/funding/events")
async def funding_record_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Manually record a deposit or tracked exit (never executes exchange transfers)."""
    svc = get_funding_service()
    event_type = str(payload.get("type") or "deposit").strip().lower()
    venue = str(payload.get("venue") or svc.main_funding_venue())
    amount = payload.get("amount")
    if amount is None:
        raise HTTPException(status_code=400, detail="amount is required")
    asset = str(payload.get("asset") or payload.get("currency") or "EUR")
    currency = str(payload.get("currency") or asset)
    ref = payload.get("external_reference")
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if event_type in {"withdrawal", "exit", "cash_out"}:
        event = svc.record_withdrawal_tracking(
            venue=venue,
            amount=amount,
            asset=asset,
            currency=currency,
            external_reference=ref,
            metadata=meta,
        )
    elif event_type == "deposit":
        event = svc.record_deposit(
            venue=venue,
            amount=amount,
            asset=asset,
            currency=currency,
            external_reference=ref,
            metadata=meta,
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="type must be deposit or withdrawal (tracking only)",
        )
    return {"event": event.model_dump(mode="json"), "executed": False}


@app.get("/rebalancing/recommendations")
async def rebalancing_recommendations() -> dict[str, Any]:
    """Suggest inventory moves — never executed automatically."""
    recs = get_funding_service().rebalance_recommendations()
    fee_bps = float(get_settings().global_transfer_fee_bps)
    return {
        "auto_execute": False,
        "transfer_fee_bps": fee_bps,
        "recommendations": [r.model_dump(mode="json") for r in recs],
        "instructions": (
            "Transfer manually via the exchange withdrawal/deposit UI, "
            "then inventory will update on the next balance refresh."
        ),
    }


@app.get("/live/readiness")
async def live_readiness() -> dict[str, Any]:
    """Full live readiness report across phases 0–5 (fail-closed)."""
    return await get_live_service().full_status()


@app.get("/live/status")
async def live_status() -> dict[str, Any]:
    svc = get_live_service()
    micro = svc.phase3_micro()
    return {
        "active_phase": svc.active_phase().name.lower(),
        "live_trading_enabled": bool(get_settings().live_trading_enabled),
        "can_place_live_orders": bool(micro.get("can_place_orders")),
        "block_reason": micro.get("block_reason"),
        "withdrawals_supported": False,
        "production_execution_enabled": False,
        "go_no_go_ready": svc.phase0().get("ready"),
    }


@app.get("/live/observe")
async def live_observe(
    probe: bool = Query(default=False),
) -> dict[str, Any]:
    """Phase 1: read-only live balances (no orders)."""
    return await get_live_service().phase1_observe(probe=probe)


@app.get("/live/credentials")
async def live_credentials(
    probe: bool = Query(default=False),
) -> dict[str, Any]:
    """Per-venue API key presence (+ optional read-only health probe)."""
    return await get_live_service().credentials(probe=probe)


@app.get("/live/micro/unlock-checklist")
async def live_micro_unlock_checklist() -> dict[str, Any]:
    """Which env flags still block micro-live (never flips them)."""
    return get_live_service().micro_unlock_checklist()


@app.post("/live/micro/dry-run")
async def live_micro_dry_run(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a hypothetical order against micro policy — does not place."""
    return get_live_service().micro_dry_run(payload or {})


@app.get("/live/micro/engine")
async def live_micro_engine_status() -> dict[str, Any]:
    """Micro-live engine status (separate from PaperRunner)."""
    return get_micro_engine().status()


@app.post("/live/micro/arm")
async def live_micro_arm() -> dict[str, Any]:
    """Arm micro engine for this process after env unlocks. Does not place orders."""
    return get_micro_engine().arm()


@app.post("/live/micro/disarm")
async def live_micro_disarm() -> dict[str, Any]:
    return get_micro_engine().disarm()


@app.post("/live/micro/orders")
async def live_micro_orders(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Submit a micro live order. Requires env unlocks + arm + confirm=true.

    Body example::
        {"venue":"bitvavo","symbol":"BTCEUR","side":"buy","notional_eur":25,"confirm":true}
    """
    body = payload or {}
    confirm = bool(body.get("confirm"))
    return await get_micro_engine().submit(body, confirm=confirm)


@app.get("/live/alerts")
async def live_alerts() -> dict[str, Any]:
    """Phase 4: venue/rebalance alerts (non-executing)."""
    observe = await get_live_service().phase1_observe()
    return get_live_service().phase4_alerts(observe)


@app.get("/live/audit")
async def live_audit(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """Phase 5: recent audit events (secrets redacted)."""
    hardening = get_live_service().phase5_hardening()
    return {
        "events": hardening.get("recent_audit") or [],
        "runbook": hardening.get("runbook"),
        "withdrawals_supported": False,
    }


@app.get("/market-data/status")
async def market_data_status() -> dict[str, Any]:
    """Per-exchange public feed health (connected / stale / age)."""
    service = get_market_data_service()
    raw = service.status()
    compact: dict[str, Any] = {}
    for exchange, health_row in raw.items():
        compact[exchange] = {
            "connected": health_row.get("connected", False),
            "stale": health_row.get("stale", True),
            "last_message_age_ms": health_row.get("last_message_age_ms"),
            "synchronized": health_row.get("synchronized", False),
            "reconnect_count": health_row.get("reconnect_count", 0),
            "sequence_gap_count": health_row.get("sequence_gap_count", 0),
            "message_rate_per_sec": health_row.get("message_rate_per_sec", 0.0),
            "connection_state": health_row.get("connection_state"),
        }
    return compact


@app.get("/paper/last-cycle")
async def paper_last_cycle() -> dict[str, Any]:
    """Last paper trading cycle result (integration / ops visibility)."""
    runner = get_paper_runner()
    cycle = runner.last_cycle if runner.last_cycle is not None else _last_paper_cycle
    if cycle is None:
        return {"available": False, "cycle": None}
    return {"available": True, "cycle": cycle}


@app.get("/paper/status")
async def paper_status() -> dict[str, Any]:
    status = get_paper_runner().status()
    status["live_readiness"] = get_live_service().compact_status()
    return status


@app.get("/paper/portfolio")
async def paper_portfolio() -> dict[str, Any]:
    portfolio = get_paper_runner().portfolio
    state = portfolio.state
    return {
        "quote_asset": state.quote_asset,
        "starting_capital": str(get_settings().paper_starting_eur),
        "equity": str(state.total_equity),
        "balances": {
            k: {"available": str(v.available), "reserved": str(v.reserved), "total": str(v.total)}
            for k, v in state.balances.items()
        },
        "positions": {
            k: {
                "quantity": str(v.quantity),
                "average_entry_price": str(v.average_entry_price),
                "realized_pnl": str(v.realized_pnl),
                "fees_paid": str(v.fees_paid),
            }
            for k, v in state.positions.items()
            if v.quantity != 0
        },
        "stats": state.stats.model_dump(mode="json"),
        "execution_mode": ExecutionMode.PAPER.value,
    }


@app.get("/paper/performance")
async def paper_performance() -> dict[str, Any]:
    snap = get_paper_runner().tracker.snapshot()
    return snap.model_dump(mode="json")


@app.get("/paper/overview")
async def paper_overview() -> dict[str, Any]:
    """Current paper-trading state in one payload for operators/UI."""
    runner = get_paper_runner()
    portfolio = runner.portfolio.state
    performance = runner.tracker.snapshot()
    return {
        "updated_at": portfolio.as_of.isoformat(),
        "execution_mode": ExecutionMode.PAPER.value,
        "status": runner.status(),
        "market_data": get_market_data_service().status(),
        "portfolio": {
            "quote_asset": portfolio.quote_asset,
            "starting_capital": str(get_settings().paper_starting_eur),
            "equity": str(portfolio.total_equity),
            "balances": {
                k: {
                    "available": str(v.available),
                    "reserved": str(v.reserved),
                    "total": str(v.total),
                }
                for k, v in portfolio.balances.items()
            },
            "positions": {
                k: {
                    "quantity": str(v.quantity),
                    "average_entry_price": str(v.average_entry_price),
                    "realized_pnl": str(v.realized_pnl),
                    "fees_paid": str(v.fees_paid),
                }
                for k, v in portfolio.positions.items()
                if v.quantity != 0
            },
            "venue_ledger": (
                runner.portfolio.venue_ledger.export()
                if runner.portfolio.venue_ledger is not None
                else None
            ),
        },
        "performance": performance.model_dump(mode="json"),
    }


@app.get("/paper/statistics/daily")
async def paper_statistics_daily() -> dict[str, Any]:
    rows = get_paper_runner().tracker.daily_stats()
    return {"daily": [r.model_dump(mode="json") for r in rows]}


@app.get("/paper/statistics/strategies")
async def paper_statistics_strategies() -> dict[str, Any]:
    rows = get_paper_runner().tracker.strategy_stats()
    payload = []
    for r in rows:
        item = r.model_dump(mode="json")
        item["win_rate"] = str(r.win_rate)
        item["profit_factor"] = str(r.profit_factor)
        payload.append(item)
    return {"strategies": payload}


@app.get("/paper/statistics/exchanges")
async def paper_statistics_exchanges() -> dict[str, Any]:
    rows = get_paper_runner().tracker.exchange_pair_stats()
    payload = []
    for r in rows:
        item = r.model_dump(mode="json")
        item["pair"] = r.pair_key
        item["win_rate"] = str(r.win_rate)
        payload.append(item)
    return {"exchanges": payload}


@app.get("/paper/statistics/hourly")
async def paper_statistics_hourly() -> dict[str, Any]:
    rows = get_paper_runner().tracker.hourly_stats()
    payload = []
    for r in rows:
        item = r.model_dump(mode="json")
        item["label"] = r.label
        item["average_net_pnl"] = str(r.average_net_pnl)
        item["win_rate"] = str(r.win_rate)
        payload.append(item)
    return {"hourly": payload}


@app.get("/paper/opportunities")
async def paper_opportunities(
    limit: int = Query(default=100, ge=1, le=1000),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    status_enum: OpportunityLifecycleStatus | None = None
    if status:
        try:
            status_enum = OpportunityLifecycleStatus(status.lower())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}") from exc
    rows = get_paper_runner().tracker.opportunities(limit=limit, status=status_enum)
    return {"opportunities": [r.model_dump(mode="json") for r in rows]}


@app.get("/paper/opportunity-decisions")
async def paper_opportunity_decisions(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    runner = get_paper_runner()
    log = getattr(runner, "_decision_log", None)
    if log is None:
        return {"decisions": []}
    return {"decisions": log.export()[-limit:]}


@app.get("/paper/why-not-trade")
async def paper_why_not_trade() -> dict[str, Any]:
    runner = get_paper_runner()
    missed = getattr(runner, "_missed", None)
    kpis = (runner.status() or {}).get("net_kpis") or {}
    cal = getattr(runner, "_calibrator", None)
    return {
        "why_not_trade": missed.why_not_trade() if missed is not None else {},
        "net_kpis": kpis,
        "ev_calibration": cal.snapshot() if cal is not None else {},
        "parameter_changes": PARAMETER_CHANGES,
    }


@app.get("/paper/trades")
async def paper_trades(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    return {"trades": get_paper_runner().tracker.trades(limit=limit)}


@app.post("/paper/start")
async def paper_start() -> dict[str, Any]:
    settings = get_settings()
    if settings.execution_mode != ExecutionMode.PAPER:
        raise HTTPException(status_code=403, detail="Paper start refused: EXECUTION_MODE is not paper")
    if not settings.paper_trading_enabled:
        raise HTTPException(status_code=403, detail="PAPER_TRADING_ENABLED=false")
    return await get_paper_runner().start()


@app.post("/paper/stop")
async def paper_stop() -> dict[str, Any]:
    return await get_paper_runner().stop()


@app.post("/paper/reset")
async def paper_reset(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reset paper portfolio only. Never affects real exchange accounts."""
    confirm = bool((payload or {}).get("confirm"))
    result = await get_paper_runner().reset(confirm=confirm)
    if not result.get("reset"):
        raise HTTPException(status_code=400, detail=result)
    return result





@app.get("/login", response_class=HTMLResponse)
async def login_page(next: str = Query(default="/fleet")) -> HTMLResponse:
    settings = get_settings()
    if not settings.dashboard_basic_auth_enabled:
        return RedirectResponse(url=next if next.startswith("/") else "/fleet", status_code=303)
    return render_login_page(next_path=next)


@app.post("/login")
async def login_submit(
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/fleet"),
) -> Response:
    settings = get_settings()
    if not settings.dashboard_basic_auth_enabled:
        return RedirectResponse(url=next if next.startswith("/") else "/fleet", status_code=303)
    if not credentials_valid(settings, username, password):
        return render_login_page(next_path=next, error="Invalid username or password")
    safe_next = next if next.startswith("/") else "/fleet"
    response = RedirectResponse(url=safe_next, status_code=303)
    set_session_cookie(response, settings, username)
    return response


@app.post("/logout")
@app.get("/logout")
async def logout() -> Response:
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    return response


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/fleet", response_class=HTMLResponse)
async def fleet_dashboard(
    request: Request, _: None = Depends(require_dashboard_access)
) -> HTMLResponse:
    """One page covering all configured paper instances."""
    payload = publicize_instance_urls(
        await collect_fleet_overview(get_settings()),
        hostname=request.url.hostname or request.headers.get("host", ""),
        scheme=request.url.scheme,
    )
    return render_fleet_dashboard(payload)


@app.get("/strategy-lab", response_class=HTMLResponse)
@app.get("/lab", response_class=HTMLResponse)
async def strategy_lab_dashboard(
    _: None = Depends(require_dashboard_access),
) -> HTMLResponse:
    """Strategy Research Lab leaderboard (shadow/research; no live execution)."""
    settings = get_settings()
    if not getattr(settings, "strategy_lab_enabled", True):
        raise HTTPException(status_code=404, detail="Strategy Lab disabled")
    payload = load_latest_lab_results(Path(settings.strategy_lab_results_path))
    return render_strategy_lab_dashboard(payload)


@app.get("/strategy-lab/api")
@app.get("/lab/api")
async def strategy_lab_api(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    settings = get_settings()
    payload = load_latest_lab_results(Path(settings.strategy_lab_results_path))
    if payload is None:
        return {"available": False, "message": "Run: python -m bot.strategy_lab.runner"}
    return {
        "available": True,
        "research_only": bool(getattr(settings, "strategy_lab_research_only", True)),
        "execution_enabled": bool(
            getattr(settings, "strategy_lab_execution_enabled", False)
        ),
        "dataset_id": payload.get("dataset_id"),
        "data_label": payload.get("data_label"),
        "leaderboard": payload.get("leaderboard"),
        "fingerprints": payload.get("fingerprints"),
        "frozen_config": payload.get("frozen_config"),
    }


@app.get("/fleet/api")
async def fleet_api(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    return await collect_fleet_overview(get_settings())


@app.post("/fleet/reset")
async def fleet_reset(
    payload: dict[str, Any] | None = None,
    _: None = Depends(require_dashboard_access),
) -> dict[str, Any]:
    """Reset every paper bot in the fleet. Never touches live exchange accounts."""
    body = payload or {}
    confirm = bool(body.get("confirm"))
    restart = bool(body.get("restart", True))
    result = await reset_fleet(get_settings(), confirm=confirm, restart=restart)
    if not confirm:
        raise HTTPException(status_code=400, detail=result)
    return result

@app.get("/paper/dashboard", response_class=HTMLResponse)
async def paper_dashboard(_: None = Depends(require_dashboard_access)) -> HTMLResponse:
    runner = get_paper_runner()
    snap = runner.tracker.snapshot()
    strategies = []
    for s in runner.tracker.strategy_stats():
        item = s.model_dump(mode="json")
        item["win_rate"] = str(s.win_rate)
        strategies.append(item)
    exchanges = []
    for p in runner.tracker.exchange_pair_stats():
        item = p.model_dump(mode="json")
        item["win_rate"] = str(p.win_rate)
        exchanges.append(item)
    opportunities = [o.model_dump(mode="json") for o in runner.tracker.opportunities(limit=25)]
    hourly = [h.model_dump(mode="json") for h in runner.tracker.hourly_stats()]
    trades = runner.tracker.trades(limit=100)
    funding = await get_funding_service().portfolio_summary()
    rebalance = get_funding_service().rebalance_recommendations()
    funding_payload = funding.model_dump(mode="json")
    funding_payload["recommendations"] = [r.model_dump(mode="json") for r in rebalance]
    funding_payload["deposits"] = [
        e.model_dump(mode="json")
        for e in get_funding_service().funding_events(
            event_type=FundingEventType.DEPOSIT, limit=10
        )
    ]
    live_payload = get_live_service().compact_status()
    live_payload["unlock"] = get_live_service().micro_unlock_checklist()
    return render_dashboard(
        {
            "status": runner.status(),
            "performance": snap.model_dump(mode="json"),
            "strategies": strategies,
            "exchanges": exchanges,
            "opportunities": opportunities,
            "hourly": hourly,
            "trades": trades,
            "funding": funding_payload,
            "live_readiness": live_payload,
        }
    )


@app.get("/paper/dashboard-lite", response_class=HTMLResponse)
async def paper_dashboard_lite(_: None = Depends(require_dashboard_access)) -> HTMLResponse:
    runner = get_paper_runner()
    snap = runner.tracker.snapshot()
    opportunities = [o.model_dump(mode="json") for o in runner.tracker.opportunities(limit=12)]
    return render_dashboard_lite(
        {
            "status": runner.status(),
            "performance": snap.model_dump(mode="json"),
            "opportunities": opportunities,
            "hourly": [h.model_dump(mode="json") for h in runner.tracker.hourly_stats()],
            "trades": runner.tracker.trades(limit=50),
        }
    )


@app.get("/risk/kill-switch")
async def kill_switch_status() -> dict[str, Any]:
    return get_kill_switch().status().model_dump(mode="json")


@app.get("/risk/events")
async def risk_events() -> dict[str, Any]:
    return {
        "events": [event.model_dump(mode="json") for event in _event_store.events],
    }


@app.post("/risk/kill-switch/recover")
async def kill_switch_recover() -> dict[str, Any]:
    ks = get_kill_switch()
    recovered = await ks.recover()
    if not recovered:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Recovery denied — conditions not satisfied or still blocked",
                "status": ks.status().model_dump(mode="json"),
            },
        )
    return {"recovered": True, "status": ks.status().model_dump(mode="json")}


@app.post("/risk/kill-switch/emergency-stop")
async def kill_switch_emergency_stop(payload: dict[str, str] | None = None) -> dict[str, Any]:
    reason = (payload or {}).get("reason", "manual emergency stop via API")
    await get_kill_switch().emergency_stop(reason)
    status = get_kill_switch().status()
    assert status.state == KillSwitchState.EMERGENCY_STOP
    return {"status": status.model_dump(mode="json")}


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "name": "Moreney",
            "message": "Trading API. Paper dashboard at /paper/dashboard",
            "docs": "/docs",
            "paper_dashboard": "/paper/dashboard",
            "paper_dashboard_lite": "/paper/dashboard-lite",
            "fleet_dashboard": "/fleet",
            "all_bots_dashboard": "/dashboard",
            "strategy_lab": "/strategy-lab",
            "dashboard_basic_auth_enabled": _dashboard_auth_enabled(),
            "execution_mode": "paper",
            "live_trading_enabled": False,
            "withdrawals_supported": False,
            "leverage_supported": False,
        }
    )


def run() -> None:
    settings = get_settings()
    # Ensure data directory exists for paper persistence.
    Path(settings.paper_persist_path).parent.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "bot.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.app_debug,
    )


if __name__ == "__main__":
    run()

"""FastAPI application entrypoint.

Exposes health, status, market-data, risk/kill-switch, funding, and live-trading
endpoints. The HTML UI is live-only (paper dashboards redirect to /live/dashboard).
Withdrawals remain disabled / non-automatic.
"""

from __future__ import annotations

import logging
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
from bot.core.disk_guard import disk_guard_status, log_disk_guard
from bot.core.enums import ExecutionMode, KillSwitchState, OpportunityLifecycleStatus
from bot.market_data.cache import MarketDataCache
from bot.market_data.service import MarketDataService
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
from bot.live.dashboard_history import (
    chart_series_from_history,
    enrich_session_from_bridge,
    load_history,
    metrics_from_payload,
    record_snapshot,
)
from bot.live.dashboard import render_live_dashboard
from bot.live.pwa_assets import ICON_SVG, MANIFEST_JSON, SERVICE_WORKER_JS
from bot.live.production_flags import PRODUCTION_EXECUTION_ENABLED
from bot.market_data.research.retention import prune_research_marketdata
from bot.live.service import get_live_service, reset_live_service
from bot.live.micro_engine import get_micro_engine, reset_micro_engine
from bot.live.micro_session_manager import (
    get_micro_session_manager,
    reset_micro_session_manager,
)
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
logger = logging.getLogger(__name__)


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
    reset_micro_session_manager()


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
        next_path = request.url.path or "/live/dashboard"
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
    log_disk_guard(
        "/",
        warn_pct=float(settings.disk_guard_warn_pct),
        block_pct=float(settings.disk_guard_block_pct),
    )
    prune_research_marketdata(
        settings.research_marketdata_recording_path,
        retention_days=int(settings.marketdata_retention_days),
        execute_delete=True,
    )
    get_settings.cache_clear()
    get_kill_switch()
    get_micro_engine().arm()
    md = get_market_data_service()
    paper_runner = None
    # Paper lab instances: auto-start PaperRunner only (never live orders).
    if settings.paper_trading_enabled and settings.paper_auto_start:
        paper_runner = get_paper_runner()
        await paper_runner.start()
        logger.info(
            "paper auto-start enabled persist=%s port=%s",
            settings.paper_persist_path,
            settings.api_port,
        )
    # Live micro: resume continuous session after uvicorn restart. Skip on
    # pure paper lab processes so they never touch live micro state.
    elif bool(settings.live_micro_enabled or settings.live_trading_enabled):
        try:
            resume = await get_micro_session_manager().resume_if_interrupted()
            if resume and resume.get("started"):
                logger.info("auto-resumed continuous micro session after process start")
            elif resume and not resume.get("started"):
                logger.warning("micro session auto-resume did not start: %s", resume)
        except Exception:  # noqa: BLE001
            logger.exception("failed to auto-resume interrupted micro session")
    yield
    if paper_runner is not None:
        try:
            await paper_runner.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("paper runner shutdown failed")
    await md.stop()


app = FastAPI(
    title="Moreney Trading System",
    description=(
        "Production-oriented cryptocurrency trading API. "
        "Strategies emit opportunities; profitability and risk gate execution. "
        "No withdrawal functionality is exposed. No leverage in this version. "
        "Live micro (:8020) and isolated paper lab instances share this codebase."
    ),
    version=__version__,
    lifespan=lifespan,
)

from bot.paper.api import router as paper_api_router  # noqa: E402

app.include_router(paper_api_router)

@app.exception_handler(DashboardLoginRedirect)
async def _dashboard_login_redirect(_request: Request, exc: DashboardLoginRedirect):
    return login_redirect(exc.next_path)



@app.get("/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    disk = disk_guard_status(
        "/",
        warn_pct=float(settings.disk_guard_warn_pct),
        block_pct=float(settings.disk_guard_block_pct),
    )
    return {
        "status": "ok" if not disk["blocked"] else "degraded",
        "version": __version__,
        "disk": disk,
    }


@app.get("/status")
async def status() -> dict[str, Any]:
    settings: Settings = get_settings()
    ks = get_kill_switch().status()
    funding_flags = get_funding_service().public_status_flags()
    micro = get_micro_session_manager().status()
    paper_running = False
    if settings.paper_trading_enabled:
        try:
            paper_running = bool(get_paper_runner().running)
        except Exception:  # noqa: BLE001
            paper_running = False
    return {
        "version": __version__,
        "environment": settings.app_env,
        "execution_mode": settings.execution_mode.value,
        "exchange": settings.exchange_name,
        "paper_mode": settings.execution_mode == ExecutionMode.PAPER,
        "paper_trading_enabled": bool(settings.paper_trading_enabled),
        "paper_running": paper_running,
        "paper_persist_path": settings.paper_persist_path,
        "api_port": settings.api_port,
        "micro_session_running": bool(micro.get("running")),
        "market_data_mode": settings.market_data_mode,
        "live_trading_enabled": bool(settings.live_trading_enabled or settings.live_micro_enabled),
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
        "production_execution_enabled": bool(PRODUCTION_EXECUTION_ENABLED),
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


@app.get("/live/micro/session")
async def live_micro_session_status() -> dict[str, Any]:
    """Live status of the full-bot micro session (budget-capped PaperRunner)."""
    return get_micro_session_manager().status()


@app.post("/live/micro/session/start")
async def live_micro_session_start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start a full-bot micro session in the background (continuous by default)."""
    body = payload or {}
    raw_minutes = body.get("minutes", None)
    if raw_minutes in (None, "", "continuous", "forever"):
        minutes: float | None = None
    else:
        minutes = float(raw_minutes)
        if minutes <= 0:
            minutes = None
    budget = float(body.get("budget_eur") or 2000)
    exclude_btc = body.get("exclude_btc", False)
    if isinstance(exclude_btc, str):
        exclude_btc = exclude_btc.strip().lower() not in {"0", "false", "no"}
    symbols_raw = body.get("symbols")
    symbols = None
    if isinstance(symbols_raw, str) and symbols_raw.strip():
        symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
    elif isinstance(symbols_raw, list):
        symbols = [str(s).strip().upper() for s in symbols_raw if str(s).strip()]
    return await get_micro_session_manager().start(
        minutes=minutes,
        budget_eur=budget,
        exclude_btc=bool(exclude_btc),
        symbols=symbols,
    )


@app.post("/live/micro/session/stop")
async def live_micro_session_stop() -> dict[str, Any]:
    """Request stop of the running full-bot micro session."""
    return await get_micro_session_manager().stop()


@app.post("/live/micro/session/reset-dashboard")
async def live_micro_session_reset_dashboard() -> dict[str, Any]:
    """Zero cumulative realized PnL and clear dashboard chart history."""
    return await get_micro_session_manager().reset_dashboard()


@app.post("/live/dashboard/reconcile")
async def live_dashboard_reconcile(
    since: str | None = Query(
        default=None,
        description="ISO timestamp; default 12:00 UTC today",
    ),
) -> dict[str, Any]:
    """Rebuild dashboard KPIs and chart history from exchange fills since ``since``."""
    from datetime import UTC, datetime, timedelta

    mgr = get_micro_session_manager()
    if since:
        since = since.strip().replace(" ", "+")
        try:
            anchor = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid since: {exc}") from exc
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)
    else:
        now = datetime.now(UTC)
        anchor = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if now < anchor:
            anchor = anchor - timedelta(days=1)
    return await mgr.reconcile_dashboard(anchor)


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


async def _live_dashboard_payload(
    *,
    record: bool = True,
    light: bool = True,
    include_history: bool = True,
) -> dict[str, Any]:
    live = get_live_service()
    mgr = get_micro_session_manager()
    session = mgr.status()
    bridge = mgr._bridge_holder.get("bridge")  # noqa: SLF001
    if bridge is not None:
        session = enrich_session_from_bridge(session, bridge)
    elif session.get("bridge") is None:
        # Stale status file after restart — avoid empty KPI sections.
        session = dict(session)
        session.setdefault("bridge", {})
    if bridge is not None:
        try:
            from bot.live.dashboard_pnl import attach_calendar_pnl, schedule_calendar_pnl_refresh

            schedule_calendar_pnl_refresh(bridge)
            session = attach_calendar_pnl(session)
        except Exception:  # noqa: BLE001
            logger.exception("calendar PnL attach on dashboard payload failed")
    payload: dict[str, Any] = {
        "session": session,
        "engine": get_micro_engine().status(),
    }
    if not light:
        try:
            readiness = live.compact_status()
            payload["readiness"] = readiness
        except Exception:  # noqa: BLE001
            payload["readiness"] = {}
        payload["unlock"] = live.micro_unlock_checklist()
    if record:
        record_snapshot(payload)
    if include_history:
        payload["history"] = load_history(limit=720)
    return payload


@app.get("/login", response_class=HTMLResponse)
async def login_page(next: str = Query(default="/live/dashboard")) -> HTMLResponse:
    settings = get_settings()
    if not settings.dashboard_basic_auth_enabled:
        return RedirectResponse(
            url=next if next.startswith("/") else "/live/dashboard", status_code=303
        )
    return render_login_page(next_path=next)


@app.post("/login")
async def login_submit(
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/live/dashboard"),
) -> Response:
    settings = get_settings()
    if not settings.dashboard_basic_auth_enabled:
        return RedirectResponse(
            url=next if next.startswith("/") else "/live/dashboard", status_code=303
        )
    if not credentials_valid(settings, username, password):
        return render_login_page(next_path=next, error="Invalid username or password")
    safe_next = next if next.startswith("/") else "/live/dashboard"
    response = RedirectResponse(url=safe_next, status_code=303)
    set_session_cookie(response, settings, username)
    return response


@app.post("/logout")
@app.get("/logout")
async def logout() -> Response:
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    return response


@app.get("/", response_class=HTMLResponse, response_model=None)
@app.get("/live/dashboard", response_class=HTMLResponse, response_model=None)
@app.get("/dashboard", response_class=HTMLResponse, response_model=None)
async def live_dashboard(_: None = Depends(require_dashboard_access)) -> HTMLResponse | RedirectResponse:
    """Live operator dashboard; paper lab instances redirect to the simple lab UI."""
    settings = get_settings()
    if settings.execution_mode == ExecutionMode.PAPER and settings.paper_trading_enabled:
        return RedirectResponse(url="/paper/dashboard", status_code=303)
    return render_live_dashboard(await _live_dashboard_payload())


@app.get("/live/micro/dashboard", response_class=HTMLResponse, response_model=None)
async def live_micro_dashboard_redirect() -> RedirectResponse:
    """Legacy URL — single operator dashboard lives at /live/dashboard."""
    return RedirectResponse(url="/live/dashboard", status_code=301)


@app.get("/live/dashboard/metrics")
async def live_dashboard_metrics(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    """JSON KPIs for mobile polling (no full HTML reload)."""
    payload = await _live_dashboard_payload(light=True, record=False, include_history=False)
    return {"metrics": metrics_from_payload(payload)}


@app.get("/live/dashboard/charts")
async def live_dashboard_charts(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    """Chart series only — polled less often than KPI metrics."""
    history = load_history(limit=720)
    return {
        "history": chart_series_from_history(history),
        "version": history[-1].get("t") if history else None,
    }


@app.get("/live/dashboard/history")
async def live_dashboard_history(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    return {"points": load_history(limit=720)}


@app.get("/live/manifest.webmanifest")
async def live_pwa_manifest() -> Response:
    return Response(content=MANIFEST_JSON, media_type="application/manifest+json")


@app.get("/live/sw.js")
async def live_service_worker() -> Response:
    return Response(content=SERVICE_WORKER_JS, media_type="application/javascript")


@app.get("/live/icon.svg")
async def live_pwa_icon() -> Response:
    return Response(content=ICON_SVG, media_type="image/svg+xml")


@app.get("/fleet", response_class=HTMLResponse)
@app.get("/strategy-lab", response_class=HTMLResponse)
@app.get("/lab", response_class=HTMLResponse)
async def legacy_research_dashboards_redirect() -> RedirectResponse:
    """Research HTML surfaces redirect to the live operator dashboard."""
    return RedirectResponse(url="/live/dashboard", status_code=303)


@app.get("/paper/dashboard", response_class=HTMLResponse, response_model=None)
@app.get("/paper/dashboard-lite", response_class=HTMLResponse, response_model=None)
async def paper_dashboard(
    request: Request,
    _: None = Depends(require_dashboard_access),
) -> HTMLResponse | RedirectResponse:
    """Simple Paper Lab UI (params + status). Live process redirects away."""
    del request  # path handled by dual route registration
    settings = get_settings()
    if not (
        settings.execution_mode == ExecutionMode.PAPER and settings.paper_trading_enabled
    ):
        return RedirectResponse(url="/live/dashboard", status_code=303)
    from bot.paper.lab_dashboard import render_lab_dashboard

    runner = get_paper_runner()
    return render_lab_dashboard(
        settings=settings,
        status=runner.status(),
        performance=runner.tracker.snapshot().model_dump(mode="json"),
    )


@app.get("/paper/lab/params")
async def paper_lab_params(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    settings = get_settings()
    if not (
        settings.execution_mode == ExecutionMode.PAPER and settings.paper_trading_enabled
    ):
        raise HTTPException(status_code=403, detail="Paper lab only")
    from bot.paper.lab_dashboard import lab_params_payload

    return lab_params_payload(settings)


@app.get("/strategy-lab/api")
@app.get("/lab/api")
async def strategy_lab_api(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="Strategy lab removed from live bot")


@app.get("/fleet/api")
async def fleet_api(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="Fleet/paper overview removed — use /live/dashboard")



@app.post("/fleet/reset")
async def fleet_reset(
    confirm: bool = Form(False),
    restart: bool = Form(False),
    _: None = Depends(require_dashboard_access),
) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="Fleet reset removed — live accounts untouched")



@app.get("/risk/kill-switch")
async def kill_switch_status() -> dict[str, Any]:
    return get_kill_switch().status().model_dump(mode="json")


@app.get("/risk/events")
async def risk_events() -> dict[str, Any]:
    return {
        "events": [event.model_dump(mode="json") for event in _event_store.events],
    }


@app.post("/risk/kill-switch/recover")
async def kill_switch_recover(force: bool = False) -> dict[str, Any]:
    ks = get_kill_switch()
    recovered = await ks.recover(force=force)
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
    # Also request micro-session stop so resting work winds down.
    session_stop: dict[str, Any] | None = None
    try:
        from bot.live.micro_session_manager import get_micro_session_manager

        mgr = get_micro_session_manager()
        session_stop = await mgr.stop()
    except Exception as exc:  # noqa: BLE001
        session_stop = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": status.model_dump(mode="json"),
        "micro_session_stop": session_stop,
    }


@app.post("/integrations/alphai/webhook")
async def alphai_webhook(request: Request) -> dict[str, Any]:
    """Ingest AlphaI Pro push articles (HMAC verified)."""
    settings = get_settings()
    if not getattr(settings, "alphai_enabled", False):
        raise HTTPException(status_code=404, detail="AlphaI integration disabled")
    body = await request.body()
    secret_raw = getattr(settings, "alphai_webhook_secret", None)
    secret = (
        secret_raw.get_secret_value()
        if secret_raw is not None and hasattr(secret_raw, "get_secret_value")
        else (str(secret_raw).strip() if secret_raw else "")
    )
    if not secret:
        raise HTTPException(status_code=503, detail="ALPHAI_WEBHOOK_SECRET not configured")
    from bot.integrations.alphai.webhook import verify_webhook_signature

    sig = request.headers.get("X-Alphai-Signature") or request.headers.get(
        "x-alphai-signature"
    )
    if not verify_webhook_signature(secret, sig, body):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    try:
        import json

        payload = json.loads(body.decode("utf-8") if body else "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    article = payload.get("article") if isinstance(payload, dict) else None
    if not isinstance(article, dict):
        raise HTTPException(status_code=400, detail="expected { \"article\": {...} }")
    from bot.integrations.alphai.pending import push_webhook_article

    push_webhook_article(article)
    try:
        runner = get_paper_runner()
        if runner.running:
            return {"ok": True, **runner.ingest_alphai_article(article)}
    except Exception:  # noqa: BLE001
        logger.exception("alphai webhook immediate ingest failed")
    return {"ok": True, "queued": True}


@app.get("/integrations/alphai/status")
async def alphai_status(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    """AlphaI monitor snapshot (runner + live bridge blocks)."""
    settings = get_settings()
    if not getattr(settings, "alphai_enabled", False):
        return {"enabled": False}
    mgr = get_micro_session_manager()
    session = mgr.status()
    bridge_snap = (session.get("bridge") or {}) if isinstance(session.get("bridge"), dict) else {}
    bridge = mgr._bridge_holder.get("bridge")  # noqa: SLF001
    if bridge is not None:
        try:
            bridge_snap = bridge.snapshot_bridge()
        except Exception:  # noqa: BLE001
            logger.exception("alphai status bridge snapshot failed")
    from bot.integrations.alphai.status import merge_alphai_status

    merged = merge_alphai_status(session, bridge_snap)
    out: dict[str, Any] = {"enabled": True, **merged}
    try:
        runner = get_paper_runner()
        if getattr(runner, "_alphai_monitor", None) is not None:
            out["monitor"] = runner._alphai_monitor.snapshot()
    except Exception:  # noqa: BLE001
        pass
    import os

    out["api_key_configured"] = bool(
        getattr(settings, "alphai_api_key", None) or os.environ.get("ALPHAI_API_KEY")
    )
    return out


@app.get("/integrations/alphai/recommendations/daily")
async def alphai_daily_recommendations(
    _: None = Depends(require_dashboard_access),
) -> dict[str, Any]:
    """Daily buy picks for the current 12:00–12:00 Europe/Amsterdam window."""
    settings = get_settings()
    from bot.integrations.alphai.daily_recommendations import load_daily_recommendations

    path = getattr(
        settings,
        "alphai_daily_recommendations_path",
        "data/alphai/daily_recommendations.json",
    )
    report = load_daily_recommendations(path)
    if report:
        return {"ok": True, **report}
    return {
        "ok": False,
        "message": "No daily recommendations yet — refresh after 12:00 NL or POST /integrations/alphai/recommendations/refresh",
    }


@app.post("/integrations/alphai/recommendations/refresh")
async def alphai_daily_recommendations_refresh(
    force: bool = Query(default=True),
    _: None = Depends(require_dashboard_access),
) -> dict[str, Any]:
    """Generate/refresh daily crypto picks (normally automatic at 12:00 NL)."""
    settings = get_settings()
    if not getattr(settings, "alphai_enabled", False):
        raise HTTPException(status_code=404, detail="AlphaI integration disabled")
    monitor = None
    try:
        runner = get_paper_runner()
        monitor = getattr(runner, "_alphai_monitor", None)
    except Exception:  # noqa: BLE001
        pass
    if monitor is not None:
        report = await monitor.maybe_refresh_daily_picks(force=force)
        if report:
            return {"ok": True, **report}
    import os

    from bot.integrations.alphai.client import AlphaIClient
    from bot.integrations.alphai.daily_recommendations import maybe_refresh_daily
    from bot.integrations.alphai.regime import _parse_csv_bases as parse_bases
    from bot.integrations.alphai.symbols import LIQUID_EUR_BASES

    key = getattr(settings, "alphai_api_key", None)
    secret = key.get_secret_value() if key is not None else os.environ.get("ALPHAI_API_KEY", "")
    if not secret:
        raise HTTPException(status_code=503, detail="ALPHAI_API_KEY not configured")
    focus = parse_bases(
        getattr(settings, "live_micro_focus_bases", "") or "",
        fallback=set(LIQUID_EUR_BASES),
    )
    client = AlphaIClient(str(secret))
    path = getattr(
        settings,
        "alphai_daily_recommendations_path",
        "data/alphai/daily_recommendations.json",
    )
    report = maybe_refresh_daily(
        client,
        path,
        focus_bases=focus,
        enabled=True,
        min_relevance=int(
            getattr(settings, "alphai_daily_recommendations_min_relevance", 6) or 6
        ),
        top_n=int(getattr(settings, "alphai_daily_recommendations_top_n", 8) or 8),
        update_hour_local=int(
            getattr(settings, "alphai_daily_recommendations_hour", 12) or 12
        ),
        interval_hours=int(
            getattr(settings, "alphai_recommendations_interval_hours", 1) or 1
        ),
        force=force,
    )
    if not report:
        raise HTTPException(status_code=500, detail="Failed to generate recommendations")
    return {"ok": True, **report}


@app.get("/api")
async def api_root() -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "name": "Moreney",
            "message": "Trading API. Live dashboard at /live/dashboard",
            "docs": "/docs",
            "live_dashboard": "/live/dashboard",
            "live_micro_session": "/live/micro/session",
            "dashboard_basic_auth_enabled": _dashboard_auth_enabled(),
            "execution_mode": settings.execution_mode.value
            if hasattr(settings.execution_mode, "value")
            else str(settings.execution_mode),
            "live_trading_enabled": bool(settings.live_trading_enabled),
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

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
from bot.paper.fleet import collect_fleet_overview
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
from bot.paper.store import PaperTradingStore
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
        "leverage_supported": False,
        "kill_switch": ks.model_dump(mode="json"),
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
    return get_paper_runner().status()


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


@app.get("/fleet", response_class=HTMLResponse)
async def fleet_dashboard(_: None = Depends(require_dashboard_access)) -> HTMLResponse:
    """One page covering all configured paper instances."""
    payload = await collect_fleet_overview(get_settings())
    return render_fleet_dashboard(payload)


@app.get("/fleet/api")
async def fleet_api(_: None = Depends(require_dashboard_access)) -> dict[str, Any]:
    return await collect_fleet_overview(get_settings())

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
    return render_dashboard(
        {
            "status": runner.status(),
            "performance": snap.model_dump(mode="json"),
            "strategies": strategies,
            "exchanges": exchanges,
            "opportunities": opportunities,
            "hourly": hourly,
            "trades": trades,
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

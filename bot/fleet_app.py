"""Lightweight fleet dashboard app (no trading / no market-data sockets)."""

from __future__ import annotations

from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from bot.core.config import get_settings
from bot.main import require_dashboard_access
from bot.paper.dashboard import render_fleet_dashboard
from bot.paper.fleet import collect_fleet_overview, publicize_instance_urls, reset_fleet

app = FastAPI(
    title="Moreney Fleet Dashboard",
    description="Aggregated paper-trading view across all instances.",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "fleet"}


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


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/fleet", response_class=HTMLResponse)
async def fleet_dashboard(
    request: Request, _: None = Depends(require_dashboard_access)
) -> HTMLResponse:
    payload = publicize_instance_urls(
        await collect_fleet_overview(get_settings()),
        hostname=request.url.hostname or request.headers.get("host", ""),
        scheme=request.url.scheme,
    )
    return render_fleet_dashboard(payload)


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "bot.fleet_app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()

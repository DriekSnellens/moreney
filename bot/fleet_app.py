"""Lightweight fleet dashboard app (no trading / no market-data sockets)."""

from __future__ import annotations

from typing import Any

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from bot.core.config import get_settings
from bot.main import require_dashboard_access
from bot.paper.dashboard import render_fleet_dashboard
from bot.paper.fleet import collect_fleet_overview

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


@app.get("/", response_class=HTMLResponse)
@app.get("/fleet", response_class=HTMLResponse)
async def fleet_dashboard(_: None = Depends(require_dashboard_access)) -> HTMLResponse:
    payload = await collect_fleet_overview(get_settings())
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

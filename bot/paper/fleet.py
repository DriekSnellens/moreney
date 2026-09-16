"""Aggregate live paper overviews from multiple Moreney instances."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from bot.core.config import Settings

logger = logging.getLogger(__name__)


def fleet_endpoints(settings: Settings) -> list[tuple[str, str]]:
    """Return (label, base_url) pairs for configured paper instances."""
    raw_urls = [
        u.strip().rstrip("/")
        for u in settings.paper_fleet_urls.split(",")
        if u.strip()
    ]
    raw_labels = [
        label.strip()
        for label in settings.paper_fleet_labels.split(",")
        if label.strip()
    ]
    pairs: list[tuple[str, str]] = []
    for idx, url in enumerate(raw_urls):
        if idx < len(raw_labels):
            label = raw_labels[idx]
        else:
            parsed = urlparse(url)
            label = parsed.netloc or url
        pairs.append((label, url))
    return pairs


async def _fetch_one(
    client: httpx.AsyncClient,
    *,
    label: str,
    base_url: str,
) -> dict[str, Any]:
    overview_url = f"{base_url}/paper/overview"
    try:
        response = await client.get(overview_url)
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status") or {}
        portfolio = payload.get("portfolio") or {}
        performance = payload.get("performance") or {}
        market_data = payload.get("market_data") or status.get("market_data") or {}
        starting = (
            portfolio.get("starting_capital")
            or status.get("starting_equity")
            or performance.get("starting_equity")
        )
        return {
            "ok": True,
            "label": label,
            "base_url": base_url,
            "dashboard_url": f"{base_url}/paper/dashboard",
            "dashboard_lite_url": f"{base_url}/paper/dashboard-lite",
            "starting_capital": starting,
            "equity": portfolio.get("equity") or status.get("current_equity"),
            "net_pnl": performance.get("net_pnl") or status.get("net_pnl"),
            "running": bool(status.get("running")),
            "cycle_count": status.get("cycle_count"),
            "trade_count": performance.get("trade_count") or status.get("trade_count"),
            "total_opportunities": performance.get("total_opportunities")
            or status.get("total_opportunities"),
            "approved_opportunities": performance.get("approved_opportunities")
            or status.get("approved_opportunities"),
            "executed_opportunities": performance.get("executed_opportunities")
            or status.get("executed_opportunities"),
            "pairs_evaluated": performance.get("pairs_evaluated")
            or status.get("pairs_evaluated")
            or 0,
            "depth_edges_found": performance.get("depth_edges_found")
            or status.get("depth_edges_found")
            or 0,
            "scan_rejections": performance.get("scan_rejections")
            or status.get("scan_rejections")
            or 0,
            "reject_counts": status.get("reject_counts") or {},
            "win_rate": performance.get("win_rate"),
            "maximum_drawdown": performance.get("maximum_drawdown"),
            "runtime_seconds": status.get("runtime_seconds"),
            "open_maker_quotes": status.get("open_maker_quotes") or 0,
            "fee_tier": status.get("fee_tier") or "retail",
            "strategy": status.get("strategy"),
            "market_data": market_data,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — surface per-instance failures in UI
        logger.info("FLEET_FETCH_FAILED label=%s url=%s error=%s", label, base_url, type(exc).__name__)
        return {
            "ok": False,
            "label": label,
            "base_url": base_url,
            "dashboard_url": f"{base_url}/paper/dashboard",
            "dashboard_lite_url": f"{base_url}/paper/dashboard-lite",
            "error": f"{type(exc).__name__}: {exc}",
        }


async def collect_fleet_overview(settings: Settings) -> dict[str, Any]:
    """Fetch overview payloads for every configured paper instance."""
    endpoints = fleet_endpoints(settings)
    timeout = httpx.Timeout(2.5, connect=1.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        rows = await asyncio.gather(
            *[
                _fetch_one(client, label=label, base_url=url)
                for label, url in endpoints
            ]
        )
    instances = list(rows)
    online = [row for row in instances if row.get("ok")]
    live_readiness: dict[str, Any] = {
        "available": False,
        "note": "Fleet aggregator; see hub /live/readiness for full report",
    }
    try:
        from bot.live.service import get_live_service

        live_readiness = {
            "available": True,
            **get_live_service().compact_status(),
        }
    except Exception as exc:  # noqa: BLE001
        live_readiness = {
            "available": False,
            "error": type(exc).__name__,
            "withdrawals_supported": False,
        }
    return {
        "instances": instances,
        "online_count": len(online),
        "configured_count": len(endpoints),
        "totals": _totals(online),
        "live_readiness": live_readiness,
    }


async def reset_fleet(
    settings: Settings,
    *,
    confirm: bool,
    restart: bool = True,
) -> dict[str, Any]:
    """Reset every configured paper instance. Never touches live exchange accounts.

    Posts ``/paper/reset`` on each bot, then optionally ``/paper/start`` so the
    fleet comes back online clean (matching PAPER_AUTO_START behaviour).
    """
    if not confirm:
        return {
            "reset": False,
            "reason": "confirmation_required",
            "message": 'POST /fleet/reset requires {"confirm": true}',
            "results": [],
        }
    endpoints = fleet_endpoints(settings)
    timeout = httpx.Timeout(30.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *[
                _reset_one(client, label=label, base_url=url, restart=restart)
                for label, url in endpoints
            ]
        )
    rows = list(results)
    ok_count = sum(1 for row in rows if row.get("ok"))
    return {
        "reset": ok_count == len(rows) and len(rows) > 0,
        "confirmed": True,
        "restart": restart,
        "configured_count": len(endpoints),
        "ok_count": ok_count,
        "real_exchange_accounts_affected": False,
        "results": rows,
    }


async def _reset_one(
    client: httpx.AsyncClient,
    *,
    label: str,
    base_url: str,
    restart: bool,
) -> dict[str, Any]:
    reset_url = f"{base_url}/paper/reset"
    try:
        response = await client.post(reset_url, json={"confirm": True})
        response.raise_for_status()
        payload = response.json()
        if not payload.get("reset"):
            return {
                "ok": False,
                "label": label,
                "base_url": base_url,
                "reset": False,
                "restarted": False,
                "error": payload.get("message") or payload.get("reason") or "reset refused",
            }
        restarted = False
        start_error = None
        if restart:
            try:
                start = await client.post(f"{base_url}/paper/start", json={})
                start.raise_for_status()
                restarted = bool(start.json().get("started") or start.json().get("running"))
            except Exception as exc:  # noqa: BLE001
                start_error = f"{type(exc).__name__}: {exc}"
                logger.info(
                    "FLEET_RESTART_FAILED label=%s url=%s error=%s",
                    label,
                    base_url,
                    type(exc).__name__,
                )
        return {
            "ok": True,
            "label": label,
            "base_url": base_url,
            "reset": True,
            "restarted": restarted,
            "starting_equity": payload.get("starting_equity"),
            "error": start_error,
        }
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "FLEET_RESET_FAILED label=%s url=%s error=%s",
            label,
            base_url,
            type(exc).__name__,
        )
        return {
            "ok": False,
            "label": label,
            "base_url": base_url,
            "reset": False,
            "restarted": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _totals(online: list[dict[str, Any]]) -> dict[str, str]:
    equity = 0.0
    pnl = 0.0
    trades = 0
    opps = 0
    evaluated = 0
    edges = 0
    scan_rej = 0
    for row in online:
        equity += _as_float(row.get("equity"))
        pnl += _as_float(row.get("net_pnl"))
        trades += int(_as_float(row.get("trade_count")))
        opps += int(_as_float(row.get("total_opportunities")))
        evaluated += int(_as_float(row.get("pairs_evaluated")))
        edges += int(_as_float(row.get("depth_edges_found")))
        scan_rej += int(_as_float(row.get("scan_rejections")))
    return {
        "equity": f"{equity:.8g}",
        "net_pnl": f"{pnl:.8g}",
        "trade_count": str(trades),
        "total_opportunities": str(opps),
        "pairs_evaluated": str(evaluated),
        "depth_edges_found": str(edges),
        "scan_rejections": str(scan_rej),
        "running_count": str(sum(1 for row in online if row.get("running"))),
        "open_maker_quotes": str(
            sum(int(_as_float(row.get("open_maker_quotes"))) for row in online)
        ),
    }


def publicize_instance_urls(
    payload: dict[str, Any],
    *,
    hostname: str,
    scheme: str,
) -> dict[str, Any]:
    """Rewrite per-bot dashboard links to the hostname the browser used."""
    host = (hostname or "").split(":")[0].strip() or "127.0.0.1"
    scheme = (scheme or "http").split(":")[0]
    instances: list[dict[str, Any]] = []
    for row in payload.get("instances") or []:
        item = dict(row)
        parsed = urlparse(str(item.get("base_url") or ""))
        port = parsed.port
        if port:
            public = f"{scheme}://{host}:{port}"
            item["dashboard_url"] = f"{public}/paper/dashboard"
            item["dashboard_lite_url"] = f"{public}/paper/dashboard-lite"
        instances.append(item)
    out = dict(payload)
    out["instances"] = instances
    return out


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

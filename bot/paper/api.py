"""Paper trading HTTP API (isolated paper processes only).

Live micro on :8020 keeps PAPER_TRADING_ENABLED=false / PAPER_AUTO_START=false.
Paper lab instances enable these flags and call into PaperRunner only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from bot.core.config import get_settings
from bot.core.enums import ExecutionMode, OpportunityLifecycleStatus
from bot.opportunity.parameter_log import PARAMETER_CHANGES

router = APIRouter(tags=["paper"])


def _runner():
    from bot.main import get_paper_runner

    return get_paper_runner()


def _require_paper_mode() -> None:
    settings = get_settings()
    if settings.execution_mode != ExecutionMode.PAPER:
        raise HTTPException(
            status_code=403,
            detail="Paper API refused: EXECUTION_MODE is not paper",
        )
    if not settings.paper_trading_enabled:
        raise HTTPException(status_code=403, detail="PAPER_TRADING_ENABLED=false")


@router.get("/paper/last-cycle")
async def paper_last_cycle() -> dict[str, Any]:
    _require_paper_mode()
    from bot.main import _last_paper_cycle

    runner = _runner()
    cycle = runner.last_cycle if runner.last_cycle is not None else _last_paper_cycle
    if cycle is None:
        return {"available": False, "cycle": None}
    return {"available": True, "cycle": cycle}


@router.get("/paper/status")
async def paper_status() -> dict[str, Any]:
    _require_paper_mode()
    from bot.live.service import get_live_service

    status = _runner().status()
    status["live_readiness"] = get_live_service().compact_status()
    return status


@router.get("/paper/portfolio")
async def paper_portfolio() -> dict[str, Any]:
    _require_paper_mode()
    portfolio = _runner().portfolio
    state = portfolio.state
    return {
        "quote_asset": state.quote_asset,
        "starting_capital": str(get_settings().paper_starting_eur),
        "equity": str(state.total_equity),
        "balances": {
            k: {
                "available": str(v.available),
                "reserved": str(v.reserved),
                "total": str(v.total),
            }
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


@router.get("/paper/performance")
async def paper_performance() -> dict[str, Any]:
    _require_paper_mode()
    return _runner().tracker.snapshot().model_dump(mode="json")


@router.get("/paper/overview")
async def paper_overview() -> dict[str, Any]:
    _require_paper_mode()
    from bot.main import get_market_data_service

    runner = _runner()
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


@router.get("/paper/statistics/daily")
async def paper_statistics_daily() -> dict[str, Any]:
    _require_paper_mode()
    rows = _runner().tracker.daily_stats()
    return {"daily": [r.model_dump(mode="json") for r in rows]}


@router.get("/paper/statistics/strategies")
async def paper_statistics_strategies() -> dict[str, Any]:
    _require_paper_mode()
    rows = _runner().tracker.strategy_stats()
    payload = []
    for r in rows:
        item = r.model_dump(mode="json")
        item["win_rate"] = str(r.win_rate)
        item["profit_factor"] = str(r.profit_factor)
        payload.append(item)
    return {"strategies": payload}


@router.get("/paper/statistics/exchanges")
async def paper_statistics_exchanges() -> dict[str, Any]:
    _require_paper_mode()
    rows = _runner().tracker.exchange_pair_stats()
    payload = []
    for r in rows:
        item = r.model_dump(mode="json")
        item["pair"] = r.pair_key
        item["win_rate"] = str(r.win_rate)
        payload.append(item)
    return {"exchanges": payload}


@router.get("/paper/statistics/hourly")
async def paper_statistics_hourly() -> dict[str, Any]:
    _require_paper_mode()
    rows = _runner().tracker.hourly_stats()
    payload = []
    for r in rows:
        item = r.model_dump(mode="json")
        item["label"] = r.label
        item["average_net_pnl"] = str(r.average_net_pnl)
        item["win_rate"] = str(r.win_rate)
        payload.append(item)
    return {"hourly": payload}


@router.get("/paper/opportunities")
async def paper_opportunities(
    limit: int = Query(default=100, ge=1, le=1000),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    _require_paper_mode()
    status_enum: OpportunityLifecycleStatus | None = None
    if status:
        try:
            status_enum = OpportunityLifecycleStatus(status.lower())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}") from exc
    rows = _runner().tracker.opportunities(limit=limit, status=status_enum)
    return {"opportunities": [r.model_dump(mode="json") for r in rows]}


@router.get("/paper/opportunity-decisions")
async def paper_opportunity_decisions(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    _require_paper_mode()
    runner = _runner()
    log = getattr(runner, "_decision_log", None)
    if log is None:
        return {"decisions": []}
    return {"decisions": log.export()[-limit:]}


@router.get("/paper/why-not-trade")
async def paper_why_not_trade() -> dict[str, Any]:
    _require_paper_mode()
    runner = _runner()
    missed = getattr(runner, "_missed", None)
    kpis = (runner.status() or {}).get("net_kpis") or {}
    cal = getattr(runner, "_calibrator", None)
    return {
        "why_not_trade": missed.why_not_trade() if missed is not None else {},
        "net_kpis": kpis,
        "ev_calibration": cal.snapshot() if cal is not None else {},
        "parameter_changes": PARAMETER_CHANGES,
    }


@router.get("/paper/trades")
async def paper_trades(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    _require_paper_mode()
    return {"trades": _runner().tracker.trades(limit=limit)}


@router.post("/paper/start")
async def paper_start() -> dict[str, Any]:
    _require_paper_mode()
    return await _runner().start()


@router.post("/paper/stop")
async def paper_stop() -> dict[str, Any]:
    _require_paper_mode()
    return await _runner().stop()


@router.post("/paper/reset")
async def paper_reset(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reset paper portfolio only. Never affects real exchange accounts."""
    _require_paper_mode()
    confirm = bool((payload or {}).get("confirm"))
    result = await _runner().reset(confirm=confirm)
    if not result.get("reset"):
        raise HTTPException(status_code=400, detail=result)
    return result

"""Stability reporting. Does not relax 70% thresholds."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bot.research.forensics.buckets import N_CHRONO_BLOCKS, chrono_block_id
from bot.research.tournament.criteria import MAX_TOP_ROUTE_PNL_SHARE, MAX_TOP_SYMBOL_PNL_SHARE


def _abs_edge(e: dict[str, Any]) -> float:
    return abs(float(e.get("forward") or 0.0))


def stability_report(
    events: list[dict[str, Any]],
    *,
    oos_start_ns: int | None = None,
    oos_end_ns: int | None = None,
) -> dict[str, Any]:
    if not events:
        return {
            "label": "EMPTY",
            "concentrated": True,
            "route_count": 0,
            "symbol_count": 0,
            "top_route_share": 1.0,
            "top_symbol_share": 1.0,
            "ROUTE_UNIVERSE_LIMITED": False,
            "positive_block_count": 0,
            "negative_block_count": 0,
            "stability_score": 0.0,
        }
    by_sym: dict[str, float] = defaultdict(float)
    by_route: dict[str, float] = defaultdict(float)
    by_block: dict[str, float] = defaultdict(float)
    start = oos_start_ns or min(int(e["ts_ns"]) for e in events if e.get("ts_ns") is not None)
    end = oos_end_ns or max(int(e["ts_ns"]) for e in events if e.get("ts_ns") is not None)
    for e in events:
        edge = _abs_edge(e)
        by_sym[str(e.get("symbol") or "?")] += edge
        by_route[str(e.get("route") or e.get("venue") or "?")] += edge
        ts = e.get("ts_ns")
        if ts is not None:
            by_block[chrono_block_id(int(ts), int(start), int(end))] += float(e.get("net") or e.get("forward") or 0.0)
    tot_s = sum(by_sym.values()) or 1.0
    tot_r = sum(by_route.values()) or 1.0
    tot_b = sum(abs(v) for v in by_block.values()) or 1.0
    top_s = max(by_sym.values()) / tot_s
    top_r = max(by_route.values()) / tot_r
    top_b = (max(abs(v) for v in by_block.values()) / tot_b) if by_block else 1.0
    n_routes = len(by_route)
    limited = n_routes <= 1
    symbol_fail = top_s > MAX_TOP_SYMBOL_PNL_SHARE
    route_fail = (not limited) and top_r > MAX_TOP_ROUTE_PNL_SHARE
    concentrated = symbol_fail or route_fail
    pos = sum(1 for i in range(1, N_CHRONO_BLOCKS + 1) if by_block.get(f"BLOCK_{i}", 0.0) > 0)
    neg = sum(1 for i in range(1, N_CHRONO_BLOCKS + 1) if by_block.get(f"BLOCK_{i}", 0.0) < 0)
    label = "CONCENTRATED_RESULT" if concentrated else "DIVERSIFIED"
    if limited:
        label = f"{label}|ROUTE_UNIVERSE_LIMITED"
    return {
        "label": label,
        "concentrated": concentrated,
        "route_count": n_routes,
        "symbol_count": len(by_sym),
        "top_route_share": top_r,
        "top_symbol_share": top_s,
        "top_block_share": top_b,
        "top_symbol": max(by_sym, key=by_sym.get) if by_sym else None,
        "top_route": max(by_route, key=by_route.get) if by_route else None,
        "ROUTE_UNIVERSE_LIMITED": limited,
        "positive_block_count": pos,
        "negative_block_count": neg,
        "stability_score": max(0.0, 1.0 - top_s if limited else 1.0 - max(top_s, top_r)),
        "max_top_symbol_share": MAX_TOP_SYMBOL_PNL_SHARE,
        "max_top_route_share": MAX_TOP_ROUTE_PNL_SHARE,
        "criteria_relaxed": False,
        "by_symbol": dict(by_sym),
        "by_route": dict(by_route),
        "by_block": dict(by_block),
    }

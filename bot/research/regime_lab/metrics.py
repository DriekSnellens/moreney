"""Research metrics. NET remains the only profitability metric."""

from __future__ import annotations

from typing import Any

from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    NOTIONAL_EUR_DEFAULT,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.economics import execution_replay_net, net_waterfall_from_edge, round_trip_fee_rate


def attach_event_economics(
    events: list[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
    horizon_ms: int,
) -> list[dict[str, Any]]:
    notional = float(NOTIONAL_EUR_DEFAULT)
    fee_rate = float(round_trip_fee_rate(venue, venue_exit))
    fees = notional * fee_rate
    slip = notional * (float(SLIPPAGE_BPS_DEFAULT) / 10000.0)
    adverse = notional * (float(ADVERSE_BPS_DEFAULT) / 10000.0)
    latency = notional * (float(LATENCY_PENALTY_BPS) / 10000.0)
    out = []
    for e in events:
        fwd = float(e.get("forward") or 0.0)
        gross = notional * fwd
        net = gross - fees - slip - adverse - latency
        row = dict(e)
        row.update(
            {
                "gross": gross,
                "fees": fees,
                "slippage": slip,
                "adverse": adverse,
                "latency": latency,
                "net": net,
                "capital_lock_ms": horizon_ms,
                "inventory_effect": 0.0,
            }
        )
        out.append(row)
    return out


def window_metrics(
    events: list[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
    mean_forward: float | None,
    horizon_ms: int,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    n = len(events)
    cand = int((audit or {}).get("candidates") or n)
    admitted = int((audit or {}).get("admitted") or n)
    gross = sum(float(e.get("gross") or 0.0) for e in events)
    fees = sum(float(e.get("fees") or 0.0) for e in events)
    slip = sum(float(e.get("slippage") or 0.0) for e in events)
    adverse = sum(float(e.get("adverse") or 0.0) for e in events)
    net = sum(float(e.get("net") or 0.0) for e in events)
    edge = abs(float(mean_forward or 0.0))
    wf = net_waterfall_from_edge(
        gross_edge_fraction=edge, venue=venue, venue_exit=venue_exit
    )
    wf["CAPITAL_LOCK_MS"] = horizon_ms
    wf["INVENTORY_EFFECT"] = 0.0
    expected_net = float(wf["EXPECTED_NET"])
    replay = execution_replay_net(expected_net=expected_net)
    exec_net = float(replay["EXECUTION_NET"])
    fills = max(1, int(round(n * float(replay["fill_rate"])))) if n else 0
    dd = _max_drawdown(events)
    net_per_fill = (exec_net / fills) if fills else None
    gross_bps = abs(edge) * 10000.0
    net_per_bps = (expected_net / gross_bps) if gross_bps else None
    ev_capture = (exec_net / expected_net) if expected_net else None
    return {
        "signals": n,
        "candidate_events": cand,
        "accepted_events": admitted,
        "completed_round_trips": fills,
        "gross": gross,
        "fees": fees,
        "slippage": slip,
        "adverse": adverse,
        "NET": net,
        "EXPECTED_NET": expected_net,
        "EXECUTION_NET": exec_net,
        "NET_per_fill": net_per_fill,
        "NET_per_bps": net_per_bps,
        "EV": expected_net,
        "EV_capture": ev_capture,
        "maximum_drawdown": dd,
        "waterfall": wf,
        "execution_replay": replay,
        "mean_forward": mean_forward,
        "note": (
            "Sum NET is descriptive per-event accounting. EXPECTED_NET is the "
            "tournament mean-edge waterfall (unchanged cost model)."
        ),
    }


def _max_drawdown(events: list[dict[str, Any]]) -> float:
    ordered = [e for e in events if e.get("ts_ns") is not None]
    ordered.sort(key=lambda e: int(e["ts_ns"]))
    peak = 0.0
    equity = 0.0
    dd = 0.0
    for e in ordered:
        equity += float(e.get("net") or 0.0)
        if equity > peak:
            peak = equity
        dd = min(dd, equity - peak)
    return dd

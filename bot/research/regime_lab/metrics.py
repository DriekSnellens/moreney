"""Research metrics. Canonical execution replay is the only evaluation NET.

EXPECTED_NET remains the tournament mean-edge per-signal figure used by OOS
gates. It is SIGNAL_EXPECTATION, not replay.
"""

from __future__ import annotations

from typing import Any

from bot.research.accounting.schema import EconomicWorld
from bot.research.accounting.waterfall import from_attached_events
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
                "other_costs": latency,
                "funding": 0.0,
                "transfer": 0.0,
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
    canon = from_attached_events(
        events,
        venue=venue,
        venue_exit=venue_exit,
        mean_forward=mean_forward,
        audit=audit,
    )
    # Tournament OOS gates still read EXPECTED_NET (mean-edge per signal).
    wf = net_waterfall_from_edge(
        gross_edge_fraction=abs(float(mean_forward or 0.0)), venue=venue, venue_exit=venue_exit
    )
    wf["CAPITAL_LOCK_MS"] = horizon_ms
    wf["INVENTORY_EFFECT"] = 0.0
    expected_net = float(wf["EXPECTED_NET"])
    replay = execution_replay_net(expected_net=expected_net)
    exec_net = float(replay["EXECUTION_NET"])
    fills = canon.fills.value
    dd = _max_drawdown(events)
    canonical_per_fill = (
        None if canon.replay_net_per_fill is None else float(canon.replay_net_per_fill.value)
    )
    mean_edge_per_fill = (
        None
        if canon.mean_edge_execution_replay_net_per_fill is None
        else float(canon.mean_edge_execution_replay_net_per_fill.value)
    )
    gross_bps = abs(float(mean_forward or 0.0)) * 10000.0
    net_per_bps = (expected_net / gross_bps) if gross_bps else None
    return {
        "signals": n,
        "candidate_events": cand,
        "accepted_events": admitted,
        "completed_round_trips": fills,
        "gross": float(canon.gross.value),
        "fees": float(canon.fees.value),
        "slippage": float(canon.slippage.value),
        "adverse": float(canon.adverse.value),
        "other_costs": float(canon.other_costs.value),
        "NET": float(canon.replay_net.value),
        "NET_world": EconomicWorld.EXECUTION_REPLAY.value,
        "NET_quantity": "RealizedReplayNetEUR",
        "EXPECTED_NET": expected_net,
        "EXPECTED_NET_world": EconomicWorld.SIGNAL_EXPECTATION.value,
        "EXPECTED_NET_quantity": "ExpectedNetPerSignalEUR",
        "EXECUTION_NET": exec_net,
        "EXECUTION_NET_quantity": "MeanEdgeExecutionReplayNetPerSignalEUR",
        "NET_per_fill": canonical_per_fill,
        "NET_per_fill_world": EconomicWorld.EXECUTION_REPLAY.value,
        "NET_per_fill_quantity": "RealizedReplayNetPerFillEUR",
        "NET_per_fill_definition": (
            "RealizedReplayNetEUR / EstimatedFillCount. Not the mean-edge overlay."
        ),
        "mean_edge_execution_replay_net_per_fill_eur": mean_edge_per_fill,
        "mean_edge_execution_replay_net_per_fill_quantity": "MeanEdgeExecutionReplayNetPerFillEUR",
        "canonical_replay_net_per_signal_eur": float(canon.replay_net_per_signal.value) if n else None,
        "NET_per_bps": net_per_bps,
        "EV": expected_net,
        "EV_world": EconomicWorld.SIGNAL_EXPECTATION.value,
        "EV_quantity": "ExpectedNetPerSignalEUR",
        "EV_capture": None,
        "EV_capture_note": "Cross-world; only via explicit CrossWorldComparison.",
        "maximum_drawdown": dd,
        "waterfall": wf,
        "execution_replay": replay,
        "mean_forward": mean_forward,
        "canonical": canon.report_block(),
        "note": (
            "NET is canonical RealizedReplayNetEUR (sum of per-signal waterfalls). "
            "EXPECTED_NET is SIGNAL_EXPECTATION mean-edge per signal (OOS gates). "
            "mean_edge_execution_replay_net_per_fill_eur is the old unlabeled NET/fill sidecar."
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

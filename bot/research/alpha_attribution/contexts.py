"""Descriptive pre-trade contexts and leave-one-context-out. Not optimization."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from bot.research.alpha_attribution.protocol import CONTEXT_NAMES, LOO_SHARE_FLAG
from bot.research.alpha_attribution.stability import classify_stability
from bot.research.accounting.waterfall import from_attached_events

_ZERO = Decimal("0")


def _econ(events: Sequence[dict[str, Any]], *, venue: str, venue_exit: str | None) -> dict[str, Any]:
    n = len(events)
    mean_fwd = sum(float(e.get("forward") or 0.0) for e in events) / n if n else None
    econ = from_attached_events(
        events,
        venue=venue,
        venue_exit=venue_exit,
        mean_forward=mean_fwd,
        audit={"candidates": n, "admitted": n, "rejected": 0},
    )
    return {
        "signal_count": econ.signals.value,
        "estimated_fills": econ.fills.value,
        "replay_net_eur": str(econ.replay_net.value),
        "replay_net_per_signal": None if n == 0 else str(econ.replay_net_per_signal.value),
        "replay_net_per_fill": (
            None if econ.replay_net_per_fill is None else str(econ.replay_net_per_fill.value)
        ),
        "net": econ.replay_net.value,
    }


def context_tables(
    events: Sequence[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
    window_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = {name: [] for name in CONTEXT_NAMES}
    by.setdefault("OTHER", [])
    for e in events:
        name = str(e.get("context") or "OTHER")
        by.setdefault(name, []).append(e)
    return by


def summarize_contexts(
    events: Sequence[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
    window_events: Sequence[tuple[str, Sequence[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    parent = _econ(events, venue=venue, venue_exit=venue_exit)
    parent_net = parent["net"] or Decimal("1")
    rows: list[dict[str, Any]] = []
    grouped = context_tables(events, venue=venue, venue_exit=venue_exit)
    for name in CONTEXT_NAMES:
        bucket = grouped.get(name) or []
        econ = _econ(bucket, venue=venue, venue_exit=venue_exit)
        wnets: list[Decimal] = []
        for _wid, wevents in window_events:
            sub = [e for e in wevents if e.get("context") == name]
            wnets.append(_econ(sub, venue=venue, venue_exit=venue_exit)["net"])
        stab = classify_stability(events=bucket, window_nets=wnets)
        share = (econ["net"] / parent_net) if parent_net else _ZERO
        rows.append(
            {
                "context": name,
                "DESCRIPTIVE_ONLY": True,
                "signal_count": econ["signal_count"],
                "estimated_fills": econ["estimated_fills"],
                "replay_net_eur": econ["replay_net_eur"],
                "replay_net_per_signal": econ["replay_net_per_signal"],
                "replay_net_per_fill": econ["replay_net_per_fill"],
                "contribution_share": str(share),
                "NET_contribution": econ["replay_net_eur"],
                "stability": stab["stability"],
                "positive_windows": stab["positive_windows"],
                "negative_windows": stab["negative_windows"],
                "mean_window_net": stab["mean_window_net"],
                "median_window_net": stab["median_window_net"],
                "top_symbol_share": stab["top_symbol_share"],
                "top_route_share": stab["top_route_share"],
                "ROUTE_UNIVERSE_LIMITED": stab["ROUTE_UNIVERSE_LIMITED"],
                "pre_trade_usable": True,
                "concentration": {
                    "top_symbol": stab.get("top_symbol"),
                    "top_symbol_share": stab["top_symbol_share"],
                    "top_route": stab.get("top_route"),
                    "top_route_share": stab["top_route_share"],
                },
                "window_nets": stab["window_nets"],
            }
        )
    rows.sort(key=lambda r: Decimal(str(r["replay_net_eur"] or 0)), reverse=True)
    return rows


def leave_one_context_out(
    events: Sequence[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
    window_events: Sequence[tuple[str, Sequence[dict[str, Any]]]],
) -> dict[str, Any]:
    parent = _econ(events, venue=venue, venue_exit=venue_exit)
    parent_net = parent["net"]
    out_rows: list[dict[str, Any]] = []
    flags: list[str] = []
    for name in CONTEXT_NAMES:
        only = [e for e in events if e.get("context") == name]
        without = [e for e in events if e.get("context") != name]
        only_e = _econ(only, venue=venue, venue_exit=venue_exit)
        without_e = _econ(without, venue=venue, venue_exit=venue_exit)
        only_w: list[Decimal] = []
        without_w: list[Decimal] = []
        all_w: list[Decimal] = []
        for _wid, wevents in window_events:
            all_w.append(_econ(wevents, venue=venue, venue_exit=venue_exit)["net"])
            only_w.append(
                _econ([e for e in wevents if e.get("context") == name], venue=venue, venue_exit=venue_exit)["net"]
            )
            without_w.append(
                _econ([e for e in wevents if e.get("context") != name], venue=venue, venue_exit=venue_exit)["net"]
            )
        share = (only_e["net"] / parent_net) if parent_net else _ZERO
        without_nonpos = without_e["net"] <= 0
        dependent = bool(share >= Decimal(str(LOO_SHARE_FLAG))) or without_nonpos
        if dependent and only:
            flags.append(name)
        out_rows.append(
            {
                "context": name,
                "DESCRIPTIVE_ONLY": True,
                "ALL": {
                    "net": parent["replay_net_eur"],
                    "net_per_signal": parent["replay_net_per_signal"],
                    "signals": parent["signal_count"],
                },
                "ONLY_context": {
                    "net": only_e["replay_net_eur"],
                    "net_per_signal": only_e["replay_net_per_signal"],
                    "signals": only_e["signal_count"],
                    "positive_windows": sum(1 for x in only_w if x > 0),
                    "negative_windows": sum(1 for x in only_w if x < 0),
                },
                "WITHOUT_context": {
                    "net": without_e["replay_net_eur"],
                    "net_per_signal": without_e["replay_net_per_signal"],
                    "signals": without_e["signal_count"],
                    "positive_windows": sum(1 for x in without_w if x > 0),
                    "negative_windows": sum(1 for x in without_w if x < 0),
                },
                "context_contribution": str(share),
                "CONTEXT_DEPENDENT_FLAG": dependent,
                "loo_share_flag": LOO_SHARE_FLAG,
                "without_net_non_positive": without_nonpos,
            }
        )
    return {
        "rows": out_rows,
        "CONTEXT_DEPENDENCY": "CONTEXT_DEPENDENT" if flags else "NOT_SINGLE_CONTEXT",
        "flagged_contexts": flags,
        "note": (
            "CONTEXT_DEPENDENT if removing a context leaves non-positive parent net "
            f"or the context share is >= {LOO_SHARE_FLAG} (existing LOO dominance floor). "
            "Not an automatic reject or promote."
        ),
    }

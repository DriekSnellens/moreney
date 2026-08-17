"""Incremental value: parent vs gated vs regime-only vs no-trade on the same window."""

from __future__ import annotations

from typing import Any

from bot.research.forensics.buckets import SPREAD_WIDE_BPS
from bot.research.robustness.protocol import MIN_REGIME_OBS
from bot.research.regime_lab.features import spread_bps, views_for
from bot.research.tournament.tape_index import TapeIndex, iter_window


def incremental_compare(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """rows keys: parent, gated, regime_only, no_trade."""
    g = rows.get("gated") or {}
    p = rows.get("parent") or {}
    r = rows.get("regime_only") or {}
    n = rows.get("no_trade") or {}
    g_net = g.get("EXPECTED_NET")
    p_net = p.get("EXPECTED_NET")
    delta = None
    if g_net is not None and p_net is not None:
        delta = float(g_net) - float(p_net)
    g_n = int(g.get("signals") or 0)
    p_n = int(p.get("signals") or 0)
    velocity_g = g_n  # signals as capital-velocity proxy (same notional/horizon)
    velocity_p = p_n
    improved = delta is not None and delta > 0
    return {
        "parent": _slice(p),
        "gated": _slice(g),
        "regime_only": _slice(r),
        "no_trade": _slice(n),
        "delta_EXPECTED_NET_gated_minus_parent": delta,
        "delta_signals": g_n - p_n,
        "capital_velocity_gated_signals": velocity_g,
        "capital_velocity_parent_signals": velocity_p,
        "gate_improves_parent_expected_net": improved,
        "INCREMENTAL_VALUE": (
            "POSITIVE" if improved else ("NONE" if delta == 0 else "NOT_POSITIVE")
        ),
    }


def _slice(m: dict[str, Any]) -> dict[str, Any]:
    stab = m.get("stability") or {}
    return {
        "EXPECTED_NET": m.get("EXPECTED_NET"),
        "NET": m.get("NET"),
        "NET_per_fill": m.get("NET_per_fill"),
        "signals": m.get("signals"),
        "maximum_drawdown": m.get("maximum_drawdown"),
        "top_symbol_share": stab.get("top_symbol_share"),
        "top_route_share": stab.get("top_route_share"),
        "ROUTE_UNIVERSE_LIMITED": stab.get("ROUTE_UNIVERSE_LIMITED"),
    }


def regime_diversity(
    *,
    index: TapeIndex,
    start_ns: int,
    end_ns_inclusive: int,
    venue: str,
    audit: dict[str, Any] | None,
    required: bool,
) -> dict[str, Any]:
    views = views_for(index)
    wide = non_wide = 0
    step_target = 2000
    for symbol in index.symbols_for(venue):
        pts = index.points(venue, symbol)
        window = list(
            iter_window(
                pts,
                start_ns=start_ns,
                end_ns_exclusive=None,
                end_ns_inclusive=end_ns_inclusive,
            )
        )
        if not window:
            continue
        step = max(1, len(window) // step_target)
        view = views.get((venue, symbol))
        for k in range(0, len(window), step):
            p = window[k]
            sp = spread_bps(p)
            if sp is None:
                continue
            if sp >= SPREAD_WIDE_BPS:
                wide += 1
            else:
                non_wide += 1
    adm = int((audit or {}).get("admitted") or 0)
    rej = int((audit or {}).get("rejected") or 0)
    both_tape = wide >= MIN_REGIME_OBS and non_wide >= MIN_REGIME_OBS
    gate_both = adm > 0 and rej > 0
    both = both_tape and (gate_both if required else True)
    return {
        "required": required,
        "venue": venue,
        "wide_observations": wide,
        "non_wide_observations": non_wide,
        "min_regime_obs": MIN_REGIME_OBS,
        "gate_admitted": adm,
        "gate_rejected": rej,
        "both_states": both,
        "tape_has_both_spread_states": both_tape,
        "gate_admits_and_rejects": gate_both,
        "INSUFFICIENT_REGIME_DIVERSITY": required and not both,
    }

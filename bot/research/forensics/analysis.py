"""Deterministic decompositions, leave-one-out, regimes, and nulls."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Callable

from bot.research.forensics.buckets import (
    FORENSICS_SEED,
    N_CHRONO_BLOCKS,
    N_PERMUTATIONS,
    NULL_EXTREME_ALPHA,
)


DECOMPOSE_FIELDS: tuple[str, ...] = (
    "symbol",
    "route",
    "direction",
    "hour_utc",
    "chrono_block",
    "vol_regime",
    "spread_regime",
    "liquidity_regime",
    "event_density_regime",
    "market_return_regime",
    "holding_regime",
    "signal_strength_bucket",
    "quote_age_regime",
)

FIELD_LABEL = {
    "symbol": "symbol",
    "route": "venue_pair",
    "direction": "direction",
    "hour_utc": "hour",
    "chrono_block": "chrono_block",
    "vol_regime": "volatility_regime",
    "spread_regime": "spread_regime",
    "liquidity_regime": "liquidity_regime",
    "event_density_regime": "event_density_regime",
    "market_return_regime": "market_return_regime",
    "holding_regime": "holding_duration",
    "signal_strength_bucket": "signal_strength",
    "quote_age_regime": "quote_age_regime",
}

PRE_TRADE_FEATURES = (
    "signal_strength_bps",
    "spread_bps",
    "depth_eur",
    "book_imbalance",
    "vol_bps",
    "event_density",
    "market_return_bps",
    "quote_age_ms",
    "cross_venue_divergence_bps",
)


def _f(x: Any) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def totals(events: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(events)
    gross = sum(_f(e.get("gross")) for e in events)
    fees = sum(_f(e.get("fees")) for e in events)
    slip = sum(_f(e.get("slippage")) for e in events)
    adverse = sum(_f(e.get("adverse")) for e in events)
    net = sum(_f(e.get("net")) for e in events)
    return {
        "signals": n,
        "trades": n,
        "gross": gross,
        "fees": fees,
        "slippage": slip,
        "adverse": adverse,
        "NET": net,
        "NET_per_trade": (net / n) if n else None,
    }


def group_key(event: dict[str, Any], field: str) -> str:
    v = event.get(field)
    if v is None:
        return "UNKNOWN"
    return str(v)


def _hhi(weights: list[float]) -> float | None:
    s = sum(weights)
    if s <= 0 or len(weights) < 1:
        return None
    return sum((w / s) ** 2 for w in weights)


def decompose(events: list[dict[str, Any]], field: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        buckets[group_key(e, field)].append(e)
    rows = []
    full = totals(events)
    full_net = _f(full["NET"])
    abs_fwd = sum(abs(_f(e.get("forward"))) for e in events) or 1.0
    for key, group in sorted(buckets.items(), key=lambda kv: -abs(totals(kv[1])["NET"])):
        t = totals(group)
        t["group"] = key
        t["net_share"] = (t["NET"] / full_net) if full_net else None
        t["abs_forward_share"] = sum(abs(_f(e.get("forward"))) for e in group) / abs_fwd
        rows.append(t)
    return {
        "field": FIELD_LABEL.get(field, field),
        "groups": rows,
        "herfindahl_abs_net": _hhi([abs(_f(r["NET"])) for r in rows]),
        "herfindahl_abs_forward": _hhi([_f(r["abs_forward_share"]) for r in rows]),
        "n_groups": len(rows),
    }


def top_k_contribution(decomp: dict[str, Any], k: int) -> dict[str, Any]:
    groups = list(decomp.get("groups") or [])
    full_net = sum(_f(g["NET"]) for g in groups)
    ranked = sorted(groups, key=lambda g: -_f(g["NET"]))
    take = ranked[:k]
    contrib = sum(_f(g["NET"]) for g in take)
    return {
        "k": k,
        "NET": contrib,
        "share_of_total_net": (contrib / full_net) if full_net else None,
        "groups": [g["group"] for g in take],
    }


def _top_row(decomp: dict[str, Any], full_net: float) -> dict[str, Any]:
    groups = decomp.get("groups") or []
    if not groups:
        return {"group": None, "NET": 0.0, "share": None, "rest_NET": full_net}
    g0 = max(groups, key=lambda g: _f(g["NET"]))
    return {
        "group": g0["group"],
        "NET": g0["NET"],
        "share": (g0["NET"] / full_net) if full_net else None,
        "rest_NET": full_net - g0["NET"],
        "abs_forward_share": g0.get("abs_forward_share"),
    }


def top_contributor_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_sym = decompose(events, "symbol")
    by_route = decompose(events, "route")
    by_hour = decompose(events, "hour_utc")
    by_block = decompose(events, "chrono_block")
    full = totals(events)
    top_trades = sorted(events, key=lambda e: -_f(e.get("net")))[:10]
    top10_net = sum(_f(e.get("net")) for e in top_trades)
    full_net = _f(full["NET"])
    return {
        "total_NET": full_net,
        "top_1": top_k_contribution(by_sym, 1),
        "top_5": top_k_contribution(by_sym, 5),
        "top_10": top_k_contribution(by_sym, 10),
        "herfindahl_symbol_abs_forward": by_sym.get("herfindahl_abs_forward"),
        "herfindahl_symbol_abs_net": by_sym.get("herfindahl_abs_net"),
        "top_symbol": _top_row(by_sym, full_net),
        "top_venue_pair": _top_row(by_route, full_net),
        "top_hour": _top_row(by_hour, full_net),
        "top_chrono_block": _top_row(by_block, full_net),
        "top_10_trades_NET": top10_net,
        "top_10_trades_share": (top10_net / full_net) if full_net else None,
        "n_routes": by_route.get("n_groups"),
        "route_share_tautology": (by_route.get("n_groups") or 0) <= 1,
    }


def chrono_block_table(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_block: dict[str, list[dict[str, Any]]] = {
        f"BLOCK_{i}": [] for i in range(1, N_CHRONO_BLOCKS + 1)
    }
    for e in events:
        by_block.setdefault(group_key(e, "chrono_block"), []).append(e)
    rows = []
    nets = []
    for i in range(1, N_CHRONO_BLOCKS + 1):
        key = f"BLOCK_{i}"
        t = totals(by_block.get(key) or [])
        t["group"] = key
        rows.append(t)
        nets.append(t["NET"])
    pos = sum(1 for x in nets if x > 0)
    neg = sum(1 for x in nets if x < 0)
    ordered = sorted(nets)
    mid = ordered[len(ordered) // 2] if ordered else None
    mean = (sum(nets) / len(nets)) if nets else None
    best_i = max(range(len(nets)), key=lambda j: nets[j]) if nets else 0
    worst_i = min(range(len(nets)), key=lambda j: nets[j]) if nets else 0
    return {
        "blocks": rows,
        "positive_blocks": pos,
        "negative_blocks": neg,
        "median_block_PnL": mid,
        "mean_block_PnL": mean,
        "best_block": rows[best_i] if rows else None,
        "worst_block": rows[worst_i] if rows else None,
        "blocks_with_signals": sum(1 for r in rows if r["signals"] > 0),
    }


def leave_one_out(events: list[dict[str, Any]], field: str) -> dict[str, Any]:
    full = totals(events)
    full_net = _f(full["NET"])
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        buckets[group_key(e, field)].append(e)
    rows = []
    for key, group in sorted(buckets.items()):
        without_net = full_net - totals(group)["NET"]
        rows.append(
            {
                "left_out": key,
                "FULL_RESULT": full_net,
                "WITHOUT": without_net,
                "group_NET": totals(group)["NET"],
                "sign_flip": (full_net > 0 > without_net) or (full_net < 0 < without_net),
            }
        )
    rows.sort(key=lambda r: abs(_f(r["FULL_RESULT"]) - _f(r["WITHOUT"])), reverse=True)
    return {
        "field": FIELD_LABEL.get(field, field),
        "FULL_RESULT": full_net,
        "rows": rows,
    }


def _mean(xs: list[float]) -> float | None:
    return (sum(xs) / len(xs)) if xs else None


def regime_explanation(events: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Compare pre-trade features of the top-NET bucket vs the complement."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        buckets[group_key(e, field)].append(e)
    if not buckets:
        return {"field": field, "structural": False}
    top_key = max(buckets, key=lambda k: totals(buckets[k])["NET"])
    focus = buckets[top_key]
    rest = [e for k, g in buckets.items() if k != top_key for e in g]
    full_net = totals(events)["NET"]
    focus_net = totals(focus)["NET"]
    share = (focus_net / full_net) if full_net else None
    feature_cmp: dict[str, Any] = {}
    structural_hits: list[str] = []
    for feat in PRE_TRADE_FEATURES:
        a = [_f(e.get(feat)) for e in focus if e.get(feat) is not None]
        b = [_f(e.get(feat)) for e in rest if e.get(feat) is not None]
        ma, mb = _mean(a), _mean(b)
        ratio = None
        if ma is not None and mb is not None and mb != 0:
            ratio = abs(ma) / abs(mb)
        hit = bool(ratio is not None and (ratio >= 2.0 or (0 < ratio <= 0.5)))
        feature_cmp[feat] = {
            "focus_mean": ma,
            "complement_mean": mb,
            "abs_ratio": ratio,
            "structural": hit,
        }
        if hit:
            structural_hits.append(feat)
    return {
        "field": FIELD_LABEL.get(field, field),
        "focus_group": top_key,
        "focus_NET": focus_net,
        "share_of_total_net": share,
        "features": feature_cmp,
        "structural_features": structural_hits,
        "structural": bool(structural_hits) and (share is not None and abs(share) >= 0.5),
    }


def _top_share(events: list[dict[str, Any]], field: str, *, use_abs_forward: bool) -> float:
    buckets: dict[str, float] = defaultdict(float)
    for e in events:
        if use_abs_forward:
            buckets[group_key(e, field)] += abs(_f(e.get("forward")))
        else:
            buckets[group_key(e, field)] += _f(e.get("net"))
    if not buckets:
        return 1.0
    if use_abs_forward:
        tot = sum(buckets.values()) or 1.0
        return max(buckets.values()) / tot
    abs_tot = sum(abs(v) for v in buckets.values()) or 1.0
    return max(abs(v) for v in buckets.values()) / abs_tot


def null_checks(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Fixed-seed permutations. Not a significance claim for alpha."""
    if len(events) < 2:
        return {"feasible": False, "reason": "too_few_events"}
    rng = random.Random(FORENSICS_SEED)
    obs_sym = _top_share(events, "symbol", use_abs_forward=True)
    obs_sym_net = _top_share(events, "symbol", use_abs_forward=False)
    obs_block = _top_share(events, "chrono_block", use_abs_forward=False)
    obs_hour = _top_share(events, "hour_utc", use_abs_forward=False)

    def _p(obs: float, sampler: Callable[[random.Random], float]) -> float:
        hits = 0
        local = random.Random(rng.randint(1, 10**9))
        for _ in range(N_PERMUTATIONS):
            if sampler(local) >= obs - 1e-15:
                hits += 1
        return (1 + hits) / (1 + N_PERMUTATIONS)

    nets = [_f(e.get("net")) for e in events]
    fwds = [_f(e.get("forward")) for e in events]
    costs = [
        _f(e.get("fees")) + _f(e.get("slippage")) + _f(e.get("adverse")) + _f(e.get("latency"))
        for e in events
    ]

    def perm_pnl_symbol(r: random.Random) -> float:
        shuffled = list(nets)
        r.shuffle(shuffled)
        tmp = [dict(e) for e in events]
        for e, n in zip(tmp, shuffled):
            e["net"] = n
        return _top_share(tmp, "symbol", use_abs_forward=False)

    def perm_signal_symbol(r: random.Random) -> float:
        shuffled = list(fwds)
        r.shuffle(shuffled)
        tmp = [dict(e) for e in events]
        for e, f, c in zip(tmp, shuffled, costs):
            e["forward"] = f
            e["net"] = 100.0 * f - c
        return _top_share(tmp, "symbol", use_abs_forward=True)

    def rotate_block(r: random.Random) -> float:
        ordered = sorted(events, key=lambda e: int(e.get("ts_ns") or 0))
        shift = r.randint(1, max(1, len(ordered) - 1))
        nets_o = [_f(e.get("net")) for e in ordered]
        rotated = nets_o[shift:] + nets_o[:shift]
        tmp = [dict(e) for e in ordered]
        for e, n in zip(tmp, rotated):
            e["net"] = n
        return _top_share(tmp, "chrono_block", use_abs_forward=False)

    p_sym_pnl = _p(obs_sym_net, perm_pnl_symbol)
    p_sym_fwd = _p(obs_sym, perm_signal_symbol)
    p_block = _p(obs_block, rotate_block)
    return {
        "feasible": True,
        "seed": FORENSICS_SEED,
        "n_permutations": N_PERMUTATIONS,
        "observed_top_symbol_abs_forward_share": obs_sym,
        "observed_top_symbol_abs_net_share": obs_sym_net,
        "observed_top_block_net_share": obs_block,
        "observed_top_hour_net_share": obs_hour,
        "p_permute_pnl_top_symbol": p_sym_pnl,
        "p_permute_signal_top_symbol": p_sym_fwd,
        "p_rotate_chrono_top_block": p_block,
        "extreme_vs_null": min(p_sym_fwd, p_block) < NULL_EXTREME_ALPHA,
        "note": "p = (1+hits)/(1+N). Not an alpha claim.",
    }


def all_decompositions(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {FIELD_LABEL.get(f, f): decompose(events, f) for f in DECOMPOSE_FIELDS}


def all_loo(events: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "symbol",
        "route",
        "chrono_block",
        "quote_age_regime",
        "vol_regime",
        "spread_regime",
    )
    return {FIELD_LABEL.get(f, f): leave_one_out(events, f) for f in fields}


def all_regimes(events: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "quote_age_regime",
        "vol_regime",
        "spread_regime",
        "liquidity_regime",
        "event_density_regime",
        "market_return_regime",
        "signal_strength_bucket",
    )
    return {FIELD_LABEL.get(f, f): regime_explanation(events, f) for f in fields}

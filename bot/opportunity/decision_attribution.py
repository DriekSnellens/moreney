"""Four-way decision attribution for causal configs A/B/C/D.

Runs each configuration with *independent* causal state. Joins by opportunity_id.
Ex-post baseline realized NET is attached for evaluation only — never fed into
another configuration's beliefs.

Usage:
  .venv/bin/python -m bot.opportunity.decision_attribution data/paper_25000live.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.opportunity.causal_walkforward import (
    CONFIGS,
    CausalBeliefModel,
    walk_forward,
)

_ZERO = Decimal("0")

CATEGORIES = (
    "ALL_TAKE",
    "EARLY_STOP_ONLY_BLOCK",
    "CONDITIONAL_EV_ONLY_BLOCK",
    "BOTH_BLOCK",
    "BASELINE_REJECT",
    "OTHER",
)


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


def _event_index(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e["opportunity_id"]): e for e in (result.get("events") or [])}


def classify_decisions(a: str, b: str, c: str, d: str) -> str:
    if a == "reject":
        return "BASELINE_REJECT"
    if a == "take" and b == "take" and c == "take" and d == "take":
        return "ALL_TAKE"
    if a == "take" and b == "reject" and c == "take":
        return "EARLY_STOP_ONLY_BLOCK"
    if a == "take" and b == "take" and c == "reject":
        return "CONDITIONAL_EV_ONLY_BLOCK"
    if a == "take" and b == "reject" and c == "reject":
        return "BOTH_BLOCK"
    return "OTHER"


def run_independent_replays(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each config gets a fresh CausalBeliefModel — no shared state."""
    out: dict[str, dict[str, Any]] = {}
    for key, cfg in CONFIGS.items():
        out[key] = walk_forward(
            trades,
            config=cfg,
            model=CausalBeliefModel(),
            data_status="IN_SAMPLE_CAUSAL_REPLAY",
        )
    return out


def build_comparison_rows(
    trades: list[dict[str, Any]],
    replays: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    idx = {k: _event_index(v) for k, v in replays.items()}
    rows: list[dict[str, Any]] = []
    for trade in sorted(trades, key=lambda t: t.get("timestamp") or ""):
        oid = str(trade.get("opportunity_id"))
        ea = idx["A_BASELINE"].get(oid) or {}
        eb = idx["B_EARLY_STOP_ONLY"].get(oid) or {}
        ec = idx["C_CONDITIONAL_EV_ONLY"].get(oid) or {}
        ed = idx["D_CONDITIONAL_EV_PLUS_EARLY_STOP"].get(oid) or {}
        da = ea.get("decision", "missing")
        db = eb.get("decision", "missing")
        dc = ec.get("decision", "missing")
        dd = ed.get("decision", "missing")
        # Prefer C's predictions for conditional fields when present; else A.
        pred_src = ec if ec else ea
        category = classify_decisions(da, db, dc, dd)
        # D has its own path-dependent beliefs. Independent B∪C is NOT D's gate.
        # Record whether independent union would differ from D (path-dependence signal).
        independent_union = (
            "reject" if (db == "reject" or dc == "reject") else "take"
        )
        rows.append(
            {
                "timestamp": trade.get("timestamp"),
                "opportunity_id": oid,
                "route_id": f"{trade.get('buy_exchange')}->{trade.get('sell_exchange')}",
                "venue_buy": trade.get("buy_exchange"),
                "venue_sell": trade.get("sell_exchange"),
                "symbol": trade.get("symbol"),
                "side": "buy",  # round-trip rows are buy-initiated in tracker
                "fill_type": trade.get("fill_type") or "unknown",
                "quote_age_ms": trade.get("quote_age_ms"),
                "route_state_before_A": ea.get("route_state_before"),
                "route_state_before_B": eb.get("route_state_before"),
                "route_state_before_C": ec.get("route_state_before"),
                "route_state_before_D": ed.get("route_state_before"),
                "historical_n_B": eb.get("historical_n"),
                "historical_n_C": ec.get("historical_n"),
                "historical_n_D": ed.get("historical_n"),
                "deterministic_net": str(_d(trade.get("expected_net_profit"))),
                "predicted_adverse_C": pred_src.get("predicted_adverse_eur"),
                "predicted_net_if_fill_C": pred_src.get("predicted_net_if_fill"),
                "predicted_p_fill_C": pred_src.get("predicted_p_fill"),
                "predicted_ev_C": pred_src.get("predicted_ev"),
                "raw_capture_before_B": eb.get("raw_capture_before"),
                "shrunk_capture_before_B": eb.get("shrunk_capture_before"),
                "baseline_decision": da,
                "early_stop_decision": db,
                "conditional_ev_decision": dc,
                "combined_decision": dd,
                "reject_reason_A": ea.get("decision_reason") if da == "reject" else None,
                "reject_reason_B": eb.get("decision_reason") if db == "reject" else None,
                "reject_reason_C": ec.get("decision_reason") if dc == "reject" else None,
                "reject_reason_D": ed.get("decision_reason") if dd == "reject" else None,
                "category": category,
                "independent_b_or_c_reject": independent_union == "reject",
                "d_differs_from_independent_union": dd != independent_union,
                "path_dependence_signal": dd != independent_union,
                # EX-POST only — baseline realized if A took (always in this dataset).
                "ex_post_counterfactual_outcome": str(_d(trade.get("realized_net_profit"))),
                "ex_post_label": "EX-POST COUNTERFACTUAL OUTCOME",
                "ex_post_realized_adverse": str(_d(trade.get("realized_adverse"))),
                "ex_post_markout_proxy_adverse": str(_d(trade.get("realized_adverse"))),
            }
        )
    return rows


def mechanism_overlap(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Blocks relative to baseline take."""
    base_taken = [r for r in rows if r["baseline_decision"] == "take"]
    es_block = [r for r in base_taken if r["early_stop_decision"] == "reject"]
    cev_block = [r for r in base_taken if r["conditional_ev_decision"] == "reject"]
    es_ids = {r["opportunity_id"] for r in es_block}
    cev_ids = {r["opportunity_id"] for r in cev_block}
    intersection = es_ids & cev_ids
    es_unique = es_ids - cev_ids
    cev_unique = cev_ids - es_ids

    def _group_stats(ids: set[str]) -> dict[str, Any]:
        subset = [r for r in base_taken if r["opportunity_id"] in ids]
        nets = [_d(r["ex_post_counterfactual_outcome"]) for r in subset]
        adv = [_d(r["ex_post_markout_proxy_adverse"]) for r in subset]
        routes: dict[str, int] = defaultdict(int)
        sides: dict[str, int] = defaultdict(int)
        for r in subset:
            routes[str(r["route_id"])] += 1
            sides[str(r["side"])] += 1
        n = len(subset)
        ordered_adv = sorted(adv)
        return {
            "count": n,
            "realized_baseline_net": str(sum(nets, _ZERO)),
            "mean_markout_proxy": str(sum(adv, _ZERO) / n) if n else None,
            "median_markout_proxy": str(ordered_adv[n // 2]) if n else None,
            "trade_through_pct": None,  # not on trade rows
            "mean_quote_age_ms": None,
            "routes": dict(routes),
            "side_distribution": dict(sides),
            "opportunity_ids": sorted(ids),
        }

    return {
        "trades_blocked_by_early_stop": len(es_ids),
        "trades_blocked_by_conditional_ev": len(cev_ids),
        "intersection": len(intersection),
        "early_stop_unique_blocks": len(es_unique),
        "conditional_ev_unique_blocks": len(cev_unique),
        "groups": {
            "early_stop_all": _group_stats(es_ids),
            "conditional_ev_all": _group_stats(cev_ids),
            "intersection": _group_stats(intersection),
            "early_stop_unique": _group_stats(es_unique),
            "conditional_ev_unique": _group_stats(cev_unique),
        },
    }


def category_pnl_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cat in CATEGORIES:
        subset = [r for r in rows if r["category"] == cat]
        # Avoided PnL: for blocked-vs-baseline categories, sum of baseline outcomes
        # that would not be taken under the blocking mechanism(s).
        nets = [_d(r["ex_post_counterfactual_outcome"]) for r in subset]
        total = sum(nets, _ZERO)
        avoided_loss = sum((-n for n in nets if n < 0), _ZERO)
        avoided_gain = sum((n for n in nets if n > 0), _ZERO)
        # For ALL_TAKE / BASELINE_REJECT the "avoided" framing is N/A as blocks.
        is_block_cat = cat in {
            "EARLY_STOP_ONLY_BLOCK",
            "CONDITIONAL_EV_ONLY_BLOCK",
            "BOTH_BLOCK",
        }
        out.append(
            {
                "category": cat,
                "opportunities": len(subset),
                "taken_by_baseline": sum(
                    1 for r in subset if r["baseline_decision"] == "take"
                ),
                "blocked": len(subset) if is_block_cat else 0,
                "realized_baseline_net": str(total),
                "avoided_loss": str(avoided_loss) if is_block_cat else "0",
                "avoided_gain": str(avoided_gain) if is_block_cat else "0",
                "net_avoided_pnl": str(-total) if is_block_cat else "0",
                "kind": "EX-POST COUNTERFACTUAL OUTCOME" if is_block_cat else "observed",
            }
        )
    return out


def marginal_contribution(replays: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    a = _d(replays["A_BASELINE"]["total_realized_net"])
    b = _d(replays["B_EARLY_STOP_ONLY"]["total_realized_net"])
    c = _d(replays["C_CONDITIONAL_EV_ONLY"]["total_realized_net"])
    d = _d(replays["D_CONDITIONAL_EV_PLUS_EARLY_STOP"]["total_realized_net"])
    # Improvement = less negative (or more positive): baseline - config (positive = better)
    # Actually: contribution as NET_config - NET_baseline (positive = improvement)
    delta_b = b - a
    delta_c = c - a
    delta_d = d - a
    # Overlap: if additive, delta_b + delta_c == delta_d; else overlap exists.
    # Unique ES block PnL (ex-post): sum of baseline outcomes in EARLY_STOP_ONLY_BLOCK
    es_only = sum(
        (
            _d(r["ex_post_counterfactual_outcome"])
            for r in rows
            if r["category"] == "EARLY_STOP_ONLY_BLOCK"
        ),
        _ZERO,
    )
    cev_only = sum(
        (
            _d(r["ex_post_counterfactual_outcome"])
            for r in rows
            if r["category"] == "CONDITIONAL_EV_ONLY_BLOCK"
        ),
        _ZERO,
    )
    both = sum(
        (
            _d(r["ex_post_counterfactual_outcome"])
            for r in rows
            if r["category"] == "BOTH_BLOCK"
        ),
        _ZERO,
    )
    return {
        "baseline_net": str(a),
        "early_stop_only_net": str(b),
        "conditional_ev_only_net": str(c),
        "combined_net": str(d),
        "delta_early_stop_vs_baseline": str(delta_b),
        "delta_conditional_ev_vs_baseline": str(delta_c),
        "delta_combined_vs_baseline": str(delta_d),
        "sum_of_individual_deltas": str(delta_b + delta_c),
        "overlap_exists": (delta_b + delta_c) != delta_d,
        "ex_post_net_of_early_stop_unique_blocks": str(es_only),
        "ex_post_net_of_conditional_ev_unique_blocks": str(cev_only),
        "ex_post_net_of_overlapping_blocks": str(both),
        # Avoided PnL = -ex_post (blocking a -€10 trade avoids +€10 improvement)
        "improvement_from_es_unique": str(-es_only),
        "improvement_from_cev_unique": str(-cev_only),
        "improvement_from_overlap": str(-both),
        "note": (
            "If C preempts B, EARLY_STOP_ONLY_BLOCK count is 0 and "
            "BOTH_BLOCK + CONDITIONAL_EV_ONLY_BLOCK explain C==D."
        ),
    }


def failure_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """False rejects / false accepts for conditional EV vs ex-post baseline outcome."""
    false_rejects: list[dict[str, Any]] = []
    false_accepts: list[dict[str, Any]] = []
    for r in rows:
        if r["baseline_decision"] != "take":
            continue
        actual = _d(r["ex_post_counterfactual_outcome"])
        pred_net = _d(r.get("predicted_net_if_fill_C"))
        if r["conditional_ev_decision"] == "reject" and actual > 0:
            false_rejects.append(
                {
                    "opportunity_id": r["opportunity_id"],
                    "timestamp": r["timestamp"],
                    "route_id": r["route_id"],
                    "symbol": r["symbol"],
                    "predicted_adverse": r.get("predicted_adverse_C"),
                    "predicted_net_if_fill": r.get("predicted_net_if_fill_C"),
                    "actual_net": str(actual),
                    "prediction_error": str(actual - pred_net),
                    "markout_proxy": r.get("ex_post_markout_proxy_adverse"),
                    "kind": "EX-POST COUNTERFACTUAL OUTCOME",
                }
            )
        if r["conditional_ev_decision"] == "take" and actual < Decimal("-3"):
            false_accepts.append(
                {
                    "opportunity_id": r["opportunity_id"],
                    "timestamp": r["timestamp"],
                    "route_id": r["route_id"],
                    "symbol": r["symbol"],
                    "predicted_adverse": r.get("predicted_adverse_C"),
                    "predicted_net_if_fill": r.get("predicted_net_if_fill_C"),
                    "actual_net": str(actual),
                    "prediction_error": str(actual - pred_net),
                    "markout_proxy": r.get("ex_post_markout_proxy_adverse"),
                    "kind": "EX-POST COUNTERFACTUAL OUTCOME",
                }
            )
    return {
        "false_rejects_predicted_neg_realized_pos": false_rejects,
        "false_accepts_predicted_ok_realized_strongly_neg": false_accepts,
        "false_reject_count": len(false_rejects),
        "false_accept_count": len(false_accepts),
        "profitable_trades_rejected_by_early_stop": sum(
            1
            for r in rows
            if r["baseline_decision"] == "take"
            and r["early_stop_decision"] == "reject"
            and _d(r["ex_post_counterfactual_outcome"]) > 0
        ),
        "profitable_trades_rejected_by_conditional_ev": sum(
            1
            for r in rows
            if r["baseline_decision"] == "take"
            and r["conditional_ev_decision"] == "reject"
            and _d(r["ex_post_counterfactual_outcome"]) > 0
        ),
        "large_losses_missed_by_early_stop": sum(
            1
            for r in rows
            if r["baseline_decision"] == "take"
            and r["early_stop_decision"] == "take"
            and _d(r["ex_post_counterfactual_outcome"]) < Decimal("-3")
        ),
        "large_losses_missed_by_conditional_ev": sum(
            1
            for r in rows
            if r["baseline_decision"] == "take"
            and r["conditional_ev_decision"] == "take"
            and _d(r["ex_post_counterfactual_outcome"]) < Decimal("-3")
        ),
    }


def mechanism_timing(rows: list[dict[str, Any]], route: str | None = None) -> dict[str, Any]:
    subset = rows
    if route:
        subset = [r for r in rows if r["route_id"] == route]
    first_cev = next(
        (
            (i, r)
            for i, r in enumerate(subset, start=1)
            if r["conditional_ev_decision"] == "reject"
        ),
        None,
    )
    first_es = next(
        (
            (i, r)
            for i, r in enumerate(subset, start=1)
            if r["early_stop_decision"] == "reject"
        ),
        None,
    )
    timeline = []
    for i, r in enumerate(subset, start=1):
        timeline.append(
            {
                "event_number": i,
                "timestamp": r["timestamp"],
                "opportunity_id": r["opportunity_id"],
                "historical_n_B": r["historical_n_B"],
                "historical_n_C": r["historical_n_C"],
                "route_state_before_B": r["route_state_before_B"],
                "route_state_before_C": r["route_state_before_C"],
                "raw_capture_B": r["raw_capture_before_B"],
                "shrunk_capture_B": r["shrunk_capture_before_B"],
                "predicted_adverse_C": r["predicted_adverse_C"],
                "predicted_net_if_fill_C": r["predicted_net_if_fill_C"],
                "early_stop_decision": r["early_stop_decision"],
                "conditional_ev_decision": r["conditional_ev_decision"],
                "ex_post_baseline_outcome": r["ex_post_counterfactual_outcome"],
                "category": r["category"],
            }
        )
    acted_first = None
    if first_cev and first_es:
        acted_first = (
            "conditional_ev"
            if first_cev[0] < first_es[0]
            else "early_stop"
            if first_es[0] < first_cev[0]
            else "same_event"
        )
    elif first_cev:
        acted_first = "conditional_ev"
    elif first_es:
        acted_first = "early_stop"

    return {
        "route": route or "ALL",
        "first_conditional_ev_reject_event": first_cev[0] if first_cev else None,
        "first_conditional_ev_reject_ts": first_cev[1]["timestamp"] if first_cev else None,
        "first_early_stop_reject_event": first_es[0] if first_es else None,
        "first_early_stop_reject_ts": first_es[1]["timestamp"] if first_es else None,
        "event_gap_cev_minus_es": (
            (first_cev[0] - first_es[0]) if first_cev and first_es else None
        ),
        "acted_first": acted_first,
        "timeline": timeline,
    }


def build_attribution_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    trades = list((data.get("tracker") or {}).get("trades") or [])
    replays = run_independent_replays(trades)
    rows = build_comparison_rows(trades, replays)
    overlap = mechanism_overlap(rows)
    categories = category_pnl_table(rows)
    marginal = marginal_contribution(replays, rows)
    failures = failure_analysis(rows)
    # Primary route from data
    routes = sorted({r["route_id"] for r in rows})
    primary = "bitvavo->bitvavo" if "bitvavo->bitvavo" in routes else (routes[0] if routes else None)
    timing_all = mechanism_timing(rows)
    timing_bv = mechanism_timing(rows, route=primary) if primary else {}

    # Prove C==D via path dependence when ES-only blocks are still taken by C/D
    c_net = replays["C_CONDITIONAL_EV_ONLY"]["total_realized_net"]
    d_net = replays["D_CONDITIONAL_EV_PLUS_EARLY_STOP"]["total_realized_net"]
    es_only_count = next(
        c["opportunities"] for c in categories if c["category"] == "EARLY_STOP_ONLY_BLOCK"
    )
    both_count = next(c["opportunities"] for c in categories if c["category"] == "BOTH_BLOCK")
    cev_only_count = next(
        c["opportunities"] for c in categories if c["category"] == "CONDITIONAL_EV_ONLY_BLOCK"
    )
    path_dep_rows = [r for r in rows if r.get("path_dependence_signal")]
    # On D's own timeline: did early_stop ever reject?
    d_es_rejects = sum(
        1
        for e in (replays["D_CONDITIONAL_EV_PLUS_EARLY_STOP"].get("events") or [])
        if e.get("decision_reason") == "early_stop_historical"
    )

    if c_net == d_net and d_es_rejects == 0 and cev_only_count > 0:
        relation = "path_dependent_overlap_cev_dominates"
        why_equal = (
            "C and D have identical NET because D's early-stop never fired: "
            "conditional EV rejected the intermediate losers that would have "
            f"built early-stop evidence on the independent B path "
            f"(CEV-only blocks={cev_only_count}, ES-only blocks still taken by C/D="
            f"{es_only_count}, D early_stop rejects={d_es_rejects})."
        )
    elif es_only_count == 0 and cev_only_count + both_count > 0:
        relation = "overlapping_cev_superset"
        why_equal = (
            "Conditional EV blocked every trade early-stop would block "
            "(EARLY_STOP_ONLY_BLOCK=0)."
        )
    elif es_only_count > 0 and cev_only_count > 0:
        relation = "complementary_with_path_dependence"
        why_equal = (
            "Independent B and C block different sets; D follows C's path so "
            f"C_net==D_net is {c_net == d_net}."
        )
    else:
        relation = "other"
        why_equal = "Inspect category table."

    conclusion = {
        "redundant_overlapping_or_complementary": relation,
        "why_c_equals_d": why_equal,
        "path_dependence_row_count": len(path_dep_rows),
        "d_early_stop_reject_count": d_es_rejects,
        "pnl_bridge": {
            "baseline": marginal["baseline_net"],
            "after_early_stop": marginal["early_stop_only_net"],
            "after_conditional_ev": marginal["conditional_ev_only_net"],
            "combined": marginal["combined_net"],
            "improvement_es_unique_blocks": marginal["improvement_from_es_unique"],
            "improvement_cev_unique_blocks": marginal["improvement_from_cev_unique"],
            "improvement_overlap_blocks": marginal["improvement_from_overlap"],
            "bridge_check": (
                "baseline + (-es_unique) + (-cev_unique) + (-overlap) should equal "
                "combined when C path == D path for remaining takes"
            ),
        },
        "profitable_rejects": {
            "early_stop": failures["profitable_trades_rejected_by_early_stop"],
            "conditional_ev": failures["profitable_trades_rejected_by_conditional_ev"],
        },
        "large_losses_missed": {
            "early_stop": failures["large_losses_missed_by_early_stop"],
            "conditional_ev": failures["large_losses_missed_by_conditional_ev"],
        },
        "acted_first_on_primary_route": timing_bv.get("acted_first"),
        "claims_alpha": False,
    }

    return {
        "source": str(path),
        "data_status": "IN_SAMPLE_CAUSAL_REPLAY + EX-POST COUNTERFACTUAL OUTCOME",
        "trade_count": len(trades),
        "replay_totals": {
            k: {
                "taken": v.get("executed_opportunities"),
                "rejected": v.get("rejected_opportunities"),
                "realized_net": v.get("total_realized_net"),
            }
            for k, v in replays.items()
        },
        "table_a_decision_overlap": overlap,
        "table_b_pnl_attribution": categories,
        "table_c_conditional_ev_errors": failures,
        "table_d_mechanism_timing": {"all": timing_all, "primary_route": timing_bv},
        "marginal_contribution": marginal,
        "comparison_rows": rows,
        "conclusion": conclusion,
        "category_counts": {c: sum(1 for r in rows if r["category"] == c) for c in CATEGORIES},
    }


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "data/paper_25000live.json")
    report = build_attribution_report(path)
    dest = Path("data/decision_attribution_report.json")
    dest.write_text(json.dumps(report, indent=2, default=str))
    # Compact summary
    print(
        json.dumps(
            {
                "category_counts": report["category_counts"],
                "overlap": {
                    k: report["table_a_decision_overlap"][k]
                    for k in (
                        "trades_blocked_by_early_stop",
                        "trades_blocked_by_conditional_ev",
                        "intersection",
                        "early_stop_unique_blocks",
                        "conditional_ev_unique_blocks",
                    )
                },
                "marginal": report["marginal_contribution"],
                "timing_primary": {
                    k: report["table_d_mechanism_timing"]["primary_route"].get(k)
                    for k in (
                        "acted_first",
                        "first_conditional_ev_reject_event",
                        "first_early_stop_reject_event",
                        "event_gap_cev_minus_es",
                    )
                },
                "conclusion": report["conclusion"],
            },
            indent=2,
            default=str,
        )
    )
    print(f"\nWrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

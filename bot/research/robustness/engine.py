"""Run the edge-robustness lab on frozen H-0005 / H-0007. Execution off."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.robustness.accounting import audit_card
from bot.research.robustness.decision import research_decision, window_dominance
from bot.research.robustness.incremental import incremental_compare, regime_diversity
from bot.research.robustness.interpretation import gate_selectivity, interpretation_verdict
from bot.research.robustness.magnitude import magnitude
from bot.research.robustness.protocol import (
    FIRST_LAB_OOS_START_NS,
    FORENSIC_OOS_END_NS,
    FROZEN_H0005_PARAMS,
    FROZEN_H0007_PARAMS,
    HISTORICAL_MECHANICAL,
    LOOKBACK_BUFFER_NS,
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    STRIDE,
    build_manifest,
)
from bot.research.robustness.stress import break_even_frontier, stress_matrix
from bot.research.robustness.windows import sequential_windows
from bot.research.regime_lab.engine import _window_eval
from bot.research.regime_lab.families import (
    FreshnessCVDFamily,
    RegimeOnlyDescriptive,
    WideSpreadMRFamily,
)
from bot.research.tournament.families import (
    CrossVenueDislocationFamily,
    ShortHorizonMeanReversionFamily,
)
from bot.research.tournament.tape_index import build_tape_index


def _window_spec(w: dict[str, Any]) -> dict[str, Any]:
    return {
        "start_ts_ns": int(w["start_ts_ns"]),
        "end_ts_ns_inclusive": int(w["end_ts_ns_inclusive"]),
    }


def _summarize_window(m: dict[str, Any], audit: dict[str, Any], sel: dict[str, Any]) -> dict[str, Any]:
    stab = m.get("stability") or {}
    fills = m.get("completed_round_trips")
    net = m.get("NET")
    return {
        "signals": m.get("signals"),
        "fills": fills,
        "NET": net,
        "NET_unit": "EUR",
        "EXPECTED_NET": m.get("EXPECTED_NET"),
        "EXPECTED_NET_unit": "EUR_per_signal",
        "NET_per_fill_from_sum": (
            (float(net) / fills) if fills and net is not None else None
        ),
        "NET_per_fill_from_sum_unit": "EUR_per_estimated_fill",
        "gross": m.get("gross"),
        "fees": m.get("fees"),
        "slippage": m.get("slippage"),
        "adverse": m.get("adverse"),
        "maxDD": m.get("maximum_drawdown"),
        "positive": (net or 0) > 0,
        "gate_admission_rate": (
            (sel.get("admitted") / sel["candidates"]) if sel.get("candidates") else None
        ),
        "gate_selectivity": sel.get("selectivity"),
        "symbol_concentration": {
            "top": stab.get("top_symbol"),
            "top_share": stab.get("top_symbol_share"),
            "count": stab.get("symbol_count"),
        },
        "time_concentration": {"top_block_share": stab.get("top_block_share")},
        "route_universe": {
            "count": stab.get("route_count"),
            "top": stab.get("top_route"),
            "top_share": stab.get("top_route_share"),
            "ROUTE_UNIVERSE_LIMITED": stab.get("ROUTE_UNIVERSE_LIMITED"),
        },
        "audit": audit,
    }


def run_robustness_lab(
    *,
    research_path: Path | str = "data/research_marketdata",
    first_lab_results: Path | str = "data/regime_hypothesis_lab/results.json",
    out_dir: Path | str | None = None,
    max_events: int | None = None,
    stride: int = STRIDE,
) -> dict[str, Any]:
    hist_path = Path(first_lab_results)
    if not hist_path.exists():
        out = {
            "STATUS": "NO_FIRST_LAB_RESULTS",
            "ACCOUNTING_AUDIT": "FAIL",
            "PRODUCTION_EXECUTION": "DISABLED",
        }
        _write(out, out_dir)
        return out

    hist = json.loads(hist_path.read_text(encoding="utf-8"))
    min_ts = int(FIRST_LAB_OOS_START_NS) - int(LOOKBACK_BUFFER_NS)
    index = build_tape_index(
        Path(research_path),
        max_events=max_events,
        stride=stride,
        min_ts_ns=min_ts,
        parse_inventory_events=False,
    )
    plan = sequential_windows(index)
    manifest = build_manifest(
        extra={
            "stride": stride,
            "min_ts_ns": min_ts,
            "dataset_id": index.dataset_id,
            "dataset_fingerprint": index.content_fingerprint,
            "split_plan": {k: v for k, v in plan.items() if k != "windows"},
            "n_windows": len(plan.get("windows") or []),
            "forensic_oos_end_ns": FORENSIC_OOS_END_NS,
        }
    )

    fams = {
        "H-0005": {
            "gated": FreshnessCVDFamily(),
            "parent": CrossVenueDislocationFamily(),
            "regime": RegimeOnlyDescriptive(regime="fresh"),
            "kind": "cvd",
            "params": dict(FROZEN_H0005_PARAMS),
            "regime_required": False,
            "venue": "okx",
            "venue_exit": "bitvavo",
        },
        "H-0007": {
            "gated": WideSpreadMRFamily(),
            "parent": ShortHorizonMeanReversionFamily(),
            "regime": RegimeOnlyDescriptive(regime="wide"),
            "kind": "mr",
            "params": dict(FROZEN_H0007_PARAMS),
            "regime_required": True,
            "venue": "bitvavo",
            "venue_exit": None,
        },
    }

    cards: dict[str, Any] = {}
    stress_sidecars: dict[str, list[dict[str, Any]]] = {}

    for hid, spec in fams.items():
        cards[hid] = _run_one(
            hid,
            spec,
            hist=hist,
            index=index,
            plan=plan,
            stress_sidecars=stress_sidecars,
        )

    out = {
        "STATUS": "COMPLETE",
        "PACKAGE": PACKAGE_LABEL,
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": manifest,
        "ACCOUNTING_AUDIT": (
            "PASS"
            if all((cards[h] or {}).get("ACCOUNTING_AUDIT") == "PASS" for h in cards)
            else "FAIL"
        ),
        "H-0005": cards.get("H-0005"),
        "H-0007": cards.get("H-0007"),
        "DATA_STATUS": plan.get("DATA_STATUS"),
        "PRODUCTION_EXECUTION": "DISABLED",
        "execution_enabled": False,
        "new_hypotheses": [],
        "thresholds_tuned": False,
        "cost_model_changed": False,
        "mechanical_verdicts_unchanged": True,
        "notes": [
            "Mechanical OOS_PASS is historical and was not rewritten.",
            "Stress overlay is research-only.",
            "NET is EUR sum; first-lab NET/fill was mean-edge replay / estimated fills.",
        ],
    }
    _write(out, out_dir, stress_sidecars)
    return out


def _run_one(
    hid: str,
    spec: dict[str, Any],
    *,
    hist: dict[str, Any],
    index,
    plan: dict[str, Any],
    stress_sidecars: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    hcard = hist.get(hid) or {}
    controls = (hist.get("CONTROL_RESULTS") or {}).get(hid) or {}
    mechanical = str(hcard.get("VERDICT") or HISTORICAL_MECHANICAL.get(hid) or "UNKNOWN")
    acc = audit_card(hcard)
    params = spec["params"]
    kind = spec["kind"]
    oos_hist = hcard.get("OOS_RESULT") or {}
    expected = oos_hist.get("EXPECTED_NET")
    mag = magnitude(
        expected_net=float(expected or 0.0),
        venue=spec["venue"],
        venue_exit=spec["venue_exit"],
        mean_forward=(hcard.get("metrics_oos") or {}).get("mean_forward"),
    )
    be = break_even_frontier(
        expected_net=float(expected or 0.0),
        venue=spec["venue"],
        venue_exit=spec["venue_exit"],
    )
    stress = stress_matrix(
        expected_net=float(expected or 0.0),
        venue=spec["venue"],
        venue_exit=spec["venue_exit"],
        signals=int(oos_hist.get("signals") or 0),
    )
    cells = stress.pop("cells")
    stress_sidecars[hid] = cells

    window_rows: list[dict[str, Any]] = []
    window_nets: list[float] = []
    agg_adm = agg_cand = agg_rej = 0
    parent_n_hist = int((controls.get("parent") or {}).get("signals") or 0)
    diversity_acc: list[dict[str, Any]] = []

    for w in plan.get("windows") or []:
        spec_w = _window_spec(w)
        gated_m = _window_eval(spec["gated"], index, spec_w, params, kind, inclusive=True)
        parent_m = _window_eval(spec["parent"], index, spec_w, params, kind, inclusive=True)
        regime_m = _window_eval(spec["regime"], index, spec_w, params, kind, inclusive=True)
        no_trade_m = {
            "signals": 0,
            "NET": 0.0,
            "EXPECTED_NET": 0.0,
            "NET_per_fill": 0.0,
            "maximum_drawdown": 0.0,
            "stability": {},
        }
        g_audit = dict(gated_m.get("audit") or {})
        sel = gate_selectivity(
            admitted=int(g_audit.get("admitted") or gated_m.get("signals") or 0),
            candidates=int(g_audit.get("candidates") or 0),
            parent_signals=int(parent_m.get("signals") or 0),
            rejected=int(g_audit.get("rejected") or 0),
        )
        inc = incremental_compare(
            {
                "parent": parent_m,
                "gated": gated_m,
                "regime_only": regime_m,
                "no_trade": no_trade_m,
            }
        )
        div = regime_diversity(
            index=index,
            start_ns=int(w["start_ts_ns"]),
            end_ns_inclusive=int(w["end_ts_ns_inclusive"]),
            venue="bitvavo" if hid == "H-0007" else spec["venue"],
            audit=g_audit,
            required=spec["regime_required"],
        )
        diversity_acc.append(div)
        row = {
            "WINDOW_ID": w["WINDOW_ID"],
            "kind": w["kind"],
            "complete": w.get("complete"),
            "start_ts_ns": w["start_ts_ns"],
            "end_ts_ns_inclusive": w["end_ts_ns_inclusive"],
            "duration_seconds": w.get("duration_seconds"),
            **_summarize_window(gated_m, g_audit, sel),
            "incremental": inc,
            "regime_diversity": div,
            "selectivity": sel,
        }
        window_rows.append(row)
        if w.get("complete") and gated_m.get("NET") is not None:
            window_nets.append(float(gated_m["NET"]))
        agg_adm += int(sel.get("admitted") or 0)
        agg_cand += int(sel.get("candidates") or 0)
        agg_rej += int(sel.get("rejected") or 0)

    agg_sel = gate_selectivity(
        admitted=agg_adm, candidates=agg_cand, parent_signals=None, rejected=agg_rej
    )
    hist_audit = hcard.get("audit") or (hcard.get("metrics_oos") or {}).get("audit") or {}
    hist_sel = gate_selectivity(
        admitted=int(hist_audit.get("admitted") or oos_hist.get("accepted_events") or 0),
        candidates=int(hist_audit.get("candidates") or oos_hist.get("candidate_events") or 0),
        parent_signals=parent_n_hist,
        rejected=int(hist_audit.get("rejected") or 0),
    )
    both_states = any(d.get("both_states") for d in diversity_acc) if diversity_acc else False
    tape_both = any(d.get("tape_has_both_spread_states") for d in diversity_acc)
    gate_both = any(d.get("gate_admits_and_rejects") for d in diversity_acc)
    rd = {
        "required": spec["regime_required"],
        "both_states": both_states if spec["regime_required"] else True,
        "tape_has_both_spread_states": tape_both,
        "gate_admits_and_rejects": gate_both,
        "windows": diversity_acc,
    }
    independently_positive = float(expected or 0) > 0 and mechanical == "OOS_PASS"
    hp = (controls.get("parent") or {}).get("EXPECTED_NET")
    hg = (controls.get("gated") or {}).get("EXPECTED_NET")
    hist_inc_pos = None if hp is None or hg is None else float(hg) > float(hp)
    same_params_pos = False
    deltas = [
        r["incremental"]["delta_EXPECTED_NET_gated_minus_parent"]
        for r in window_rows
        if r.get("complete")
        and r["incremental"].get("delta_EXPECTED_NET_gated_minus_parent") is not None
    ]
    if deltas:
        same_params_pos = sum(float(x) for x in deltas) > 0
    n_complete = sum(1 for r in window_rows if r.get("complete"))
    sel_for_interp = hist_sel if hist_sel.get("inactive") else agg_sel
    interp = interpretation_verdict(
        mechanical=mechanical,
        selectivity=sel_for_interp,
        regime_diversity=rd,
        edge_to_uncertainty=mag.get("EDGE_TO_MODEL_UNCERTAINTY_RATIO"),
        incremental_positive=same_params_pos if hid == "H-0005" else hist_inc_pos,
        independent_windows=n_complete,
        independently_positive=independently_positive,
    )
    if hid == "H-0007" and hist_sel.get("inactive"):
        interp = "GATE_INACTIVE"

    accounting_pass = acc.get("ACCOUNTING_AUDIT") == "PASS"
    final = research_decision(
        accounting_pass=accounting_pass,
        interpretation=interp,
        independent_windows=n_complete,
        window_nets=window_nets,
        survives_reasonable_stress=bool(stress.get("survives_reasonable_stress")),
        gate_selective=not (hist_sel.get("inactive") or agg_sel.get("inactive")),
        parent_comparison_available=True,
        production_loosened=False,
        model_uncertainty_too_high=bool(mag.get("MODEL_UNCERTAINTY_TOO_HIGH")),
        regime_diversity_ok=bool(rd.get("both_states")),
        required_regime_diversity=spec["regime_required"],
    )
    return {
        "hypothesis_id": hid,
        "MECHANICAL_VERDICT": mechanical,
        "INTERPRETATION_VERDICT": interp,
        "GATE_SELECTIVITY": hist_sel,
        "GATE_SELECTIVITY_AGGREGATE": agg_sel,
        "INCREMENTAL_VALUE": {
            "historical_refit_parent": {
                "parent_EXPECTED_NET": hp,
                "gated_EXPECTED_NET": hg,
                "positive": hist_inc_pos,
            },
            "same_params_windows": [
                {
                    "WINDOW_ID": r["WINDOW_ID"],
                    "delta_EXPECTED_NET": r["incremental"]["delta_EXPECTED_NET_gated_minus_parent"],
                    "INCREMENTAL_VALUE": r["incremental"]["INCREMENTAL_VALUE"],
                    "parent_signals": r["incremental"]["parent"]["signals"],
                    "gated_signals": r["incremental"]["gated"]["signals"],
                }
                for r in window_rows
            ],
            "same_params_aggregate_positive": same_params_pos,
        },
        "EDGE_TO_COST_RATIO": mag.get("EDGE_TO_COST_RATIO"),
        "EDGE_TO_MODEL_UNCERTAINTY_RATIO": mag.get("EDGE_TO_MODEL_UNCERTAINTY_RATIO"),
        "MODEL_UNCERTAINTY": mag,
        "BREAK_EVEN_ADVERSE": be.get("BREAK_EVEN_ADVERSE_BPS"),
        "BREAK_EVEN_FEES": be.get("BREAK_EVEN_FEE_BPS"),
        "BREAK_EVEN_SLIPPAGE": be.get("BREAK_EVEN_SLIPPAGE_BPS"),
        "BREAK_EVEN_FRONTIER": be,
        "STRESS": stress,
        "INDEPENDENT_OOS_WINDOWS": n_complete,
        "window_dominance": window_dominance(window_nets),
        "windows": window_rows,
        "REGIME_DIVERSITY": rd,
        "REPLICATION_STATUS": (
            "FIRST_OOS_ONLY" if n_complete <= 1 else f"FIRST_OOS_PLUS_{n_complete - 1}_WALKFORWARD"
        ),
        "FINAL_RESEARCH_DECISION": final,
        "ACCOUNTING_AUDIT": acc.get("ACCOUNTING_AUDIT"),
        "accounting": acc,
        "historical_NET_per_fill": hcard.get("NET/fill"),
        "production_assumptions_loosened": False,
        "execution_enabled": False,
    }


def _write(
    out: dict[str, Any],
    out_dir: Path | str | None,
    stress: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    dest = Path(out_dir or "data/edge_robustness_lab")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "results.json").write_text(
        json.dumps(out, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    if stress:
        for hid, cells in stress.items():
            path = dest / f"stress_{hid}.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for cell in cells:
                    handle.write(json.dumps(cell, sort_keys=True, default=str) + "\n")
    compact = {
        "label": PACKAGE_LABEL,
        "STATUS": out.get("STATUS"),
        "ACCOUNTING_AUDIT": out.get("ACCOUNTING_AUDIT"),
        "PRODUCTION_EXECUTION": "DISABLED",
        "disclaimer": (
            "Mechanical OOS_PASS unchanged. Interpretation is a second layer. "
            "Research-only stress. Execution disabled."
        ),
        "rows": [
            _dash_row(out.get("H-0005") or {}, "H-0005"),
            _dash_row(out.get("H-0007") or {}, "H-0007"),
        ],
        "manifest": out.get("manifest"),
    }
    target = Path("data/edge_robustness_lab")
    if dest.resolve() == target.resolve() or dest.resolve().is_relative_to(target.resolve()):
        Path("data/edge_robustness_lab_report.json").write_text(
            json.dumps(compact, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )


def _dash_row(card: dict[str, Any], hid: str) -> dict[str, Any]:
    be_a = card.get("BREAK_EVEN_ADVERSE") or {}
    be_f = card.get("BREAK_EVEN_FEES") or {}
    be_s = card.get("BREAK_EVEN_SLIPPAGE") or {}
    worst = (card.get("STRESS") or {}).get("worst") or {}
    acc = card.get("accounting") or {}
    units = (acc.get("units") or {}).get("NET_per_fill_from_replay") or {}
    hist_sel = card.get("GATE_SELECTIVITY") or {}
    return {
        "ID": hid,
        "mechanical_verdict": card.get("MECHANICAL_VERDICT"),
        "interpretation_verdict": card.get("INTERPRETATION_VERDICT"),
        "NET_per_fill": units.get("value") if units else card.get("historical_NET_per_fill"),
        "NET_per_fill_unit": units.get("unit"),
        "edge_to_cost": card.get("EDGE_TO_COST_RATIO"),
        "edge_to_uncertainty": card.get("EDGE_TO_MODEL_UNCERTAINTY_RATIO"),
        "break_even_adverse": be_a.get("value"),
        "break_even_fee": be_f.get("value"),
        "break_even_slippage": be_s.get("value"),
        "worst_stress_NET": worst.get("EXECUTION_NET"),
        "worst_stress_sign": worst.get("sign"),
        "independent_oos_windows": card.get("INDEPENDENT_OOS_WINDOWS"),
        "gate_selectivity": hist_sel.get("selectivity"),
        "gate_inactive": hist_sel.get("inactive"),
        "parent_comparison": (card.get("INCREMENTAL_VALUE") or {}).get("historical_refit_parent"),
        "replication_status": card.get("REPLICATION_STATUS"),
        "final_research_decision": card.get("FINAL_RESEARCH_DECISION"),
        "accounting_audit": card.get("ACCOUNTING_AUDIT"),
        "regime_diversity": (card.get("REGIME_DIVERSITY") or {}).get("both_states"),
    }

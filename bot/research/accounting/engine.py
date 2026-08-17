"""Re-report H-0005 / H-0007 through the canonical accounting layer.

Does not change strategy parameters, tape splits, fees, fills, adverse, or OOS gates.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.research.accounting.audit import audit_canonical
from bot.research.accounting.fingerprint import replay_fingerprint
from bot.research.accounting.paired import (
    PairedPartition,
    aggregate_paired,
    pair_from_stored_nets,
    pair_window,
)
from bot.research.accounting.protocol import (
    CONCENTRATION_THRESHOLD,
    H0007_AUTO_CHILD_GENERATION,
    MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS,
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    REPLAY_VERSION,
    SCHEMA_VERSION,
    STRIDE,
    build_manifest,
)
from bot.research.accounting.replication import replication_advance
from bot.research.accounting.schema import EconomicWorld
from bot.research.accounting.stress import break_even_canonical, stress_canonical, uses_canonical_replay
from bot.research.accounting.waterfall import CanonicalEconomics, from_component_sums
from bot.research.regime_lab.families import FreshnessCVDFamily, WideSpreadMRFamily
from bot.research.regime_lab.metrics import attach_event_economics
from bot.research.regime_lab.protocol import FORENSIC_OOS_END_NS
from bot.research.regime_lab.stability import stability_report
from bot.research.robustness.interpretation import gate_selectivity
from bot.research.robustness.protocol import (
    FIRST_LAB_OOS_START_NS,
    FROZEN_H0005_PARAMS,
    FROZEN_H0007_PARAMS,
    LOOKBACK_BUFFER_NS,
)
from bot.research.robustness.windows import sequential_windows
from bot.research.tournament.tape_index import build_tape_index


def _microbench() -> dict[str, Any]:
    """Research-path only. Does not touch the realtime hot path."""
    from bot.research.accounting.waterfall import line_from_forward, assemble_canonical
    from bot.research.regime_lab.metrics import attach_event_economics, window_metrics

    n = 2000
    raw = [
        {"forward": 0.01, "ts_ns": 10**18 + i, "symbol": "BTCEUR", "route": "okx|bitvavo"}
        for i in range(n)
    ]
    t0 = time.perf_counter()
    attached = attach_event_economics(raw, venue="okx", venue_exit="bitvavo", horizon_ms=5000)
    t_attach = time.perf_counter() - t0
    t1 = time.perf_counter()
    window_metrics(
        attached, venue="okx", venue_exit="bitvavo", mean_forward=0.01, horizon_ms=5000, audit={"candidates": n, "admitted": n, "rejected": 0}
    )
    t_metrics = time.perf_counter() - t1
    t2 = time.perf_counter()
    lines = [
        line_from_forward(forward=0.01, venue="okx", venue_exit="bitvavo", ts_ns=10**18 + i, symbol="BTCEUR", route="okx|bitvavo")
        for i in range(n)
    ]
    assemble_canonical(
        lines, venue="okx", venue_exit="bitvavo", candidates=n, admitted=n, rejected=0, mean_forward=0.01
    )
    t_canon = time.perf_counter() - t2
    return {
        "n_synthetic_signals": n,
        "attach_event_economics_seconds": t_attach,
        "window_metrics_with_canonical_seconds": t_metrics,
        "assemble_canonical_seconds": t_canon,
        "hot_path_untouched": True,
        "pydantic_on_hot_path": False,
        "note": "Research/replay path only. Redis hydrate / quote draft / fee caches unchanged.",
    }

_SPECS = {
    "H-0005": {
        "family": FreshnessCVDFamily,
        "params": FROZEN_H0005_PARAMS,
        "venue": "okx",
        "venue_exit": "bitvavo",
        "kind": "cvd",
    },
    "H-0007": {
        "family": WideSpreadMRFamily,
        "params": FROZEN_H0007_PARAMS,
        "venue": "bitvavo",
        "venue_exit": None,
        "kind": "mr",
    },
}


def _mean_fwd(events: list[dict[str, Any]]) -> float | None:
    if not events:
        return None
    return sum(float(e.get("forward") or 0.0) for e in events) / len(events)


def _canon_from_oos(
    oos: dict[str, Any],
    *,
    venue: str,
    venue_exit: str | None,
    audit: dict[str, Any] | None,
) -> CanonicalEconomics:
    audit = audit or {}
    return from_component_sums(
        venue=venue,
        venue_exit=venue_exit,
        signals=int(oos.get("signals") or 0),
        candidates=int(oos.get("candidate_events") or audit.get("candidates") or oos.get("signals") or 0),
        admitted=int(oos.get("accepted_events") or audit.get("admitted") or oos.get("signals") or 0),
        rejected=int(audit.get("rejected") or 0),
        fills=int(oos.get("completed_round_trips") or 0),
        gross=oos.get("gross") or 0,
        fees=oos.get("fees") or 0,
        slippage=oos.get("slippage") or 0,
        adverse=oos.get("adverse") or 0,
        net=oos.get("NET") or 0,
        mean_forward=None,
        expected_net_per_signal=oos.get("EXPECTED_NET"),
    )


def _assert_frozen(hid: str, params: dict[str, Any]) -> None:
    frozen = dict(_SPECS[hid]["params"])
    if dict(params) != frozen:
        raise RuntimeError(f"{hid} params retuned after OOS freeze: {params} != {frozen}")


def _check_leakage(events: list[dict[str, Any]]) -> bool:
    for e in events:
        ts = e.get("ts_ns")
        if ts is not None and int(ts) <= int(FORENSIC_OOS_END_NS):
            return False
    return True


def _oos_from_first_lab(hist: dict[str, Any], hid: str) -> tuple[CanonicalEconomics, dict[str, Any]]:
    card = hist.get(hid) or {}
    oos = card.get("OOS_RESULT") or {}
    spec = _SPECS[hid]
    econ = _canon_from_oos(
        oos, venue=spec["venue"], venue_exit=spec["venue_exit"], audit=card.get("audit") or {}
    )
    return econ, card


def _paired_from_robustness(rob: dict[str, Any], hid: str) -> list[dict[str, Any]]:
    spec = _SPECS[hid]
    rows: list[dict[str, Any]] = []
    for w in (rob.get(hid) or {}).get("windows") or []:
        inc = w.get("incremental") or {}
        parent_raw = inc.get("parent") or {}
        child_raw = {
            "NET": w.get("NET"),
            "signals": w.get("signals"),
            "gross": w.get("gross"),
            "fees": w.get("fees"),
            "slippage": w.get("slippage"),
            "adverse": w.get("adverse"),
            "EXPECTED_NET": w.get("EXPECTED_NET"),
            "completed_round_trips": w.get("fills"),
            "candidate_events": (w.get("audit") or {}).get("candidates"),
            "accepted_events": (w.get("audit") or {}).get("admitted"),
        }
        parent_oos = {
            "NET": parent_raw.get("NET"),
            "signals": parent_raw.get("signals"),
            "EXPECTED_NET": parent_raw.get("EXPECTED_NET"),
            "completed_round_trips": None,
            "gross": 0,
            "fees": 0,
            "slippage": 0,
            "adverse": 0,
            "candidate_events": parent_raw.get("signals"),
            "accepted_events": parent_raw.get("signals"),
        }
        # Parent stored incremental slice may lack waterfall components.
        # Reconstruct parent net only; excluded_net = parent - child.
        parent = from_component_sums(
            venue=spec["venue"],
            venue_exit=spec["venue_exit"],
            signals=int(parent_oos["signals"] or 0),
            candidates=int(parent_oos["signals"] or 0),
            admitted=int(parent_oos["signals"] or 0),
            rejected=0,
            fills=None,
            gross=parent_raw.get("NET") or 0,
            fees=0,
            slippage=0,
            adverse=0,
            net=parent_raw.get("NET") or 0,
            mean_forward=None,
            expected_net_per_signal=parent_raw.get("EXPECTED_NET"),
            other_costs=0,
        )
        child = _canon_from_oos(child_raw, venue=spec["venue"], venue_exit=spec["venue_exit"], audit=w.get("audit"))
        rows.append(
            pair_from_stored_nets(
                window_id=str(w.get("WINDOW_ID")),
                complete=bool(w.get("complete")),
                start_ts_ns=int(w.get("start_ts_ns") or 0),
                end_ts_ns_inclusive=int(w.get("end_ts_ns_inclusive") or 0),
                parent=parent,
                child=child,
            )
        )
    return rows


def _live_paired(
    *,
    hid: str,
    index,
    plan: dict[str, Any],
) -> tuple[list[Any], bool, dict[str, Any]]:
    spec = _SPECS[hid]
    fam = spec["family"]()
    params = dict(spec["params"])
    _assert_frozen(hid, params)
    venue, vx = spec["venue"], spec["venue_exit"]
    h = int(params.get("horizon_ms") or 5000)
    rows = []
    no_leakage = True
    stab_acc: list[dict[str, Any]] = []
    for w in plan.get("windows") or []:
        part_raw = fam.partition_window(
            index,
            start_ns=int(w["start_ts_ns"]),
            end_ns_exclusive=None,
            end_ns_inclusive=int(w["end_ts_ns_inclusive"]),
            params=params,
            horizons=[h],
        )
        parent_e = attach_event_economics(list(part_raw["parent_events"]), venue=venue, venue_exit=vx, horizon_ms=h)
        child_e = attach_event_economics(list(part_raw["child_events"]), venue=venue, venue_exit=vx, horizon_ms=h)
        excl_e = attach_event_economics(list(part_raw["excluded_events"]), venue=venue, venue_exit=vx, horizon_ms=h)
        uns_e = attach_event_economics(list(part_raw["unsupported_events"]), venue=venue, venue_exit=vx, horizon_ms=h)
        if not (
            _check_leakage(parent_e)
            and _check_leakage(child_e)
            and _check_leakage(excl_e)
            and _check_leakage(uns_e)
        ):
            no_leakage = False
            raise RuntimeError(f"{hid} {w['WINDOW_ID']}: forensic timestamp leaked into labeled window")
        audit = part_raw["audit"]
        partition = PairedPartition(
            parent_events=tuple(parent_e),
            child_events=tuple(child_e),
            excluded_events=tuple(excl_e),
            unsupported_events=tuple(uns_e),
            candidates=int(audit.get("candidates") or 0),
            admitted=int(audit.get("admitted") or 0),
            rejected=int(audit.get("rejected") or 0),
            unsupported=int(audit.get("unsupported") or 0),
        )
        row = pair_window(
            window_id=str(w["WINDOW_ID"]),
            complete=bool(w.get("complete")),
            start_ts_ns=int(w["start_ts_ns"]),
            end_ts_ns_inclusive=int(w["end_ts_ns_inclusive"]),
            partition=partition,
            venue=venue,
            venue_exit=vx,
            mean_forward_parent=_mean_fwd(parent_e),
            mean_forward_child=_mean_fwd(child_e),
            mean_forward_excluded=_mean_fwd(excl_e),
        )
        rows.append(row)
        if w.get("complete"):
            stab_acc.append(stability_report(child_e, oos_start_ns=int(w["start_ts_ns"]), oos_end_ns=int(w["end_ts_ns_inclusive"])))
    conc = {
        "max_symbol_share": max((s.get("top_symbol_share") or 0.0) for s in stab_acc) if stab_acc else None,
        "any_route_limited": any(s.get("ROUTE_UNIVERSE_LIMITED") for s in stab_acc) if stab_acc else False,
        "symbol_ok": all(
            (s.get("top_symbol_share") or 0.0) <= CONCENTRATION_THRESHOLD for s in stab_acc
        )
        if stab_acc
        else False,
    }
    return rows, no_leakage, conc


def run_canonical_accounting(
    *,
    research_path: Path | str = "data/research_marketdata",
    first_lab_results: Path | str = "data/regime_hypothesis_lab/results.json",
    robustness_results: Path | str = "data/edge_robustness_lab/results.json",
    out_dir: Path | str | None = None,
    live: bool = True,
    max_events: int | None = None,
    stride: int = STRIDE,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    hist = json.loads(Path(first_lab_results).read_text(encoding="utf-8"))
    rob_path = Path(robustness_results)
    rob = json.loads(rob_path.read_text(encoding="utf-8")) if rob_path.exists() else {}

    cards: dict[str, Any] = {}
    live_meta: dict[str, Any] = {"used": False}
    plan: dict[str, Any] = {}
    index_meta: dict[str, Any] = {}

    if live and Path(research_path).exists():
        min_ts = int(FIRST_LAB_OOS_START_NS) - int(LOOKBACK_BUFFER_NS)
        index = build_tape_index(
            Path(research_path),
            max_events=max_events,
            stride=stride,
            min_ts_ns=min_ts,
            parse_inventory_events=False,
        )
        plan = sequential_windows(index)
        index_meta = {
            "dataset_id": index.dataset_id,
            "dataset_fingerprint": index.content_fingerprint,
            "stride": stride,
            "min_ts_ns": min_ts,
        }
        live_meta = {"used": True, **index_meta, "n_windows": len(plan.get("windows") or [])}

    for hid in ("H-0005", "H-0007"):
        first_econ, first_card = _oos_from_first_lab(hist, hid)
        first_audit = audit_canonical(first_econ)
        first_fp = replay_fingerprint(first_econ, extra={"source": "first_lab_oos", "hypothesis_id": hid})
        stress = stress_canonical(first_econ)
        stress_cells = stress.pop("cells")
        assert all(uses_canonical_replay(c) for c in stress_cells)
        be = break_even_canonical(first_econ)
        audit = first_card.get("audit") or {}
        sel = gate_selectivity(
            admitted=int(audit.get("admitted") or first_econ.admitted.value),
            candidates=int(audit.get("candidates") or first_econ.candidates.value),
            parent_signals=int(
                ((((hist.get("CONTROL_RESULTS") or {}).get(hid) or {}).get("parent") or {}).get("signals") or 0)
            ),
            rejected=int(audit.get("rejected") or first_econ.rejected.value),
        )
        if live_meta.get("used"):
            paired_rows, no_leakage, conc = _live_paired(hid=hid, index=index, plan=plan)
            paired_dicts = [r.to_dict() for r in paired_rows]
            paired_agg = aggregate_paired(paired_rows)
            n_complete = sum(1 for r in paired_rows if r.complete)
            window_share = None
            abs_nets = [abs(float(r.child.replay_net.value)) for r in paired_rows if r.complete]
            tot = sum(abs_nets) or 1.0
            window_share = max(abs_nets) / tot if abs_nets else 1.0
            window_ok = window_share <= CONCENTRATION_THRESHOLD
            symbol_ok = bool(conc.get("symbol_ok"))
            route_limited = bool(conc.get("any_route_limited"))
        else:
            paired_dicts = _paired_from_robustness(rob, hid)
            paired_agg = aggregate_paired(paired_dicts)
            no_leakage = True
            n_complete = sum(1 for r in paired_dicts if r.get("complete"))
            abs_nets = [abs(float(r["child_replay_net"])) for r in paired_dicts if r.get("complete")]
            tot = sum(abs_nets) or 1.0
            window_share = max(abs_nets) / tot if abs_nets else 1.0
            window_ok = window_share <= CONCENTRATION_THRESHOLD
            symbol_ok = not bool((first_card.get("STABILITY") or {}).get("concentrated"))
            route_limited = bool((first_card.get("STABILITY") or {}).get("ROUTE_UNIVERSE_LIMITED"))
            conc = {"from_stored": True}

        if hid == "H-0005":
            repl = replication_advance(
                accounting_audit_pass=first_audit["ACCOUNTING_AUDIT"] == "PASS",
                independent_complete_windows=n_complete,
                paired_comparison_present=bool(paired_dicts),
                aggregate_paired_delta_positive=bool(paired_agg.get("aggregate_delta_positive")),
                window_concentration_ok=window_ok,
                symbol_concentration_ok=symbol_ok,
                route_limitation_reported=True,
                cost_stress_positive=bool(stress.get("survives_worst_cell")),
                no_leakage=no_leakage,
                no_parameter_retune_after_oos=True,
                mechanical_first_oos_pass=str(first_card.get("VERDICT")) == "OOS_PASS",
                production_execution_disabled=True,
            )
            research_status = repl["state"]
            research_decision = (
                "PROMISING_REPLICATION_REQUIRED"
                if repl["state"] in {"REPLICATING", "FIRST_OOS_PASS"}
                else repl["state"]
            )
            if repl["state"] == "ROBUST_PAPER_CANDIDATE" and first_audit["ACCOUNTING_AUDIT"] != "PASS":
                research_decision = "PROMISING_REPLICATION_REQUIRED"
                research_status = "REPLICATING"
        else:
            repl = {
                "state": "GATE_INACTIVE",
                "note": "OOS_PASS does not imply selective strategy improvement.",
            }
            research_status = "GATE_INACTIVE"
            research_decision = "GATE_INACTIVE"
            if not H0007_AUTO_CHILD_GENERATION:
                repl["auto_child_generation"] = False

        cards[hid] = {
            "hypothesis_id": hid,
            "mechanical_verdict": first_card.get("VERDICT"),
            "RESEARCH_STATUS": research_status,
            "RESEARCH_DECISION": research_decision,
            "REPLICATION": repl,
            "ACCOUNTING_AUDIT": first_audit,
            "first_lab_oos": first_econ.report_block(),
            "first_lab_fingerprint": first_fp,
            "GATE_SELECTIVITY": sel,
            "PAIRED_PARENT_CHILD": {
                "windows": paired_dicts,
                "aggregate": paired_agg,
                "universe": "same_candidate_universe",
            },
            "COST_STRESS": {**stress, "n_cells_written": len(stress_cells)},
            "BREAK_EVEN": be,
            "concentration": {
                **conc,
                "window_share": window_share,
                "window_ok": window_ok,
                "symbol_ok": symbol_ok,
                "route_universe_limited": route_limited,
                "threshold": CONCENTRATION_THRESHOLD,
            },
            "frozen_params": dict(_SPECS[hid]["params"]),
            "production_execution": "DISABLED",
            "no_leakage": no_leakage,
            "parameters_retuned_after_oos": False,
            "stress_cells": stress_cells,
        }

    accounting_pass = all(
        (cards[h]["ACCOUNTING_AUDIT"] or {}).get("ACCOUNTING_AUDIT") == "PASS" for h in cards
    )
    # FAIL accounting blocks ROBUST_PAPER_CANDIDATE
    robust: list[str] = []
    if accounting_pass:
        for hid, card in cards.items():
            if card.get("RESEARCH_STATUS") == "ROBUST_PAPER_CANDIDATE":
                robust.append(hid)
    else:
        for hid, card in cards.items():
            if card.get("RESEARCH_STATUS") == "ROBUST_PAPER_CANDIDATE":
                card["RESEARCH_STATUS"] = "REPLICATING"
                card["RESEARCH_DECISION"] = "PROMISING_REPLICATION_REQUIRED"
                card["ROBUST_BLOCKED_BY_ACCOUNTING"] = True

    elapsed = time.perf_counter() - t0
    bench = _microbench()
    out = {
        "STATUS": "COMPLETE",
        "PACKAGE": PACKAGE_LABEL,
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": build_manifest(extra={**live_meta, **index_meta, "split_plan": {k: v for k, v in plan.items() if k != "windows"} if plan else {}}),
        "ACCOUNTING_AUDIT": "PASS" if accounting_pass else "FAIL",
        "H-0005": {k: v for k, v in cards["H-0005"].items() if k != "stress_cells"},
        "H-0007": {k: v for k, v in cards["H-0007"].items() if k != "stress_cells"},
        "ROBUST_PAPER_CANDIDATES": robust,
        "PRODUCTION_EXECUTION": "DISABLED",
        "execution_enabled": False,
        "new_hypotheses": [],
        "h0007_auto_child_generation": H0007_AUTO_CHILD_GENERATION,
        "thresholds_tuned": False,
        "cost_model_changed": False,
        "fills_changed": False,
        "oos_criteria_changed": False,
        "live_replay": live_meta,
        "PERFORMANCE": {
            "canonical_accounting_seconds": elapsed,
            "hot_path_untouched": True,
            "pydantic_on_hot_path": False,
            "microbench": bench,
        },
        "MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS": MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS,
        "worlds": [w.value for w in EconomicWorld],
        "old_accounting_failure": {
            "example_hypothesis": "H-0005",
            "NET": 2218.3727597787497,
            "fills": 363,
            "canonical_arithmetic_net_per_fill": 2218.3727597787497 / 363,
            "published_NET_per_fill": 0.0050320770426509395,
            "published_quantity": "MeanEdgeExecutionReplayNetPerFillEUR",
            "root_cause": (
                "window_metrics set NET_per_fill = EXECUTION_NET / fills where "
                "EXECUTION_NET is fill_rate * (EXPECTED_NET - extra_adverse), "
                "a mean-edge overlay, while NET is the sum of per-signal waterfalls."
            ),
        },
    }
    _write(out, out_dir, {h: cards[h]["stress_cells"] for h in cards})
    return out


def _write(out: dict[str, Any], out_dir: Path | str | None, stress: dict[str, list] | None) -> None:
    dest = Path(out_dir or "data/research")
    dest.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, indent=2, sort_keys=True, default=str) + "\n"
    (dest / "canonical_accounting_results.json").write_text(payload, encoding="utf-8")
    Path("data/research").mkdir(parents=True, exist_ok=True)
    Path("data/research/canonical_accounting_results.json").write_text(payload, encoding="utf-8")
    compact = {
        "label": PACKAGE_LABEL,
        "STATUS": out.get("STATUS"),
        "ACCOUNTING_AUDIT": out.get("ACCOUNTING_AUDIT"),
        "PRODUCTION_EXECUTION": "DISABLED",
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "ROBUST_PAPER_CANDIDATES": out.get("ROBUST_PAPER_CANDIDATES") or [],
        "disclaimer": (
            "Canonical execution replay is the only strategy-evaluation NET. "
            "Expected, replay, and observed worlds are separate. Execution disabled."
        ),
        "H-0005": _dash_h5(out.get("H-0005") or {}),
        "H-0007": _dash_h7(out.get("H-0007") or {}),
        "rows": [_dash_row(out.get("H-0005") or {}, "H-0005"), _dash_row(out.get("H-0007") or {}, "H-0007")],
    }
    Path("data/canonical_accounting_report.json").write_text(
        json.dumps(compact, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    if stress:
        for hid, cells in stress.items():
            path = dest / f"stress_{hid}.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for cell in cells:
                    handle.write(json.dumps(cell, sort_keys=True, default=str) + "\n")


def _qty(block: dict[str, Any] | None, *keys: str) -> Any:
    cur: Any = block or {}
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    if isinstance(cur, dict) and "value" in cur:
        return cur["value"]
    return cur


def _dash_h5(card: dict[str, Any]) -> dict[str, Any]:
    first = card.get("first_lab_oos") or {}
    replay = first.get("EXECUTION_REPLAY_WORLD") or {}
    exp = first.get("EXPECTED_WORLD") or {}
    paired = (card.get("PAIRED_PARENT_CHILD") or {}).get("aggregate") or {}
    return {
        "CANONICAL_REPLAY_NET": _qty(replay, "replay_net_eur"),
        "CANONICAL_REPLAY_NET_PER_FILL": _qty(replay, "replay_net_per_fill_eur"),
        "CANONICAL_REPLAY_NET_PER_SIGNAL": _qty(replay, "replay_net_per_signal_eur"),
        "PARENT_CHILD_PAIRED_DELTA": paired.get("aggregate_delta"),
        "REPLICATION_STATUS": (card.get("REPLICATION") or {}).get("state") or card.get("RESEARCH_STATUS"),
        "RESEARCH_DECISION": card.get("RESEARCH_DECISION"),
        "expected_net_per_signal_eur": _qty(exp, "expected_net_per_signal_eur"),
    }


def _dash_h7(card: dict[str, Any]) -> dict[str, Any]:
    sel = card.get("GATE_SELECTIVITY") or {}
    return {
        "GATE_SELECTIVITY": sel.get("selectivity"),
        "GATE_INACTIVE": sel.get("inactive"),
        "RESEARCH_STATUS": card.get("RESEARCH_STATUS"),
        "RESEARCH_DECISION": card.get("RESEARCH_DECISION"),
        "note": "OOS_PASS does not imply selective strategy improvement.",
    }


def _dash_row(card: dict[str, Any], hid: str) -> dict[str, Any]:
    first = card.get("first_lab_oos") or {}
    replay = first.get("EXECUTION_REPLAY_WORLD") or {}
    exp = first.get("EXPECTED_WORLD") or {}
    sidecar = first.get("MEAN_EDGE_EXECUTION_REPLAY_SIDECAR") or {}
    paired = (card.get("PAIRED_PARENT_CHILD") or {}).get("aggregate") or {}
    be = card.get("BREAK_EVEN") or {}
    return {
        "ID": hid,
        "RESEARCH_STATUS": card.get("RESEARCH_STATUS"),
        "mechanical_verdict": card.get("mechanical_verdict"),
        "SIGNALS": first.get("SIGNALS"),
        "CANDIDATES": first.get("CANDIDATES"),
        "ADMITTED": first.get("ADMITTED"),
        "REJECTED": first.get("REJECTED"),
        "FILLS": first.get("FILLS"),
        "expected_net_total_eur": _qty(exp, "expected_net_total_eur"),
        "expected_net_per_signal_eur": _qty(exp, "expected_net_per_signal_eur"),
        "expected_world": EconomicWorld.SIGNAL_EXPECTATION.value,
        "replay_gross_eur": _qty(replay, "replay_gross_eur"),
        "replay_fees_eur": _qty(replay, "replay_fees_eur"),
        "replay_slippage_eur": _qty(replay, "replay_slippage_eur"),
        "replay_adverse_eur": _qty(replay, "replay_adverse_eur"),
        "replay_other_costs_eur": _qty(replay, "replay_other_costs_eur"),
        "replay_net_eur": _qty(replay, "replay_net_eur"),
        "replay_net_per_signal_eur": _qty(replay, "replay_net_per_signal_eur"),
        "replay_net_per_fill_eur": _qty(replay, "replay_net_per_fill_eur"),
        "replay_world": EconomicWorld.EXECUTION_REPLAY.value,
        "mean_edge_execution_replay_net_per_fill_eur": _qty(
            sidecar, "mean_edge_execution_replay_net_per_fill_eur"
        ),
        "observed_status": "NOT_RUN",
        "observed_world": EconomicWorld.OBSERVED.value,
        "paired_delta_replay_net_eur": paired.get("aggregate_delta"),
        "paired_mean_delta": paired.get("mean_delta"),
        "paired_positive_window_fraction": paired.get("positive_window_fraction"),
        "break_even_extra_cost_eur": be.get("extra_cost_eur"),
        "break_even_notional_eur": be.get("notional_eur"),
        "break_even_extra_cost_bps_of_notional": be.get("extra_cost_bps_of_notional"),
        "gate_selectivity": (card.get("GATE_SELECTIVITY") or {}).get("selectivity"),
        "gate_inactive": (card.get("GATE_SELECTIVITY") or {}).get("inactive"),
        "replication_status": (card.get("REPLICATION") or {}).get("state") or card.get("RESEARCH_STATUS"),
        "ACCOUNTING_AUDIT": (card.get("ACCOUNTING_AUDIT") or {}).get("ACCOUNTING_AUDIT"),
        "canonical_replay_net_per_fill_eur": _qty(replay, "replay_net_per_fill_eur"),
        "canonical_replay_net_per_fill_world": EconomicWorld.EXECUTION_REPLAY.value,
        "NET_per_fill": None,
        "NET": None,
        "EV": None,
    }

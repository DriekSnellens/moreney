"""Final validation runner: frozen 5-scenario overlay on the parent universe.

Streaming: window → scenario → overlay → accumulator.observe → del waterfall
→ atomic artifact → reducer. Does not retain ExecutionWaterfall lists.
Does not call the hypothesis generator. Does not modify PaperExecutor.
"""

from __future__ import annotations

import gc
import hashlib
import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from bot.research.accounting.protocol import FILL_RATE, REPLAY_VERSION, SCHEMA_VERSION
from bot.research.accounting.waterfall import estimated_fill_count
from bot.research.execution_realism.accumulator import ExecutionAccumulator
from bot.research.execution_realism.artifacts import (
    artifact_is_valid,
    atomic_write_json,
    canonical_fingerprint,
    load_json,
    window_artifact_path,
)
from bot.research.execution_realism.memory import MemoryMonitor
from bot.research.final_validation.breakeven import break_even_from_baseline
from bot.research.final_validation.overlay import CanonicalLine, apply_overlay
from bot.research.final_validation.protocol import (
    ARTIFACT_SCHEMA_VERSION,
    NEW_STRATEGIES_CREATED,
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    SCENARIOS,
    STRATEGY_ID,
    UNSUPPORTED_BY_DATA,
    UNIVERSE,
    build_manifest,
    protocol_hash,
    scenario_config_hash,
)
from bot.research.final_validation.verdict import decide
from bot.research.regime_lab.families import FreshnessCVDFamily
from bot.research.regime_lab.metrics import attach_event_economics
from bot.research.robustness.protocol import (
    FIRST_LAB_OOS_START_NS,
    FROZEN_H0005_PARAMS,
    LOOKBACK_BUFFER_NS,
)
from bot.research.robustness.windows import sequential_windows
from bot.research.tournament.tape_index import build_tape_index

_ZERO = Decimal("0")


def _tape_fp(dataset_id: str, content_fp: str, stride: int, min_ts: int) -> str:
    return hashlib.sha256(f"{dataset_id}:{content_fp}:{stride}:{min_ts}".encode()).hexdigest()


def _line_from_event(event: dict[str, Any], *, window_id: str, venue: str, venue_exit: str) -> CanonicalLine:
    ts = int(event.get("ts_ns") or 0)
    sym = str(event.get("symbol") or "")
    return CanonicalLine(
        signal_id=f"{window_id}_{sym}_{ts}",
        window_id=window_id,
        symbol=sym,
        route=f"{venue}|{venue_exit}",
        canonical_net=Decimal(str(event.get("net") or 0)),
        gross=Decimal(str(event.get("gross") or 0)),
        fees=Decimal(str(event.get("fees") or 0)),
        slippage=Decimal(str(event.get("slippage") or 0)),
        adverse=Decimal(str(event.get("adverse") or 0)),
        latency=Decimal(str(event.get("latency") or 0)),
        inventory=Decimal(str(event.get("inventory_effect") or 0)),
        forward=float(event.get("forward") or 0.0),
    )


def collect_window_lines(
    *,
    index: Any,
    window: dict[str, Any],
    fam: FreshnessCVDFamily,
    params: dict[str, Any],
    venue: str,
    venue_exit: str,
    horizon_ms: int,
) -> list[CanonicalLine]:
    part = fam.partition_window(
        index,
        start_ns=int(window["start_ts_ns"]),
        end_ns_exclusive=None,
        end_ns_inclusive=int(window["end_ts_ns_inclusive"]),
        params=params,
        horizons=[horizon_ms],
    )
    parent_events = attach_event_economics(
        list(part["parent_events"]), venue=venue, venue_exit=venue_exit, horizon_ms=horizon_ms
    )
    wid = str(window["WINDOW_ID"])
    return [_line_from_event(e, window_id=wid, venue=venue, venue_exit=venue_exit) for e in parent_events]


def _top_share(window_nets: list[Decimal], total: Decimal) -> float:
    if not window_nets or total == 0:
        return 0.0
    peak = max(window_nets, key=lambda x: abs(x))
    return float(abs(peak) / abs(total)) if total != 0 else 0.0


def _labeled_scenario_dict(
    scen: dict[str, Any],
    acc: ExecutionAccumulator,
    *,
    window_nets: list[Decimal],
    symbol_net: dict[str, Decimal],
    route_net: dict[str, Decimal],
) -> dict[str, Any]:
    n = acc.signal_count
    fills = acc.fill_count
    exec_net = acc.execution_net_sum
    canon = acc.canonical_replay_net_sum
    est_fills = estimated_fill_count(n) if n else 0
    pos_w = sum(1 for x in window_nets if x > 0)
    neg_w = sum(1 for x in window_nets if x < 0)
    med = Decimal(str(median(float(x) for x in window_nets))) if window_nets else None
    worst = min(window_nets) if window_nets else _ZERO
    best = max(window_nets) if window_nets else _ZERO
    top_sym = max(symbol_net.items(), key=lambda kv: abs(kv[1]), default=(None, _ZERO))
    top_rt = max(route_net.items(), key=lambda kv: abs(kv[1]), default=(None, _ZERO))
    return {
        "scenario_id": scen["scenario_id"],
        "classification": scen["classification"],
        "description": scen["description"],
        "scenario_config_hash": scenario_config_hash(scen),
        "candidate_count": n,
        "signal_count": n,
        "fill_count": fills,
        "missed_fill_count": acc.no_fill_count,
        "partial_fill_count": acc.partial_count,
        "gross_sum": str(acc.gross_sum),
        "fees": str(acc.fee_sum),
        "slippage": str(acc.slippage_sum),
        "adverse": str(acc.adverse_sum),
        "inventory": str(acc.inventory_sum),
        "CANONICAL_REPLAY_NET": str(canon),
        "CANONICAL_REPLAY_NET_PER_SIGNAL": str(canon / Decimal(n) if n else _ZERO),
        "CANONICAL_REPLAY_NET_PER_FILL": str(canon / Decimal(est_fills) if est_fills else None),
        "estimated_fill_count": est_fills,
        "EXECUTION_NET": str(exec_net),
        "execution_net_eur": str(exec_net),
        "EXECUTION_NET_PER_SIGNAL": str(exec_net / Decimal(n) if n else _ZERO),
        "EXECUTION_NET_PER_FILL": str(exec_net / Decimal(fills) if fills else None),
        "MEAN_EDGE_EXECUTION_NET_PER_FILL": None,
        "expected_net_sum": str(acc.expected_net_sum),
        "max_drawdown": str(acc.max_drawdown),
        "positive_windows": pos_w,
        "negative_windows": neg_w,
        "total_windows": len(window_nets),
        "median_window_net": None if med is None else str(med),
        "worst_window_net": str(worst),
        "best_window_net": str(best),
        "top_window_share": _top_share(window_nets, exec_net),
        "win_rate": fills / n if n else 0.0,
        "accounting_identity_status": acc.accounting_identity_status,
        "deterministic_fingerprint": acc.sums_fingerprint(),
        "ROUTE_UNIVERSE": sorted(route_net),
        "ROUTE_UNIVERSE_LIMITED": len(route_net) <= 1,
        "top_symbol": top_sym[0],
        "top_symbol_share": (
            float(abs(top_sym[1]) / abs(exec_net)) if exec_net != 0 and top_sym[0] else 0.0
        ),
        "top_route": top_rt[0],
        "top_route_share": (
            float(abs(top_rt[1]) / abs(exec_net)) if exec_net != 0 and top_rt[0] else 0.0
        ),
        "concentration": (
            "CONCENTRATED"
            if exec_net != 0 and top_sym[0] and abs(top_sym[1]) / abs(exec_net) >= 0.70
            else "ROUTE_UNIVERSE_LIMITED"
            if len(route_net) <= 1
            else "DISPERSED"
        ),
        "outcome_counts": dict(acc.outcome_counts),
        "window_nets": [str(x) for x in window_nets],
    }


def replay_lines_streaming(
    windows: list[tuple[str, list[CanonicalLine]]],
    scenarios: list[dict[str, Any]],
    *,
    run_dir: Path,
    dataset_fingerprint: str,
    tape_fingerprint: str,
    resume: bool = False,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    replayed: list[str] = []
    per_scen_acc: dict[str, ExecutionAccumulator] = {s["scenario_id"]: ExecutionAccumulator() for s in scenarios}
    per_scen_windows: dict[str, list[Decimal]] = {s["scenario_id"]: [] for s in scenarios}
    per_scen_symbol: dict[str, dict[str, Decimal]] = {s["scenario_id"]: defaultdict(lambda: _ZERO) for s in scenarios}
    per_scen_route: dict[str, dict[str, Decimal]] = {s["scenario_id"]: defaultdict(lambda: _ZERO) for s in scenarios}

    for wid, lines in windows:
        for scen in scenarios:
            key = f"{wid}:{scen['scenario_id']}"
            dest = window_artifact_path(run_dir, wid, scen["scenario_id"])
            scen_hash = scenario_config_hash(scen)
            if resume and artifact_is_valid(
                dest,
                dataset_fingerprint=dataset_fingerprint,
                scenario_hash=scen_hash,
                schema_version=ARTIFACT_SCHEMA_VERSION,
            ):
                payload = load_json(dest) or {}
                wacc = ExecutionAccumulator.from_dict(payload.get("accumulator") or {})
                per_scen_acc[scen["scenario_id"]].add_sums_from(wacc)
                for net_s in payload.get("execution_nets") or []:
                    per_scen_acc[scen["scenario_id"]].observe_drawdown(Decimal(str(net_s)))
                per_scen_windows[scen["scenario_id"]].append(wacc.execution_net_sum)
                for k, v in (payload.get("symbol_net") or {}).items():
                    per_scen_symbol[scen["scenario_id"]][k] += Decimal(str(v))
                for k, v in (payload.get("route_net") or {}).items():
                    per_scen_route[scen["scenario_id"]][k] += Decimal(str(v))
                skipped.append(key)
                continue

            acc = ExecutionAccumulator()
            nets: list[str] = []
            symbol_net: dict[str, Decimal] = defaultdict(lambda: _ZERO)
            route_net: dict[str, Decimal] = defaultdict(lambda: _ZERO)
            for line in lines:
                wf = apply_overlay(line, scen)
                acc.observe(wf, canonical_net=line.canonical_net)
                symbol_net[line.symbol] += wf.execution_net
                route_net[line.route] += wf.execution_net
                nets.append(str(wf.execution_net))
                del wf
            payload = {
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "dataset_fingerprint": dataset_fingerprint,
                "tape_fingerprint": tape_fingerprint,
                "window_id": wid,
                "scenario_id": scen["scenario_id"],
                "scenario_config_hash": scen_hash,
                "accumulator": acc.to_dict(),
                "execution_nets": nets,
                "symbol_net": {k: str(v) for k, v in symbol_net.items()},
                "route_net": {k: str(v) for k, v in route_net.items()},
            }
            payload["deterministic_fingerprint"] = canonical_fingerprint(payload)
            atomic_write_json(dest, payload)
            per_scen_acc[scen["scenario_id"]].add_sums_from(acc)
            for net_s in nets:
                per_scen_acc[scen["scenario_id"]].observe_drawdown(Decimal(net_s))
            per_scen_windows[scen["scenario_id"]].append(acc.execution_net_sum)
            for k, v in symbol_net.items():
                per_scen_symbol[scen["scenario_id"]][k] += v
            for k, v in route_net.items():
                per_scen_route[scen["scenario_id"]][k] += v
            replayed.append(key)
            del acc
            del nets
        gc.collect()

    results = [
        _labeled_scenario_dict(
            scen,
            per_scen_acc[scen["scenario_id"]],
            window_nets=per_scen_windows[scen["scenario_id"]],
            symbol_net=dict(per_scen_symbol[scen["scenario_id"]]),
            route_net=dict(per_scen_route[scen["scenario_id"]]),
        )
        for scen in scenarios
    ]
    return {
        "scenario_results": results,
        "windows_replayed": replayed,
        "windows_skipped": skipped,
    }


def run_final_validation(
    *,
    research_path: str = "data/research_marketdata",
    max_events: int | None = None,
    stride: int | None = None,
    out_dir: str = "data/research/final_validation",
    run_id: str | None = None,
    resume: bool = False,
    write_markdown_report: bool = True,
) -> dict[str, Any]:
    from bot.research.execution_realism.config import STRIDE_DEFAULT
    from bot.research.execution_realism.config import EXECUTION_REALISM_PRODUCTION_ENABLED

    t0 = time.perf_counter()
    if not Path(research_path).exists():
        return {
            "STATUS": "DATA_NOT_READY",
            "FINAL_VALIDATION_VERDICT": "PROMISING_BUT_INSUFFICIENT",
            "WHY": ["Research tape path is missing.", "Cannot score execution robustness without the parent universe."],
            "NEXT_ACTION": "Collect the research tape and re-run python -m bot.research.final_validation.runner",
            "PRODUCTION_EXECUTION": "DISABLED",
        }

    stride = int(stride if stride is not None else STRIDE_DEFAULT)
    min_ts = int(FIRST_LAB_OOS_START_NS) - int(LOOKBACK_BUFFER_NS)
    index = build_tape_index(
        Path(research_path),
        max_events=max_events,
        stride=stride,
        min_ts_ns=min_ts,
        parse_inventory_events=False,
    )
    plan = sequential_windows(index)
    complete_windows = [w for w in (plan.get("windows") or []) if w.get("complete")]
    if len(complete_windows) < 1:
        return {
            "STATUS": "DATA_NOT_READY",
            "FINAL_VALIDATION_VERDICT": "PROMISING_BUT_INSUFFICIENT",
            "WHY": ["No complete independent windows on the tape."],
            "NEXT_ACTION": "Collect additional unseen tape until complete windows exist.",
            "PRODUCTION_EXECUTION": "DISABLED",
        }

    params = dict(FROZEN_H0005_PARAMS)
    venue = str(params["venue_a"])
    venue_exit = str(params["venue_b"])
    h = int(params.get("horizon_ms") or 5000)
    fam = FreshnessCVDFamily()
    dataset_fp = str(index.content_fingerprint or "")
    tape_fp = _tape_fp(index.dataset_id, dataset_fp, stride, min_ts)
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(out_dir) / "runs" / run_id

    monitor = MemoryMonitor(windows_total=len(complete_windows), scenarios_total=len(SCENARIOS) * len(complete_windows))
    packed: list[tuple[str, list[CanonicalLine]]] = []
    n_signals = 0
    parent_canonical = _ZERO
    for w in complete_windows:
        lines = collect_window_lines(
            index=index, window=w, fam=fam, params=params, venue=venue, venue_exit=venue_exit, horizon_ms=h
        )
        packed.append((str(w["WINDOW_ID"]), lines))
        n_signals += len(lines)
        parent_canonical += sum((ln.canonical_net for ln in lines), _ZERO)
        monitor.windows_completed += 1

    streamed = replay_lines_streaming(
        packed,
        list(SCENARIOS),
        run_dir=run_dir,
        dataset_fingerprint=dataset_fp,
        tape_fingerprint=tape_fp,
        resume=resume,
    )
    del packed
    gc.collect()

    scenario_results = streamed["scenario_results"]
    decision = decide(scenario_results, n_windows=len(complete_windows))
    baseline = next(r for r in scenario_results if r["scenario_id"] == "BASELINE")
    be = break_even_from_baseline(
        execution_net=Decimal(str(baseline["EXECUTION_NET"])),
        n_signals=int(baseline["signal_count"]),
        fee_sum=Decimal(str(baseline["fees"])),
    )
    elapsed = time.perf_counter() - t0
    mem = monitor.snapshot(force_log=True)

    result: dict[str, Any] = {
        "STATUS": "COMPLETE",
        "PACKAGE": PACKAGE_LABEL,
        "STRATEGY": STRATEGY_ID,
        "EXECUTION": "RESEARCH_ONLY",
        "PRODUCTION_EXECUTION": "DISABLED",
        "execution_enabled": EXECUTION_REALISM_PRODUCTION_ENABLED,
        "NEW_STRATEGIES_CREATED": list(NEW_STRATEGIES_CREATED),
        "HYPOTHESIS_GENERATOR_ENABLED": False,
        "NO_NEW_ALPHA_CLAIMED": True,
        "protocol_version": PROTOCOL_VERSION,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": build_manifest(
            extra={
                "dataset_id": index.dataset_id,
                "dataset_fingerprint": dataset_fp,
                "tape_fingerprint": tape_fp,
                "n_signals": n_signals,
                "n_windows": len(complete_windows),
            }
        ),
        "UNIVERSE": UNIVERSE,
        "UNSUPPORTED_BY_DATA": [dict(u) for u in UNSUPPORTED_BY_DATA],
        "DATASET": index.dataset_id,
        "DATASET_FINGERPRINT": dataset_fp,
        "n_signals": n_signals,
        "n_windows": len(complete_windows),
        "window_ids": [w["WINDOW_ID"] for w in complete_windows],
        "CANONICAL_REPLAY_NET": str(parent_canonical),
        "scenario_results": scenario_results,
        "BASELINE_RESULT": baseline,
        "break_even": be,
        "FINAL_VALIDATION_VERDICT": decision["FINAL_VALIDATION_VERDICT"],
        "WHY": decision["WHY"],
        "NEXT_ACTION": decision["NEXT_ACTION"],
        "FILL_RATE_ESTIMATED_COUNT_ONLY": FILL_RATE,
        "STREAMING": {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "windows_replayed": streamed["windows_replayed"],
            "windows_skipped": streamed["windows_skipped"],
            "peak_rss_mb": mem.get("peak_rss_mb"),
            "rss_mb": mem.get("rss_mb"),
        },
        "PERFORMANCE": {
            "seconds": elapsed,
            "n_signals": n_signals,
            "n_scenarios": len(SCENARIOS),
            "replays": n_signals * len(SCENARIOS),
            "replays_per_sec": (n_signals * len(SCENARIOS) / elapsed) if elapsed else 0,
            "peak_rss_mb": mem.get("peak_rss_mb"),
        },
    }

    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    (dest / "results.json").write_text(payload, encoding="utf-8")
    if write_markdown_report:
        from bot.research.final_validation.report import write_report

        write_report(result, "docs/CROSS_VENUE_DISLOCATION_FINAL_VALIDATION.md")
    return result

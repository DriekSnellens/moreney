"""Main execution realism engine — streaming window/scenario pipeline.

Default path never retains ExecutionWaterfall collections. A legacy in-memory
path exists only for fixture equivalence tests and the small-fixture benchmark.
"""

from __future__ import annotations

import gc
import hashlib
import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.research.accounting.protocol import REPLAY_VERSION, SCHEMA_VERSION
from bot.research.execution_realism.artifacts import (
    artifact_is_valid,
    scenario_config_hash,
    window_artifact_path,
    write_run_meta,
    write_window_artifact,
)
from bot.research.execution_realism.breakeven import compute_breakeven_surface
from bot.research.execution_realism.config import (
    ARTIFACT_SCHEMA_VERSION,
    EXECUTION_REALISM_PRODUCTION_ENABLED,
    FILL_RATE,
    MIN_INDEPENDENT_WINDOWS,
    MIN_POSITIVE_SCENARIO_FRACTION,
    NOTIONAL_EUR,
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    STRIDE_DEFAULT,
    build_manifest,
)
from bot.research.execution_realism.memory import MemoryMonitor
from bot.research.execution_realism.models import Verdict
from bot.research.execution_realism.reducer import reduce_run
from bot.research.execution_realism.replay import (
    WindowSignal,
    accumulator_from_waterfalls,
    legacy_replay_window,
    scenario_result_dict,
    streaming_replay_window,
)
from bot.research.execution_realism.scenario import reference_scenarios, stage1_screen
from bot.research.regime_lab.families import FreshnessCVDFamily
from bot.research.regime_lab.features import views_for
from bot.research.regime_lab.metrics import attach_event_economics
from bot.research.robustness.protocol import (
    FIRST_LAB_OOS_START_NS,
    FROZEN_H0005_PARAMS,
    LOOKBACK_BUFFER_NS,
)
from bot.research.robustness.windows import sequential_windows
from bot.research.tournament.tape_index import build_tape_index

_ZERO = Decimal("0")


def _signal_side(event: dict[str, Any]) -> str:
    dis = event.get("dislocation")
    if dis is None:
        return "BUY"
    return "BUY" if float(dis) > 0 else "SELL"


def _entry_price(event: dict[str, Any]) -> Decimal:
    mid = event.get("mid") or event.get("entry_mid")
    if mid is not None:
        return Decimal(str(mid))
    bid = event.get("bid")
    ask = event.get("ask")
    if bid is not None and ask is not None:
        return Decimal(str((float(bid) + float(ask)) / 2.0))
    return Decimal("1")


def _tape_fingerprint(dataset_id: str, content_fingerprint: str, stride: int, min_ts_ns: int) -> str:
    payload = f"{dataset_id}:{content_fingerprint}:{stride}:{min_ts_ns}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _window_points(index: Any, venue: str, start_ns: int, end_ns: int) -> list[Any]:
    window_points = []
    for key, pts in index.series.items():
        if key[0] == venue:
            for p in pts:
                if start_ns <= p.ts_ns <= end_ns:
                    window_points.append(p)
    window_points.sort(key=lambda p: p.ts_ns)
    return window_points


def collect_window_signals(
    *,
    index: Any,
    window: dict[str, Any],
    fam: FreshnessCVDFamily,
    params: dict[str, Any],
    venue: str,
    venue_exit: str,
    horizon_ms: int,
) -> tuple[list[WindowSignal], Decimal]:
    """Load only the signals required for this window. Caller must release the list."""
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
    window_points = _window_points(
        index, venue, int(window["start_ts_ns"]), int(window["end_ts_ns_inclusive"])
    )
    signals: list[WindowSignal] = []
    parent_canonical = _ZERO
    wid = str(window["WINDOW_ID"])
    h_ns = horizon_ms * 1_000_000
    route = f"{venue}|{venue_exit}"
    for event in parent_events:
        canonical = Decimal(str(event.get("net") or 0))
        parent_canonical += canonical
        ts = int(event.get("ts_ns") or 0)
        sym = str(event.get("symbol") or "")
        sig_points = [p for p in window_points if p.ts_ns >= ts and p.ts_ns <= ts + h_ns]
        signals.append(
            WindowSignal(
                signal_id=f"{wid}_{sym}_{ts}",
                strategy_id="H-0005",
                symbol=sym,
                route=route,
                venue=venue,
                venue_exit=venue_exit,
                side=_signal_side(event),
                forward=float(event.get("forward") or 0.0),
                observed_at_ns=ts,
                entry_price=_entry_price(event),
                exchange_ts_available=event.get("exchange_ts_ns") is not None,
                canonical_net=canonical,
                points=sig_points,
            )
        )
    del window_points
    del parent_events
    return signals, parent_canonical


def _verdict(
    scenario_results: list[dict[str, Any]],
    n_windows: int,
) -> tuple[Verdict, dict[str, Any] | None, float]:
    normal_scen = next(
        (
            r
            for r in scenario_results
            if r["latency_scenario"] == "NORMAL"
            and r["fill_model"] == "POST_ONLY_SURVIVAL"
            and r["hedge_scenario"] == "NORMAL"
        ),
        None,
    )
    positive_scenarios = sum(1 for r in scenario_results if Decimal(str(r["execution_net_eur"])) > 0)
    total_scenarios = len(scenario_results) or 1
    pos_fraction = positive_scenarios / total_scenarios
    if n_windows <= 0:
        verdict = Verdict.DATA_NOT_READY
    elif normal_scen is None:
        verdict = Verdict.EXECUTION_FRAGILE
    elif Decimal(str(normal_scen["execution_net_eur"])) <= 0:
        verdict = Verdict.EXECUTION_FAILED
    elif pos_fraction < MIN_POSITIVE_SCENARIO_FRACTION:
        verdict = Verdict.EXECUTION_FRAGILE
    elif n_windows < MIN_INDEPENDENT_WINDOWS:
        verdict = Verdict.PROMISING_EXECUTION
    else:
        verdict = Verdict.ROBUST_EXECUTION_CANDIDATE
    return verdict, normal_scen, pos_fraction


def _finalize_result(
    *,
    scenario_results: list[dict[str, Any]],
    canonical_net: Decimal,
    n_signals: int,
    n_windows: int,
    scenarios: list[dict[str, str]],
    strategies: list[str],
    mode: str,
    elapsed: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verdict, normal_scen, pos_fraction = _verdict(scenario_results, n_windows)
    canonical_per_signal = canonical_net / Decimal(n_signals) if n_signals else _ZERO
    fee_per_signal = NOTIONAL_EUR * Decimal("0.002")
    adverse_per_signal = NOTIONAL_EUR * Decimal("0.0008")
    slip_per_signal = NOTIONAL_EUR * Decimal("0.0002")
    breakeven = compute_breakeven_surface(
        canonical_net_per_signal=canonical_per_signal,
        n_signals=n_signals,
        fill_rate_baseline=FILL_RATE,
        fee_baseline_per_signal=fee_per_signal,
        adverse_baseline_per_signal=adverse_per_signal,
        slippage_baseline_per_signal=slip_per_signal,
    )
    ref_normal = normal_scen or (scenario_results[0] if scenario_results else {})
    oc = ref_normal.get("outcome_counts") or {}
    total_oc = sum(oc.values()) or 1
    result: dict[str, Any] = {
        "STATUS": "COMPLETE",
        "PACKAGE": PACKAGE_LABEL,
        "VERDICT": verdict.value,
        "protocol_version": PROTOCOL_VERSION,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": build_manifest(
            extra={
                "n_signals": n_signals,
                "n_windows": n_windows,
                "n_scenarios": len(scenarios),
                "mode": mode,
                "strategies": strategies,
            }
        ),
        "CANONICAL_REPLAY_NET": str(canonical_net),
        "REALISTIC_EXECUTION_NET": str(ref_normal.get("execution_net_eur") or _ZERO),
        "DELTA": str(Decimal(str(ref_normal.get("execution_net_eur") or 0)) - canonical_net),
        "FILL_SURVIVAL_PCT": round(float(ref_normal.get("fill_rate") or 0) * 100, 1),
        "PARTIAL_FILL_PCT": round(float(ref_normal.get("partial_fill_rate") or 0) * 100, 1),
        "NO_FILL_PCT": round(
            float(ref_normal.get("n_no_fill") or 0) / max(1, int(ref_normal.get("n_signals") or 1)) * 100,
            1,
        ),
        "alpha_loss_attribution": {k: round(v / total_oc * 100, 1) for k, v in oc.items()},
        "n_signals": n_signals,
        "n_windows": n_windows,
        "positive_scenario_fraction": round(pos_fraction, 3),
        "scenario_results": scenario_results,
        "breakeven_surface": breakeven,
        "PRODUCTION_EXECUTION": "DISABLED",
        "execution_enabled": EXECUTION_REALISM_PRODUCTION_ENABLED,
        "NEW_STRATEGIES_CREATED": [],
        "NO_NEW_ALPHA_CLAIMED": True,
        "PERFORMANCE": {
            "execution_realism_seconds": elapsed,
            "n_signals": n_signals,
            "n_scenarios": len(scenarios),
            "signals_per_second": n_signals / elapsed if elapsed > 0 else 0,
        },
    }
    if extra:
        result.update(extra)
    return result


def run_execution_realism(
    *,
    mode: str = "screen",
    strategies: list[str] | None = None,
    research_path: str = "data/research_marketdata",
    max_events: int | None = None,
    stride: int = STRIDE_DEFAULT,
    out_dir: str = "data/research",
    run_id: str | None = None,
    resume: bool = False,
    streaming: bool = True,
    legacy_in_memory: bool = False,
    write_markdown_report: bool = True,
    workers: int = 1,
    artifact_root: str | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    strategies = strategies or ["H-0005"]
    if workers != 1:
        print(
            "WARNING: bounded parallelism is not enabled; using sequential streaming (workers=1)",
            flush=True,
        )

    if not Path(research_path).exists():
        return {"STATUS": "DATA_NOT_READY", "VERDICT": Verdict.DATA_NOT_READY.value}

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

    if len(complete_windows) < 3:
        return {"STATUS": "DATA_NOT_READY", "VERDICT": Verdict.DATA_NOT_READY.value}

    scenarios = stage1_screen() if mode == "screen" else reference_scenarios()
    views_for(index)
    fam = FreshnessCVDFamily()
    params = dict(FROZEN_H0005_PARAMS)
    venue = str(params["venue_a"])
    venue_exit = str(params["venue_b"])
    h = int(params.get("horizon_ms") or 5000)

    dataset_fp = str(index.content_fingerprint or "")
    tape_fp = _tape_fingerprint(index.dataset_id, dataset_fp, stride, min_ts)
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    art_root = Path(artifact_root or "data/research/execution_realism/runs")
    run_dir = art_root / run_id
    window_ids = [str(w["WINDOW_ID"]) for w in complete_windows]
    write_run_meta(
        run_dir,
        run_id=run_id,
        dataset_fingerprint=dataset_fp,
        tape_fingerprint=tape_fp,
        dataset_id=str(index.dataset_id),
        window_ids=window_ids,
        scenarios=scenarios,
        extra={"mode": mode, "resume": resume, "streaming": streaming and not legacy_in_memory},
    )

    monitor = MemoryMonitor(
        windows_total=len(complete_windows),
        scenarios_total=len(complete_windows) * len(scenarios),
    )
    skipped: list[str] = []
    replayed: list[str] = []
    n_signals = 0
    canonical_net = _ZERO

    use_legacy = legacy_in_memory or not streaming

    for w in complete_windows:
        signals, window_canonical = collect_window_signals(
            index=index,
            window=w,
            fam=fam,
            params=params,
            venue=venue,
            venue_exit=venue_exit,
            horizon_ms=h,
        )
        n_signals += len(signals)
        canonical_net += window_canonical
        wid = str(w["WINDOW_ID"])

        for scen in scenarios:
            key = f"{wid}:{scen['scenario_id']}"
            dest = window_artifact_path(run_dir, wid, scen["scenario_id"])
            scen_hash = scenario_config_hash(scen)
            if resume and artifact_is_valid(
                dest,
                dataset_fingerprint=dataset_fp,
                scenario_hash=scen_hash,
            ):
                skipped.append(key)
                monitor.artifacts_skipped += 1
                monitor.scenarios_processed += 1
                continue

            if use_legacy:
                waterfalls = legacy_replay_window(signals, scen)
                acc = accumulator_from_waterfalls(waterfalls, signals)
                nets = [str(wf.execution_net) for wf in waterfalls]
                del waterfalls
            else:
                acc, nets = streaming_replay_window(signals, scen)

            write_window_artifact(
                run_dir,
                window_id=wid,
                scenario=scen,
                accumulator=acc.to_dict(),
                execution_nets=nets,
                dataset_fingerprint=dataset_fp,
                tape_fingerprint=tape_fp,
                parent_canonical_net_sum=str(window_canonical),
            )
            replayed.append(key)
            monitor.artifacts_written += 1
            monitor.signals_processed += acc.signal_count
            monitor.scenarios_processed += 1
            del acc
            del nets
            monitor.snapshot()

        monitor.windows_completed += 1
        del signals
        gc.collect()
        monitor.snapshot(force_log=True)

    reduced = reduce_run(
        run_dir,
        scenarios=scenarios,
        window_ids=window_ids,
        dataset_fingerprint=dataset_fp,
        parent_canonical_net=canonical_net,
    )
    elapsed = time.perf_counter() - t0
    mem_final = monitor.snapshot(force_log=True)
    result = _finalize_result(
        scenario_results=reduced["scenario_results"],
        canonical_net=canonical_net,
        n_signals=n_signals,
        n_windows=len(complete_windows),
        scenarios=scenarios,
        strategies=strategies,
        mode=mode,
        elapsed=elapsed,
        extra={
            "STREAMING": {
                "enabled": not use_legacy,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "windows_replayed": replayed,
                "windows_skipped": skipped,
                "artifacts_written": monitor.artifacts_written,
                "artifacts_skipped": monitor.artifacts_skipped,
                "reducer_status": reduced.get("REDUCER_STATUS"),
                "reducer_issues": reduced.get("issues") or [],
                "peak_rss_mb": mem_final.get("peak_rss_mb"),
                "rss_mb": mem_final.get("rss_mb"),
            },
            "PERFORMANCE": {
                "execution_realism_seconds": elapsed,
                "n_signals": n_signals,
                "n_scenarios": len(scenarios),
                "signals_per_second": n_signals / elapsed if elapsed > 0 else 0,
                "artifacts_per_second": monitor.artifacts_written / elapsed if elapsed > 0 else 0,
                "peak_rss_mb": mem_final.get("peak_rss_mb"),
                "rss_mb": mem_final.get("rss_mb"),
            },
        },
    )

    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    (dest / "execution_realism_results.json").write_text(payload, encoding="utf-8")

    if write_markdown_report:
        from bot.research.execution_realism.report import write_report

        write_report(result, "docs/EXECUTION_REALISM_REPORT.md")

    return result


def replay_fixture_streaming(
    windows: Sequence[tuple[str, list[WindowSignal]]],
    scenarios: Sequence[dict[str, str]],
    *,
    run_dir: Path,
    dataset_fingerprint: str = "fixture",
    tape_fingerprint: str = "fixture",
    resume: bool = False,
) -> dict[str, Any]:
    """Tape-free streaming runner used by tests and the memory benchmark."""
    window_ids = [wid for wid, _ in windows]
    write_run_meta(
        run_dir,
        run_id=run_dir.name,
        dataset_fingerprint=dataset_fingerprint,
        tape_fingerprint=tape_fingerprint,
        dataset_id="fixture",
        window_ids=window_ids,
        scenarios=list(scenarios),
    )
    skipped: list[str] = []
    replayed: list[str] = []
    parent_canonical = _ZERO
    n_signals = 0
    for wid, signals in windows:
        window_canonical = sum((s.canonical_net for s in signals), _ZERO)
        parent_canonical += window_canonical
        n_signals += len(signals)
        for scen in scenarios:
            key = f"{wid}:{scen['scenario_id']}"
            dest = window_artifact_path(run_dir, wid, scen["scenario_id"])
            if resume and artifact_is_valid(
                dest,
                dataset_fingerprint=dataset_fingerprint,
                scenario_hash=scenario_config_hash(scen),
            ):
                skipped.append(key)
                continue
            acc, nets = streaming_replay_window(signals, scen)
            write_window_artifact(
                run_dir,
                window_id=wid,
                scenario=scen,
                accumulator=acc.to_dict(),
                execution_nets=nets,
                dataset_fingerprint=dataset_fingerprint,
                tape_fingerprint=tape_fingerprint,
                parent_canonical_net_sum=str(window_canonical),
            )
            replayed.append(key)
            del acc
            del nets
        gc.collect()
    reduced = reduce_run(
        run_dir,
        scenarios=scenarios,
        window_ids=window_ids,
        dataset_fingerprint=dataset_fingerprint,
        parent_canonical_net=parent_canonical,
    )
    return {
        "n_signals": n_signals,
        "CANONICAL_REPLAY_NET": str(parent_canonical),
        "scenario_results": reduced["scenario_results"],
        "windows_replayed": replayed,
        "windows_skipped": skipped,
        "REDUCER_STATUS": reduced["REDUCER_STATUS"],
        "issues": reduced["issues"],
    }


def replay_fixture_legacy(
    windows: Sequence[tuple[str, list[WindowSignal]]],
    scenarios: Sequence[dict[str, str]],
    *,
    retain_waterfalls: bool = False,
) -> dict[str, Any]:
    """In-memory equivalent of replay_fixture_streaming (retains waterfalls)."""
    parent_canonical = _ZERO
    n_signals = 0
    scenario_results: list[dict[str, Any]] = []
    per_scen_wfs: dict[str, list] = {s["scenario_id"]: [] for s in scenarios}
    per_scen_window_nets: dict[str, list[Decimal]] = {s["scenario_id"]: [] for s in scenarios}
    flat_signals: list[WindowSignal] = []
    for _wid, signals in windows:
        parent_canonical += sum((s.canonical_net for s in signals), _ZERO)
        n_signals += len(signals)
        flat_signals.extend(signals)
        for scen in scenarios:
            wfs = legacy_replay_window(signals, scen)
            per_scen_wfs[scen["scenario_id"]].extend(wfs)
            per_scen_window_nets[scen["scenario_id"]].append(
                sum((wf.execution_net for wf in wfs), _ZERO)
            )
    for scen in scenarios:
        sid = scen["scenario_id"]
        acc = accumulator_from_waterfalls(per_scen_wfs[sid], flat_signals)
        scenario_results.append(
            scenario_result_dict(
                scen,
                acc,
                window_execution_nets=per_scen_window_nets[sid],
                parent_canonical_net=parent_canonical,
            )
        )
    out: dict[str, Any] = {
        "n_signals": n_signals,
        "CANONICAL_REPLAY_NET": str(parent_canonical),
        "scenario_results": scenario_results,
        "waterfall_count": sum(len(v) for v in per_scen_wfs.values()),
    }
    if retain_waterfalls:
        out["_waterfalls"] = per_scen_wfs
    return out

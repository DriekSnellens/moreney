"""End-to-end research tape finalization: scan → validate → manifest → accept."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.market_data.research import SCHEMA_VERSION
from bot.market_data.research.acceptance import (
    PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA,
    HORIZONS_MS,
    build_cross_venue_matrix,
    evaluate_acceptance,
)
from bot.market_data.research.chrono_split import chronological_split
from bot.market_data.research.integrity import validate_tape
from bot.market_data.research.manifest import git_commit, write_manifest
from bot.market_data.research.operational_state import resolve_operational_state
from bot.market_data.research.sync import sync_coverage_report
from bot.market_data.research.tape_scan import dataset_id_from_fingerprint, scan_tape
from bot.market_data.research.study import _from_dict


def _sample_events_for_sync(root: Path, *, max_per_venue: int = 5_000) -> list[Any]:
    """Load a bounded sample for sync diagnostics (full tape may be huge)."""
    from collections import defaultdict

    counts: dict[str, int] = defaultdict(int)
    events = []
    for path in sorted(root.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                venue = str(raw.get("venue") or "")
                if venue not in {"binance", "bitvavo", "okx"}:
                    continue
                if counts[venue] >= max_per_venue:
                    continue
                events.append(_from_dict(raw))
                counts[venue] += 1
        if all(counts[v] >= max_per_venue for v in ("binance", "bitvavo", "okx")):
            break
    return events


def _pair_sync_reports(events: list[Any]) -> dict[str, dict[str, Any]]:
    from bot.market_data.research.acceptance import DIRECTED_ROUTES

    out: dict[str, dict[str, Any]] = {}
    for src, dst in DIRECTED_ROUTES:
        subset = [e for e in events if e.venue in {src, dst}]
        out[f"{src}->{dst}"] = sync_coverage_report(
            subset,
            venues=(src, dst),
            tolerances_ms=HORIZONS_MS,
            sample_step=20,
        )
    return out


def run_finalization(
    *,
    research_path: Path | str = "data/research_marketdata",
    recorder_snapshot: dict[str, Any] | None = None,
    max_integrity_events: int | None = None,
) -> dict[str, Any]:
    root = Path(research_path)
    rec = recorder_snapshot or {}
    inventory = scan_tape(root)
    integrity = validate_tape(root, max_events=max_integrity_events)

    sample = _sample_events_for_sync(root) if inventory.total_events else []
    sync = (
        sync_coverage_report(sample, venues=("binance", "bitvavo", "okx"), tolerances_ms=HORIZONS_MS)
        if sample
        else {"targets_sampled": 0, "by_tolerance_ms": {}}
    )
    pair_sync = _pair_sync_reports(sample) if sample else {}

    acceptance = evaluate_acceptance(
        inventory=inventory.as_dict(),
        integrity=integrity.as_dict(),
        sync_by_tolerance=sync.get("by_tolerance_ms") or {},
        recorder_drops=int(rec.get("dropped") or rec.get("EVENTS_DROPPED") or 0),
        write_errors=int(rec.get("write_errors") or rec.get("WRITE_ERRORS") or 0),
        events_written_runtime=int(rec.get("written") or rec.get("EVENTS_WRITTEN") or 0),
        criteria=PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA,
    )

    ds_id = (
        dataset_id_from_fingerprint(inventory.content_fingerprint, schema_version=SCHEMA_VERSION)
        if inventory.total_events
        else None
    )
    split = chronological_split(
        start_ts_ns=inventory.first_received_ts_ns,
        end_ts_ns=inventory.last_received_ts_ns,
        content_fingerprint=inventory.content_fingerprint,
        dataset_id=ds_id or "NONE",
    )
    matrix = build_cross_venue_matrix(
        sync_pair_reports=pair_sync,
        symbols=sorted(inventory.events_by_symbol),
        coverage_by_venue=inventory.coverage_by_venue,
    )

    manifest = {
        "dataset_id": ds_id,
        "content_fingerprint": inventory.content_fingerprint,
        "schema_version": SCHEMA_VERSION,
        "git_commit": git_commit(),
        "recording_start_ts_ns": inventory.first_received_ts_ns,
        "recording_end_ts_ns": inventory.last_received_ts_ns,
        "duration_seconds": inventory.duration_seconds,
        "total_events": inventory.total_events,
        "events_by_venue": inventory.events_by_venue,
        "events_by_symbol": inventory.events_by_symbol,
        "recorder_drops": int(rec.get("dropped") or 0),
        "write_errors": int(rec.get("write_errors") or 0),
        "reconnects": integrity.as_dict().get("reasons", {}).get("reconnect_gap", 0),
        "timestamp_coverage": inventory.coverage_by_venue,
        "ordering_diagnostics": {
            "out_of_order": integrity.out_of_order,
            "timestamp_regressions": integrity.timestamp_regressions,
            "duplicates": integrity.duplicates,
        },
        "sequence_diagnostics": {"sequence_gaps": integrity.sequence_gaps},
        "depth_coverage": {
            "with_depth": integrity.with_depth,
            "missing_l1": integrity.missing_l1,
            "observed": integrity.observed,
        },
        "file_checksums": inventory.file_checksums,
        "layout": inventory.layout,
        "criteria_version": PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA["version"],
        "final_verdict": acceptance["final_verdict"],
        "label": "RESEARCH_TAPE_ACCEPTANCE",
    }

    op_state = resolve_operational_state(
        recorder_present=True,
        recorder_enabled=bool(rec.get("enabled", rec.get("RECORDER_ENABLED", True))),
        recorder_running=bool(rec.get("RECORDER_RUNNING", rec.get("enabled", False))),
        events_written=int(rec.get("written") or 0),
        events_dropped=int(rec.get("dropped") or 0),
        write_errors=int(rec.get("write_errors") or 0),
        tape_events=inventory.total_events,
        acceptance_verdict=acceptance["final_verdict"],
    )

    # Prefer acceptance final for dataset readiness when tape exists
    final_verdict = acceptance["final_verdict"]
    if inventory.total_events <= 0:
        if not bool(rec.get("enabled", True)):
            final_verdict = "RECORDER_DISABLED"
        else:
            final_verdict = "NO_REAL_TAPE"

    return {
        "RECORDER_STATUS": op_state,
        "operational_state": op_state,
        "DATASET_ID": ds_id,
        "EVENT_COUNT": inventory.total_events,
        "DURATION": inventory.duration_seconds,
        "VENUES": sorted(inventory.events_by_venue),
        "SYMBOLS": sorted(inventory.events_by_symbol),
        "RECORDER_DROPS": int(rec.get("dropped") or 0),
        "WRITE_ERRORS": int(rec.get("write_errors") or 0),
        "TIMESTAMP_COVERAGE": inventory.coverage_by_venue,
        "SYNC_COVERAGE": sync,
        "SUPPORTED_HORIZONS": acceptance["supported_horizons"],
        "FINAL_VERDICT": final_verdict,
        "NEXT_ACTION": acceptance["next_action"],
        "inventory": inventory.as_dict(),
        "integrity": integrity.as_dict(),
        "acceptance": acceptance,
        "manifest": manifest,
        "chrono_split": split,
        "cross_venue_matrix": matrix,
        "recorder": rec,
        "criteria": {
            k: v
            for k, v in PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA.items()
            if k != "notes"
        },
        "effective_config": {
            "RESEARCH_MARKETDATA_RECORDING_ENABLED": rec.get(
                "enabled", rec.get("RECORDER_ENABLED")
            ),
            "RESEARCH_MARKETDATA_OUTPUT_DIR": rec.get(
                "path", rec.get("RECORDER_OUTPUT_DIR", str(root))
            ),
            "RESEARCH_MARKETDATA_QUEUE_SIZE": rec.get("QUEUE_SIZE"),
            "RESEARCH_MARKETDATA_FLUSH_INTERVAL_MS": rec.get("flush_interval_ms"),
        },
    }


def write_finalization_artifacts(
    report: dict[str, Any],
    *,
    report_path: Path | str = "data/market_data_research_report.json",
    manifest_path: Path | str = "data/research_marketdata_manifest.json",
) -> None:
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Compact operational report for dashboard
    compact = {
        "final_verdict": report["FINAL_VERDICT"],
        "event_count": report["EVENT_COUNT"],
        "supported_horizons": report["SUPPORTED_HORIZONS"],
        "unsupported_horizons": [
            f"{h}ms"
            for h in HORIZONS_MS
            if f"{h}ms" not in (report["SUPPORTED_HORIZONS"] or [])
        ],
        "DATASET_ID": report["DATASET_ID"],
        "DURATION": report["DURATION"],
        "VENUES": report["VENUES"],
        "SYMBOLS": report["SYMBOLS"],
        "RECORDER_DROPS": report["RECORDER_DROPS"],
        "WRITE_ERRORS": report["WRITE_ERRORS"],
        "TIMESTAMP_COVERAGE": report["TIMESTAMP_COVERAGE"],
        "SYNC_COVERAGE": report.get("SYNC_COVERAGE"),
        "NEXT_ACTION": report["NEXT_ACTION"],
        "RECORDER_STATUS": report["RECORDER_STATUS"],
        "operational_state": report["operational_state"],
        "J_horizon_readiness": {
            "horizon_scores": {
                f"LEAD_LAG_{h}MS": (
                    report["acceptance"]["horizon_detail"].get(f"{h}ms", {}).get("status")
                    or "NOT_READY"
                )
                for h in HORIZONS_MS
            },
            "verdict": report["FINAL_VERDICT"],
        },
        "H_synchronization": report.get("SYNC_COVERAGE"),
        "market_data_lab_panel": [
            {
                "venue": v,
                "events": int((report["TIMESTAMP_COVERAGE"].get(v) or {}).get("n") or 0),
                "exchange_ts_coverage": (report["TIMESTAMP_COVERAGE"].get(v) or {}).get(
                    "exchange_ts_pct"
                ),
                "receive_ts_coverage": (report["TIMESTAMP_COVERAGE"].get(v) or {}).get(
                    "received_ts_pct"
                ),
                "sequence_coverage": (report["TIMESTAMP_COVERAGE"].get(v) or {}).get(
                    "sequence_pct"
                ),
                "quality_grade": (
                    "UNSUPPORTED"
                    if v == "bitvavo"
                    else ("MEDIUM" if v == "binance" else "HIGH")
                ),
            }
            for v in ("binance", "bitvavo", "okx")
        ],
        "O_next_step_for_lead_lag": report["NEXT_ACTION"],
        "chrono_split": report.get("chrono_split"),
        "cross_venue_matrix": report.get("cross_venue_matrix"),
        "A_problem": "Finalize research tape path for causal readiness (not alpha).",
        "C_venue_capabilities": {"critical_finding": "Bitvavo exchange_ts unsupported"},
        "manifest": report.get("manifest"),
        "label": "RESEARCH_TAPE_ACCEPTANCE",
    }
    report_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_manifest(report["manifest"], manifest_path)

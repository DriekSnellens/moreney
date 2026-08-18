"""Rebuild the live scorecard from disk. Deterministic. No hot-path objects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.shadow_validation.accumulator import ShadowAccumulator
from bot.research.shadow_validation.artifacts import (
    integrity_from_records,
    load_json,
    run_paths,
    verify_resume,
)
from bot.research.shadow_validation.scorecard import build_scorecard
from bot.research.shadow_validation.verdict import decide


def iter_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def reduce_run(run_dir: Path | str, *, identity: dict[str, Any] | None = None) -> dict[str, Any]:
    paths = run_paths(Path(run_dir))
    manifest = load_json(paths["manifest"]) or {}
    frozen = load_json(paths["frozen_strategy"]) or identity or {}
    ident = identity or frozen
    if manifest and ident:
        try:
            verify_resume(manifest, ident)
        except Exception:
            # Caller may already have checked. Reducer still scans for MIXED_DATA.
            pass
    records = iter_records(paths["observations"])
    integrity = integrity_from_records(
        records,
        expected_fingerprint=str(ident.get("strategy_fingerprint") or ""),
        expected_run_id=str(ident.get("validation_run_id") or manifest.get("validation_run_id") or ""),
    )
    acc = ShadowAccumulator()
    start = manifest.get("run_start_ms")
    acc.run_start_ms = float(start) if start is not None else frozen.get("run_start_ms")
    mixed = 0
    fp = ident.get("strategy_fingerprint")
    run_id = ident.get("validation_run_id") or manifest.get("validation_run_id")
    for rec in records:
        if fp and rec.get("strategy_fingerprint") not in {fp, None}:
            mixed += 1
            continue
        if run_id and rec.get("validation_run_id") not in {run_id, None}:
            mixed += 1
            continue
        acc.complete_from_record(rec)
    if mixed:
        integrity = "MIXED_DATA"
    now_ms = acc.run_start_ms or 0.0
    if records:
        now_ms = max(float(r.get("signal_time_ms") or 0.0) for r in records) + 5000.0
    snap = acc.snapshot(now_ms=now_ms, fingerprint=str(fp or ""))
    decision = decide(snap)
    card = build_scorecard(snap, decision, integrity=integrity, identity=ident)
    return {
        "accumulator": acc,
        "snapshot": snap,
        "decision": decision,
        "scorecard": card,
        "VALIDATION_INTEGRITY": integrity,
        "n_records": len(records),
        "n_skipped_mixed": mixed,
    }

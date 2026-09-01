"""Tournament orchestration — one shared tape, split, costs, report."""

from __future__ import annotations

import json
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research import CRITERIA_VERSION, PACKAGE_LABEL
from bot.research.tournament.criteria import criteria_manifest
from bot.research.tournament.families import all_families
from bot.research.tournament.registry import ExperimentRegistry
from bot.research.tournament.scoreboard import build_scoreboard
from bot.research.tournament.tape_index import build_tape_index, make_split


def _load_horizon_readiness(report_path: Path) -> dict[str, str]:
    if not report_path.exists():
        return {}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    scores = (report.get("J_horizon_readiness") or {}).get("horizon_scores") or {}
    # Also accept flat supported list
    if not scores and report.get("supported_horizons"):
        for item in report["supported_horizons"]:
            text = str(item).replace("ms", "").replace("MS", "")
            try:
                h = int(float(text))
                scores[f"LEAD_LAG_{h}MS"] = "READY_WITH_CAUTION"
            except ValueError:
                continue
        for h in (50, 100, 250):
            scores.setdefault(f"LEAD_LAG_{h}MS", "NOT_READY")
    return {str(k): str(v) for k, v in scores.items()}


def run_tournament(
    *,
    research_path: Path | str = "data/research_marketdata",
    readiness_report: Path | str = "data/market_data_research_report.json",
    out_dir: Path | str | None = None,
    max_events: int | None = None,
    stride: int = 1,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    tracemalloc.start()
    root = Path(research_path)
    readiness = _load_horizon_readiness(Path(readiness_report))

    index = build_tape_index(root, max_events=max_events, stride=stride)
    split = make_split(index)

    if index.dataset_id == "NONE" or index.peak_points == 0:
        result = {
            "STATUS": "BLOCKED_BY_DATA",
            "DATASET_ID": None,
            "PACKAGE": PACKAGE_LABEL,
            "criteria_version": CRITERIA_VERSION,
            "note": "NO_REAL_TAPE or empty index",
            "scoreboard": [],
            "candidates": {},
            "ALL_STRATEGIES_REJECTED": True,
            "PAPER_CANDIDATES": [],
        }
        return result

    dataset_meta = {
        "dataset_id": index.dataset_id,
        "fingerprint": index.content_fingerprint,
        "duration_seconds": index.duration_seconds,
        "points": index.peak_points,
        "symbols": index.symbols,
        "venues": index.venues,
    }

    families = all_families()
    candidates = []
    per_strategy_ms: dict[str, float] = {}
    registry = ExperimentRegistry()

    for fam in families:
        ts = time.perf_counter()
        res = fam.run(
            index=index,
            split=split,
            horizon_readiness=readiness,
            dataset_meta=dataset_meta,
        )
        per_strategy_ms[fam.strategy_id] = (time.perf_counter() - ts) * 1000.0
        candidates.append(res)
        registry.append(
            {
                "experiment_id": res.experiment_id,
                "strategy_id": res.strategy_id,
                "dataset_id": index.dataset_id,
                "dataset_fingerprint": index.content_fingerprint,
                "verdict": res.verdict,
                "failed_gate": res.failed_gate,
                "parameters": res.frozen_params,
                "result_fingerprint": res.experiment_id,
                "criteria_version": CRITERIA_VERSION,
            }
        )

    scoreboard = build_scoreboard(candidates)
    paper = [c.strategy_id for c in candidates if c.verdict == "PAPER_CANDIDATE"]
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    elapsed = time.perf_counter() - t0

    out = {
        "STATUS": "COMPLETE",
        "PACKAGE": PACKAGE_LABEL,
        "criteria_version": CRITERIA_VERSION,
        "criteria": criteria_manifest(),
        "generated_at": datetime.now(UTC).isoformat(),
        "DATASET_ID": index.dataset_id,
        "DATA_DURATION": index.duration_seconds,
        "dataset_fingerprint": index.content_fingerprint,
        "DATA_READINESS": readiness,
        "DEVELOPMENT_WINDOW": (split.get("development") if split.get("available") else None),
        "FREEZE_BOUNDARY": (split.get("freeze_boundary") if split.get("available") else None),
        "OOS_WINDOW": (split.get("untouched_oos") if split.get("available") else None),
        "chrono_split": split,
        "candidates": {c.strategy_id: c.as_dict() for c in candidates},
        "scoreboard": scoreboard,
        "PAPER_CANDIDATES": paper,
        "ALL_STRATEGIES_REJECTED": len(paper) == 0,
        "PERFORMANCE": {
            "tournament_seconds": elapsed,
            "tape_load_seconds": index.load_seconds,
            "points_indexed": index.peak_points,
            "events_per_second": (index.peak_points / index.load_seconds)
            if index.load_seconds
            else None,
            "per_strategy_ms": per_strategy_ms,
            "peak_memory_mb": peak / (1024 * 1024),
            "current_memory_mb": current / (1024 * 1024),
        },
        "execution_enabled": False,
        "notes": [
            "Research-only tournament. Execution disabled.",
            "PAPER_CANDIDATE != proven live profitable.",
            "All families share fees, split, and acceptance rules.",
            "Do not rescue maker inventory; do not loosen fills.",
        ],
    }

    dest = Path(out_dir or f"data/research_tournament/{index.dataset_id}")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "results.json").write_text(
        json.dumps(out, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    (dest / "scoreboard.json").write_text(
        json.dumps(scoreboard, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    # Compact dashboard snapshot — never clobber from pytest tmp dirs.
    resolved = dest.resolve()
    if "data" in resolved.parts and "research_tournament" in resolved.parts:
        Path("data/research_tournament_report.json").write_text(
            json.dumps(
                {
                    "DATASET_ID": out["DATASET_ID"],
                    "DATA_DURATION": out["DATA_DURATION"],
                    "DEVELOPMENT_WINDOW": out["DEVELOPMENT_WINDOW"],
                    "FREEZE_BOUNDARY": out["FREEZE_BOUNDARY"],
                    "OOS_WINDOW": out["OOS_WINDOW"],
                    "DATA_READINESS": readiness,
                    "scoreboard": scoreboard,
                    "PAPER_CANDIDATES": paper,
                    "ALL_STRATEGIES_REJECTED": out["ALL_STRATEGIES_REJECTED"],
                    "candidates": out["candidates"],
                    "PERFORMANCE": out["PERFORMANCE"],
                    "STATUS": out["STATUS"],
                    "label": PACKAGE_LABEL,
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
    return out

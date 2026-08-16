"""Dataset session manifest for reproducibility."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from bot.market_data.research import SCHEMA_VERSION
from bot.market_data.research.diagnostics import latency_report
from bot.market_data.research.ordering import analyze_ordering
from bot.market_data.research.quality import classify_dataset_quality
from bot.market_data.research.schema import ResearchMarketEvent


def git_commit() -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            or None
        )
    except Exception:
        return None


def build_manifest(
    events: Sequence[ResearchMarketEvent],
    *,
    dataset_id: str,
    venues: Sequence[str],
    symbols: Sequence[str],
    recorder_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ts_list = [e.received_ts_ns for e in events]
    start = min(ts_list) if ts_list else None
    end = max(ts_list) if ts_list else None
    quality = classify_dataset_quality(events, venues_required=venues)
    ordering = analyze_ordering(events)
    latency = latency_report(events)
    return {
        "dataset_id": dataset_id,
        "schema_version": SCHEMA_VERSION,
        "code_version_git": git_commit(),
        "created_at": datetime.now(UTC).isoformat(),
        "start_ts_ns": start,
        "end_ts_ns": end,
        "venues": list(venues),
        "symbols": list(symbols),
        "event_count": len(events),
        "timestamp_coverage": {
            "exchange_ts": quality.get("exchange_ts_coverage"),
            "receive_ts": quality.get("receive_ts_coverage"),
            "sequence": quality.get("sequence_coverage"),
        },
        "quality": quality,
        "ordering": ordering.as_dict(),
        "latency": latency,
        "recorder": recorder_stats or {},
        "missing_data": quality.get("missing_venues") or [],
        "reconnects_approx": ordering.reconnect_boundaries,
        "sequence_gaps": ordering.sequence_gaps,
        "label": "RESEARCH_INFRASTRUCTURE",
    }


def write_manifest(manifest: dict[str, Any], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path

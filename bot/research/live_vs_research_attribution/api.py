"""Read-only API helpers for attribution audit JSON."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path("data/research/live_vs_research_attribution.json")


@lru_cache(maxsize=1)
def _cached_mtime(path_str: str) -> float:
    p = Path(path_str)
    return p.stat().st_mtime if p.is_file() else 0.0


def load_attribution_report(path: Path | None = None) -> dict[str, Any]:
    p = path or _DEFAULT_PATH
    if not p.is_file():
        return {
            "status": "NOT_GENERATED",
            "message": f"Run: python -m bot.research.live_vs_research_attribution",
            "path": str(p),
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"status": "ERROR", "message": str(exc), "path": str(p)}


def attribution_summary(path: Path | None = None) -> dict[str, Any]:
    report = load_attribution_report(path)
    if report.get("status"):
        return report
    return {
        "generated_at": report.get("generated_at"),
        "executive_summary": report.get("executive_summary"),
        "sample": report.get("sample"),
        "root_causes": report.get("root_causes"),
        "final_conclusions": report.get("final_conclusions"),
        "what_not_to_change_yet": report.get("what_not_to_change_yet"),
    }


def attribution_funnel(path: Path | None = None) -> dict[str, Any]:
    report = load_attribution_report(path)
    if report.get("status"):
        return report
    return report.get("funnel") or {}


def attribution_skips(path: Path | None = None) -> dict[str, Any]:
    report = load_attribution_report(path)
    if report.get("status"):
        return report
    return report.get("skip_attribution") or {}


def attribution_execution(path: Path | None = None) -> dict[str, Any]:
    report = load_attribution_report(path)
    if report.get("status"):
        return report
    return {
        "execution_attribution": report.get("execution_attribution"),
        "adverse_selection": report.get("adverse_selection"),
        "inventory_attribution": report.get("inventory_attribution"),
    }


def attribution_root_causes(path: Path | None = None) -> dict[str, Any]:
    report = load_attribution_report(path)
    if report.get("status"):
        return report
    return {
        "root_causes": report.get("root_causes"),
        "strategy_mismatch": report.get("strategy_mismatch"),
        "research_realism": report.get("research_realism"),
    }

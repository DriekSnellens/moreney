"""Retention helper — prune old research tape partitions before disk fills."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _partition_date(name: str) -> datetime.date | None:
    """Parse legacy ``YYYYMMDD`` or session ``date=YYYY-MM-DD`` directory names."""
    if name.startswith("date="):
        try:
            return datetime.strptime(name.split("=", 1)[1], "%Y-%m-%d").date()
        except ValueError:
            return None
    try:
        return datetime.strptime(name, "%Y%m%d").date()
    except ValueError:
        return None


def plan_retention(
    root: Path | str,
    *,
    retention_days: int,
    export_dir: Path | str | None = None,
) -> dict[str, Any]:
    """List partitions older than retention; optionally copy to export before delete."""
    root = Path(root)
    cutoff = datetime.now(UTC).date() - timedelta(days=max(0, retention_days))
    stale: list[str] = []
    if root.exists():
        for day_dir in root.iterdir():
            if not day_dir.is_dir():
                continue
            day = _partition_date(day_dir.name)
            if day is None:
                continue
            if day < cutoff:
                stale.append(str(day_dir))
    return {
        "root": str(root),
        "retention_days": retention_days,
        "cutoff_date": cutoff.isoformat(),
        "stale_partitions": stale,
        "export_dir": str(export_dir) if export_dir else None,
        "silent_delete": False,
        "note": "Call apply_retention(..., execute_delete=True) only after export.",
    }


def apply_retention(
    plan: dict[str, Any],
    *,
    execute_delete: bool = False,
    export_first: bool = True,
) -> dict[str, Any]:
    exported = []
    deleted = []
    export_dir = Path(plan["export_dir"]) if plan.get("export_dir") else None
    for part in plan.get("stale_partitions") or []:
        src = Path(part)
        if not src.exists():
            continue
        if export_first and export_dir is not None:
            dest = export_dir / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copytree(src, dest)
            exported.append(str(dest))
        if execute_delete:
            shutil.rmtree(src)
            deleted.append(str(src))
    return {
        "exported": exported,
        "deleted": deleted,
        "execute_delete": execute_delete,
    }


def prune_research_marketdata(
    root: Path | str,
    *,
    retention_days: int,
    execute_delete: bool = True,
    export_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Plan and optionally delete stale research tape partitions."""
    plan = plan_retention(root, retention_days=retention_days, export_dir=export_dir)
    result = apply_retention(plan, execute_delete=execute_delete, export_first=False)
    out = {**plan, **result}
    if result.get("deleted"):
        logger.info(
            "RESEARCH_RETENTION deleted=%s retention_days=%s root=%s",
            len(result["deleted"]),
            retention_days,
            plan["root"],
        )
    return out


def effective_retention_days(
    *,
    configured_days: int,
    disk_used_pct: float,
    warn_pct: float = 85.0,
    block_pct: float = 92.0,
    emergency_days: int = 1,
) -> int:
    """Tighten retention automatically when disk pressure rises."""
    if disk_used_pct >= block_pct:
        return min(configured_days, emergency_days)
    if disk_used_pct >= warn_pct:
        return min(configured_days, max(1, configured_days // 2))
    return configured_days

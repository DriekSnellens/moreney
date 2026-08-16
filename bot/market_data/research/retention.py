"""Retention helper — never silently delete without export opportunity."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def plan_retention(
    root: Path | str,
    *,
    retention_days: int,
    export_dir: Path | str | None = None,
) -> dict[str, Any]:
    """List partitions older than retention; optionally copy to export before delete.

    Does not delete unless ``execute_delete=True`` is passed to apply_retention.
    """
    root = Path(root)
    cutoff = datetime.now(UTC).date() - timedelta(days=max(0, retention_days))
    stale: list[str] = []
    if root.exists():
        for day_dir in root.iterdir():
            if not day_dir.is_dir():
                continue
            try:
                day = datetime.strptime(day_dir.name, "%Y%m%d").date()
            except ValueError:
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

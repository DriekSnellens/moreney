"""Disk space guard — fail-closed when the root filesystem is nearly full."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def disk_usage(path: Path | str = "/") -> dict[str, Any]:
    """Return used/total/free bytes and used_pct for ``path``."""
    usage = shutil.disk_usage(path)
    used_pct = (100.0 * usage.used / usage.total) if usage.total else 0.0
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_pct": round(used_pct, 2),
    }


def disk_guard_status(
    path: Path | str = "/",
    *,
    warn_pct: float = 85.0,
    block_pct: float = 92.0,
) -> dict[str, Any]:
    """Classify disk pressure for operator alerts and fail-closed gates."""
    stats = disk_usage(path)
    used = float(stats["used_pct"])
    if used >= block_pct:
        level = "block"
    elif used >= warn_pct:
        level = "warn"
    else:
        level = "ok"
    return {
        **stats,
        "level": level,
        "warn_pct": warn_pct,
        "block_pct": block_pct,
        "blocked": level == "block",
    }


def log_disk_guard(path: Path | str = "/", **kwargs: Any) -> dict[str, Any]:
    status = disk_guard_status(path, **kwargs)
    if status["level"] == "block":
        logger.error(
            "DISK_GUARD block used_pct=%.1f free=%s path=%s",
            status["used_pct"],
            status["free_bytes"],
            status["path"],
        )
    elif status["level"] == "warn":
        logger.warning(
            "DISK_GUARD warn used_pct=%.1f free=%s path=%s",
            status["used_pct"],
            status["free_bytes"],
            status["path"],
        )
    return status

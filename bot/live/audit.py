"""Phase 5 — append-only live audit log (no secrets)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class LiveAuditLog:
    """JSONL audit trail for live observe / order attempts."""

    def __init__(self, path: str | Path, *, max_entries_read: int = 500) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._max_read = max_entries_read

    def record(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "payload": _redact(payload or {}),
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        return row

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        lines: list[str] = []
        with self._lock:
            try:
                text = self._path.read_text(encoding="utf-8")
            except OSError:
                return []
        for line in text.splitlines():
            if line.strip():
                lines.append(line)
        out: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, self._max_read)) :]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(out))


_SECRET_KEYS = {
    "api_key",
    "api_secret",
    "secret",
    "passphrase",
    "password",
    "token",
    "authorization",
}


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        lk = str(key).lower()
        if any(s in lk for s in _SECRET_KEYS):
            clean[key] = "[redacted]"
        elif isinstance(value, dict):
            clean[key] = _redact(value)
        else:
            clean[key] = value
    return clean

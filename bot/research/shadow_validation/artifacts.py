"""Streaming artifacts for a shadow validation run.

Layout:
    data/research/shadow_validation/runs/<run_id>/
        manifest.json
        frozen_strategy.json
        acceptance_criteria.json
        observations.jsonl
        windows/W000.json
        daily/YYYY-MM-DD.json
        summaries/execution_gap.json
        summaries/adverse.json
        summaries/funnel.json
        accumulator.json
        final_results.json

JSONL is batched without per-event fsync. Summaries use atomic replace.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from bot.research.shadow_validation.protocol import (
    ACCEPTANCE_FILENAME,
    ACCUMULATOR_FILENAME,
    ARTIFACT_SCHEMA_VERSION,
    DEFAULT_RUNS_ROOT,
    FINAL_RESULTS_FILENAME,
    FROZEN_STRATEGY_FILENAME,
    MANIFEST_FILENAME,
    OBSERVATIONS_FILENAME,
    WRITER_BATCH_SIZE,
    WRITER_FLUSH_INTERVAL_S,
    acceptance_hash,
    config_hash,
    frozen_acceptance,
    protocol_hash,
    strategy_fingerprint,
)


class ResumeIncompatibleError(RuntimeError):
    pass


class MixedDataError(RuntimeError):
    pass


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dict(payload), fh, indent=2, sort_keys=True, default=str)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


class CompactObservationWriter:
    """Batched JSONL append. Hot path only enqueues dicts."""

    def __init__(
        self,
        path: Path | str,
        *,
        batch_size: int = WRITER_BATCH_SIZE,
        flush_interval_s: float = WRITER_FLUSH_INTERVAL_S,
    ) -> None:
        self.path = Path(path)
        self.batch_size = int(batch_size)
        self.flush_interval_s = float(flush_interval_s)
        self._buf: list[dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def enqueue(self, record: Mapping[str, Any]) -> None:
        self._buf.append(dict(record))
        now = time.monotonic()
        if len(self._buf) >= self.batch_size or (now - self._last_flush) >= self.flush_interval_s:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            self._last_flush = time.monotonic()
            return
        lines = "".join(json.dumps(r, separators=(",", ":"), default=str) + "\n" for r in self._buf)
        self._buf.clear()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(lines)
        self._last_flush = time.monotonic()

    @property
    def pending(self) -> int:
        return len(self._buf)


def write_accumulator_snapshot(path: Path | str, payload: Mapping[str, Any]) -> None:
    atomic_write_json(Path(path), payload)


def runs_root(root: Path | str | None = None) -> Path:
    return Path(root or DEFAULT_RUNS_ROOT)


def run_dir_for(run_id: str, *, root: Path | str | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in run_id)[:80]
    return runs_root(root) / safe


def run_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "run_dir": run_dir,
        "manifest": run_dir / MANIFEST_FILENAME,
        "frozen_strategy": run_dir / FROZEN_STRATEGY_FILENAME,
        "acceptance": run_dir / ACCEPTANCE_FILENAME,
        "observations": run_dir / OBSERVATIONS_FILENAME,
        "accumulator": run_dir / ACCUMULATOR_FILENAME,
        "windows": run_dir / "windows",
        "daily": run_dir / "daily",
        "summaries": run_dir / "summaries",
        "final_results": run_dir / FINAL_RESULTS_FILENAME,
        "execution_gap": run_dir / "summaries" / "execution_gap.json",
        "adverse": run_dir / "summaries" / "adverse.json",
        "funnel": run_dir / "summaries" / "funnel.json",
    }


def default_paths(run_dir: Path | str | None = None) -> dict[str, Path]:
    root = Path(run_dir) if run_dir is not None else run_dir_for("default")
    return run_paths(root)


def new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"cvd-shadow-{stamp}"


def write_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_json(path, payload)


def verify_resume(manifest: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    checks = (
        ("artifact_schema_version", ARTIFACT_SCHEMA_VERSION),
        ("strategy_fingerprint", current.get("strategy_fingerprint") or strategy_fingerprint()),
        ("config_hash", current.get("config_hash") or config_hash()),
        ("acceptance_hash", current.get("acceptance_hash") or acceptance_hash()),
        ("protocol_hash", current.get("protocol_hash") or protocol_hash()),
        ("git_commit", current.get("git_commit")),
    )
    for key, expected in checks:
        stored = manifest.get(key)
        if stored != expected:
            raise ResumeIncompatibleError(
                f"incompatible run: {key} stored={stored} current={expected}"
            )


def integrity_from_records(
    records: list[Mapping[str, Any]],
    *,
    expected_fingerprint: str,
    expected_run_id: str,
) -> str:
    if not records:
        return "UNKNOWN"
    fps = {r.get("strategy_fingerprint") for r in records}
    runs = {r.get("validation_run_id") for r in records}
    if None in fps or fps - {expected_fingerprint}:
        return "MIXED_DATA"
    if None in runs or runs - {expected_run_id}:
        return "MIXED_DATA"
    return "VALID"


def write_acceptance(path: Path) -> None:
    atomic_write_json(
        path,
        {
            "acceptance": frozen_acceptance(),
            "acceptance_hash": acceptance_hash(),
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        },
    )

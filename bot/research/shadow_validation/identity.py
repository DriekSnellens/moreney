"""Frozen strategy identity. Config mutation invalidates the live run."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.shadow_validation.artifacts import (
    ResumeIncompatibleError,
    atomic_write_json,
    load_json,
    verify_resume,
    write_acceptance,
)
from bot.research.shadow_validation.protocol import (
    ARTIFACT_SCHEMA_VERSION,
    DEFAULT_RUN_DIR,
    FROZEN_STRATEGY_FILENAME,
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    RUNTIME_ID_LIVE,
    STRATEGY_ID,
    acceptance_hash,
    config_hash,
    current_git_commit,
    frozen_acceptance,
    frozen_parameters,
    parameter_hash,
    protocol_hash,
    strategy_fingerprint,
)


def build_frozen_strategy(
    *,
    git_commit_override: str | None = None,
    validation_run_id: str | None = None,
    runtime_id: str | None = None,
) -> dict[str, Any]:
    commit = git_commit_override if git_commit_override is not None else current_git_commit()
    params = frozen_parameters()
    acceptance = frozen_acceptance()
    p_hash = parameter_hash()
    c_hash = config_hash()
    s_fp = strategy_fingerprint()
    a_hash = acceptance_hash()
    # Normalize before hashing so a stored file round-trips. Hashing None
    # while persisting "live_paper" would invalidate an unchanged freeze.
    rid = runtime_id or RUNTIME_ID_LIVE
    vid = validation_run_id
    run_fp = _run_fingerprint(s_fp, commit, protocol_hash(), vid, rid)
    return {
        "package": PACKAGE_LABEL,
        "protocol_version": PROTOCOL_VERSION,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_display_name": "Cross-Venue Dislocation",
        "frozen": True,
        "production_execution": "DISABLED",
        "paper_executor_live_trading": False,
        "dataset_independent": True,
        "git_commit": commit,
        "parameter_hash": p_hash,
        "config_hash": c_hash,
        "protocol_hash": protocol_hash(),
        "acceptance_hash": a_hash,
        "strategy_fingerprint": s_fp,
        "run_fingerprint": run_fp,
        "validation_run_id": vid,
        "runtime_id": rid,
        "parameters": params,
        "acceptance": acceptance,
        "identity_note": (
            "Every shadow event must reference strategy_fingerprint, config_hash, "
            "runtime_id, git_commit, and validation_run_id. "
            "Any change invalidates the current run."
        ),
    }


def event_identity(frozen: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_fingerprint": frozen.get("strategy_fingerprint"),
        "config_hash": frozen.get("config_hash"),
        "runtime_id": frozen.get("runtime_id"),
        "git_commit": frozen.get("git_commit"),
        "validation_run_id": frozen.get("validation_run_id"),
    }


def _run_fingerprint(
    strategy_fp: str,
    git_commit: str | None,
    proto: str,
    validation_run_id: str | None,
    runtime_id: str | None,
) -> str:
    from bot.research.shadow_validation.protocol import _stable_hash

    return _stable_hash(
        {
            "strategy_fingerprint": strategy_fp,
            "git_commit": git_commit,
            "protocol_hash": proto,
            "validation_run_id": validation_run_id,
            "runtime_id": runtime_id,
        }
    )


def frozen_strategy_path(run_dir: Path | str | None = None) -> Path:
    return Path(run_dir or DEFAULT_RUN_DIR) / FROZEN_STRATEGY_FILENAME


def write_frozen_strategy(
    payload: dict[str, Any],
    *,
    run_dir: Path | str | None = None,
) -> Path:
    path = frozen_strategy_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)
    return path


def load_frozen_strategy(run_dir: Path | str | None = None) -> dict[str, Any] | None:
    return load_json(frozen_strategy_path(run_dir))


def identity_matches(stored: dict[str, Any], current: dict[str, Any]) -> bool:
    keys = (
        "strategy_fingerprint",
        "run_fingerprint",
        "parameter_hash",
        "config_hash",
        "git_commit",
        "strategy_id",
        "acceptance_hash",
        "validation_run_id",
        "runtime_id",
    )
    return all(stored.get(k) == current.get(k) for k in keys)


def invalidate_run(run_dir: Path | str, *, reason: str) -> Path:
    src = Path(run_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = src.parent / f"{src.name}.invalidated.{stamp}"
    if src.exists():
        shutil.move(str(src), str(dest))
        marker = dest / "INVALIDATED.json"
        marker.write_text(
            json.dumps({"reason": reason, "invalidated_at": stamp, "VALIDATION_INTEGRITY": "INVALIDATED"}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    return dest


def ensure_frozen_identity(
    *,
    run_dir: Path | str | None = None,
    git_commit_override: str | None = None,
    validation_run_id: str | None = None,
    runtime_id: str | None = None,
    resume: bool = False,
) -> tuple[dict[str, Any], bool, str]:
    """Return (identity, invalidated, VALIDATION_INTEGRITY)."""
    directory = Path(run_dir or DEFAULT_RUN_DIR)
    stored = load_frozen_strategy(directory)
    current = build_frozen_strategy(
        git_commit_override=git_commit_override,
        validation_run_id=validation_run_id or (stored.get("validation_run_id") if stored else None),
        runtime_id=runtime_id or (stored.get("runtime_id") if stored else None),
    )
    if stored is None:
        if resume:
            raise ResumeIncompatibleError("resume requested but no frozen_strategy.json")
        write_frozen_strategy(current, run_dir=directory)
        write_acceptance(directory / "acceptance_criteria.json")
        return current, False, "VALID"
    # Rebuild current using stored run_id so a new uuid is not invented on match.
    current = build_frozen_strategy(
        git_commit_override=git_commit_override if git_commit_override is not None else stored.get("git_commit"),
        validation_run_id=stored.get("validation_run_id"),
        runtime_id=stored.get("runtime_id"),
    )
    if identity_matches(stored, current):
        return stored, False, "VALID"
    if resume:
        manifest = load_json(directory / "manifest.json") or stored
        try:
            verify_resume(manifest, current)
        except ResumeIncompatibleError:
            raise
        raise ResumeIncompatibleError("frozen identity mismatch on resume")
    invalidate_run(
        directory,
        reason=(
            "frozen identity mismatch: "
            f"stored_fp={stored.get('run_fingerprint')} "
            f"current_fp={current.get('run_fingerprint')}"
        ),
    )
    fresh = build_frozen_strategy(
        git_commit_override=git_commit_override,
        validation_run_id=validation_run_id,
        runtime_id=runtime_id,
    )
    write_frozen_strategy(fresh, run_dir=directory)
    write_acceptance(directory / "acceptance_criteria.json")
    return fresh, True, "INVALIDATED"

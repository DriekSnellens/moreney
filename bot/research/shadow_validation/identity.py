"""Frozen strategy identity. Config mutation invalidates the live run."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.shadow_validation.protocol import (
    ARTIFACT_SCHEMA_VERSION,
    DEFAULT_RUN_DIR,
    FROZEN_STRATEGY_FILENAME,
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    config_hash,
    current_git_commit,
    frozen_acceptance,
    frozen_parameters,
    parameter_hash,
    protocol_hash,
    strategy_fingerprint,
)


def build_frozen_strategy(*, git_commit_override: str | None = None) -> dict[str, Any]:
    commit = git_commit_override if git_commit_override is not None else current_git_commit()
    params = frozen_parameters()
    acceptance = frozen_acceptance()
    p_hash = parameter_hash()
    c_hash = config_hash()
    s_fp = strategy_fingerprint()
    run_fp = _run_fingerprint(s_fp, commit, protocol_hash())
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
        "strategy_fingerprint": s_fp,
        "run_fingerprint": run_fp,
        "parameters": params,
        "acceptance": acceptance,
        "identity_note": (
            "Every shadow event must reference strategy_fingerprint. "
            "Any strategy/config/code change invalidates the current run."
        ),
    }


def _run_fingerprint(strategy_fp: str, git_commit: str | None, proto: str) -> str:
    from bot.research.shadow_validation.protocol import _stable_hash

    return _stable_hash(
        {
            "strategy_fingerprint": strategy_fp,
            "git_commit": git_commit,
            "protocol_hash": proto,
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
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_frozen_strategy(run_dir: Path | str | None = None) -> dict[str, Any] | None:
    path = frozen_strategy_path(run_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def identity_matches(stored: dict[str, Any], current: dict[str, Any]) -> bool:
    keys = (
        "strategy_fingerprint",
        "run_fingerprint",
        "parameter_hash",
        "config_hash",
        "git_commit",
        "strategy_id",
    )
    return all(stored.get(k) == current.get(k) for k in keys)


def invalidate_run(run_dir: Path | str, *, reason: str) -> Path:
    """Archive the current run directory so a new freeze can start clean."""
    src = Path(run_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = src.parent / f"{src.name}.invalidated.{stamp}"
    if src.exists():
        shutil.move(str(src), str(dest))
        marker = dest / "INVALIDATED.json"
        marker.write_text(
            json.dumps({"reason": reason, "invalidated_at": stamp}, indent=2) + "\n",
            encoding="utf-8",
        )
    return dest


def ensure_frozen_identity(
    *,
    run_dir: Path | str | None = None,
    git_commit_override: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return (identity, invalidated). Writes frozen_strategy.json if missing.

    A mismatch archives the previous run and writes a fresh freeze.
    """
    directory = Path(run_dir or DEFAULT_RUN_DIR)
    current = build_frozen_strategy(git_commit_override=git_commit_override)
    stored = load_frozen_strategy(directory)
    if stored is None:
        write_frozen_strategy(current, run_dir=directory)
        return current, False
    if identity_matches(stored, current):
        return stored, False
    invalidate_run(
        directory,
        reason=(
            "frozen identity mismatch: "
            f"stored_fp={stored.get('run_fingerprint')} "
            f"current_fp={current.get('run_fingerprint')}"
        ),
    )
    write_frozen_strategy(current, run_dir=directory)
    return current, True

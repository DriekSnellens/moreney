"""Immutable experiment freeze + fingerprint."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from typing import Any

from bot.research import CRITERIA_VERSION
from bot.research.tournament.economics import shared_cost_assumptions


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


def freeze_experiment(
    *,
    strategy_id: str,
    dataset_id: str,
    dataset_fingerprint: str,
    parameters: dict[str, Any],
    development_window: dict[str, Any],
    freeze_boundary: dict[str, Any],
    oos_window: dict[str, Any],
    feature_definitions: list[str],
    execution_assumptions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "strategy_id": strategy_id,
        "dataset_id": dataset_id,
        "dataset_fingerprint": dataset_fingerprint,
        "parameters": parameters,
        "feature_definitions": feature_definitions,
        "cost_assumptions": shared_cost_assumptions(),
        "execution_assumptions": execution_assumptions
        or {"no_queue_fills": True, "trade_through_baseline": True},
        "development_window": development_window,
        "freeze_boundary": freeze_boundary,
        "oos_window": oos_window,
        "criteria_version": CRITERIA_VERSION,
        "git_commit": git_commit(),
        "freeze_timestamp": datetime.now(UTC).isoformat(),
        "immutable": True,
    }
    fp = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    experiment_id = f"exp-{strategy_id}-{fp[:12]}"
    payload["experiment_id"] = experiment_id
    payload["experiment_fingerprint"] = fp
    return payload


def assert_params_unchanged(frozen: dict[str, Any], params: dict[str, Any]) -> None:
    if frozen.get("parameters") != params:
        raise RuntimeError("OOS evaluation attempted to modify frozen parameters")

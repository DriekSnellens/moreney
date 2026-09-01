"""Atomic on-disk artifacts for the streaming execution-realism runner.

Layout:
    data/research/execution_realism/runs/<run_id>/
        manifest.json
        config.json
        windows/<window_id>/scenario_<safe_id>.json
        summaries/scenario_<safe_id>.json

A killed process must not leave a valid-looking corrupt artifact: writes go
to a sibling .tmp file, fsync, then os.replace.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from bot.research.execution_realism.config import ARTIFACT_SCHEMA_VERSION, protocol_hash
from bot.research.tournament.freeze import git_commit

SCHEMA_VERSION = ARTIFACT_SCHEMA_VERSION
_FINGERPRINT_SKIP = frozenset({"deterministic_fingerprint", "generated_at", "git_commit"})


def scenario_safe_id(scenario_id: str) -> str:
    return scenario_id.replace("|", "__").replace("/", "_")


def scenario_config_hash(scenario: Mapping[str, str]) -> str:
    payload = {
        "fill_model": scenario["fill_model"],
        "latency_scenario": scenario["latency_scenario"],
        "hedge_scenario": scenario["hedge_scenario"],
        "cancel_scenario": scenario["cancel_scenario"],
        "scenario_id": scenario["scenario_id"],
        "protocol_hash": protocol_hash(),
        "schema_version": SCHEMA_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k not in _FINGERPRINT_SKIP}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
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
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def window_artifact_path(run_dir: Path, window_id: str, scenario_id: str) -> Path:
    return run_dir / "windows" / window_id / f"scenario_{scenario_safe_id(scenario_id)}.json"


def summary_artifact_path(run_dir: Path, scenario_id: str) -> Path:
    return run_dir / "summaries" / f"scenario_{scenario_safe_id(scenario_id)}.json"


def artifact_is_valid(
    path: Path,
    *,
    dataset_fingerprint: str,
    scenario_hash: str,
    schema_version: str = SCHEMA_VERSION,
) -> bool:
    data = load_json(path)
    if data is None:
        return False
    if data.get("schema_version") != schema_version:
        return False
    if data.get("dataset_fingerprint") != dataset_fingerprint:
        return False
    if data.get("scenario_config_hash") != scenario_hash:
        return False
    stored = data.get("deterministic_fingerprint")
    if not stored:
        return False
    return stored == canonical_fingerprint(data)


def write_window_artifact(
    run_dir: Path,
    *,
    window_id: str,
    scenario: Mapping[str, str],
    accumulator: Mapping[str, Any],
    execution_nets: list[str],
    dataset_fingerprint: str,
    tape_fingerprint: str,
    parent_canonical_net_sum: str,
) -> Path:
    scen_hash = scenario_config_hash(scenario)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "git_commit": git_commit(),
        "dataset_fingerprint": dataset_fingerprint,
        "tape_fingerprint": tape_fingerprint,
        "window_id": window_id,
        "scenario_id": scenario["scenario_id"],
        "fill_model": scenario["fill_model"],
        "latency_scenario": scenario["latency_scenario"],
        "hedge_scenario": scenario["hedge_scenario"],
        "cancel_scenario": scenario["cancel_scenario"],
        "scenario_config_hash": scen_hash,
        "signal_count": accumulator["signal_count"],
        "fill_count": accumulator["fill_count"],
        "waterfall_sums": {
            "gross_sum": accumulator["gross_sum"],
            "fee_sum": accumulator["fee_sum"],
            "slippage_sum": accumulator["slippage_sum"],
            "adverse_sum": accumulator["adverse_sum"],
            "inventory_sum": accumulator["inventory_sum"],
            "latency_sum": accumulator["latency_sum"],
            "hedge_sum": accumulator["hedge_sum"],
        },
        "canonical_replay_net": accumulator["canonical_replay_net_sum"],
        "parent_canonical_net_sum": parent_canonical_net_sum,
        "expected_net": accumulator["expected_net_sum"],
        "execution_net": accumulator["execution_net_sum"],
        "max_drawdown": accumulator["max_drawdown"],
        "accounting_identity_status": accumulator["accounting_identity_status"],
        "accumulator": dict(accumulator),
        "execution_nets": execution_nets,
    }
    payload["deterministic_fingerprint"] = canonical_fingerprint(payload)
    dest = window_artifact_path(run_dir, window_id, scenario["scenario_id"])
    atomic_write_json(dest, payload)
    return dest


def write_run_meta(
    run_dir: Path,
    *,
    run_id: str,
    dataset_fingerprint: str,
    tape_fingerprint: str,
    dataset_id: str,
    window_ids: list[str],
    scenarios: list[Mapping[str, str]],
    extra: Mapping[str, Any] | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "git_commit": git_commit(),
        "dataset_id": dataset_id,
        "dataset_fingerprint": dataset_fingerprint,
        "tape_fingerprint": tape_fingerprint,
        "protocol_hash": protocol_hash(),
        "window_ids": list(window_ids),
        "scenario_ids": [s["scenario_id"] for s in scenarios],
    }
    if extra:
        manifest.update(dict(extra))
    config = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "dataset_fingerprint": dataset_fingerprint,
        "tape_fingerprint": tape_fingerprint,
        "scenarios": [dict(s) for s in scenarios],
        "scenario_config_hashes": {s["scenario_id"]: scenario_config_hash(s) for s in scenarios},
        "protocol_hash": protocol_hash(),
    }
    atomic_write_json(run_dir / "manifest.json", manifest)
    atomic_write_json(run_dir / "config.json", config)

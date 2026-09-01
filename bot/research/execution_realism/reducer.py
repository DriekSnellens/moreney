"""Reduce compact per-window artifacts into final scenario results.

Does not load ExecutionWaterfall objects. Exact equality for sums.
Max drawdown is reconstructed by streaming compact execution_net strings
in window order (same order as the original sequential replay).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from bot.research.execution_realism.accumulator import ExecutionAccumulator
from bot.research.execution_realism.artifacts import (
    SCHEMA_VERSION,
    artifact_is_valid,
    atomic_write_json,
    load_json,
    scenario_config_hash,
    summary_artifact_path,
    window_artifact_path,
)
from bot.research.execution_realism.replay import scenario_result_dict

_ZERO = Decimal("0")


def reduce_run(
    run_dir: Path,
    *,
    scenarios: Sequence[Mapping[str, str]],
    window_ids: Sequence[str],
    dataset_fingerprint: str,
    parent_canonical_net: Decimal | None = None,
) -> dict[str, Any]:
    """Merge window artifacts into per-scenario summaries and result dicts."""
    scenario_results: list[dict[str, Any]] = []
    issues: list[str] = []
    for scen in scenarios:
        sid = scen["scenario_id"]
        scen_hash = scenario_config_hash(scen)
        acc = ExecutionAccumulator()
        window_nets: list[Decimal] = []
        for wid in window_ids:
            path = window_artifact_path(run_dir, wid, sid)
            if not artifact_is_valid(
                path,
                dataset_fingerprint=dataset_fingerprint,
                scenario_hash=scen_hash,
                schema_version=SCHEMA_VERSION,
            ):
                issues.append(f"missing_or_invalid:{wid}:{sid}")
                continue
            payload = load_json(path)
            if payload is None:
                issues.append(f"unreadable:{wid}:{sid}")
                continue
            wacc = ExecutionAccumulator.from_dict(payload.get("accumulator") or {})
            acc.add_sums_from(wacc)
            window_nets.append(wacc.execution_net_sum)
            for net_s in payload.get("execution_nets") or []:
                acc.observe_drawdown(Decimal(str(net_s)))
            del payload
        canon = parent_canonical_net
        if canon is None:
            canon = acc.canonical_replay_net_sum
        result = scenario_result_dict(
            scen,
            acc,
            window_execution_nets=window_nets,
            parent_canonical_net=canon,
        )
        scenario_results.append(result)
        atomic_write_json(summary_artifact_path(run_dir, sid), result)

    return {
        "scenario_results": scenario_results,
        "issues": issues,
        "REDUCER_STATUS": "PASS" if not issues else "FAIL",
    }

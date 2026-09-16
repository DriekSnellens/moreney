"""Streaming execution-realism runner: memory, resume, artifacts, equivalence."""

from __future__ import annotations

import gc
import json
from decimal import Decimal
from pathlib import Path

from bot.research.execution_realism.accumulator import (
    ExecutionAccumulator,
    equity_curve_max_drawdown,
    streaming_max_drawdown,
)
from bot.research.execution_realism.artifacts import (
    SCHEMA_VERSION,
    artifact_is_valid,
    atomic_write_json,
    canonical_fingerprint,
    load_json,
    scenario_config_hash,
    window_artifact_path,
)
from bot.research.execution_realism.benchmark import make_fixture, run_benchmark, screen_subset
from bot.research.execution_realism.config import EXECUTION_REALISM_PRODUCTION_ENABLED
from bot.research.execution_realism.engine import replay_fixture_legacy, replay_fixture_streaming
from bot.research.execution_realism.models import ExecutionWaterfall
from bot.research.execution_realism.replay import (
    accumulator_from_waterfalls,
    legacy_replay_window,
    streaming_replay_window,
)
from bot.research.execution_realism.scenario import scenario_id

_ZERO = Decimal("0")

_EXACT_KEYS = (
    "n_signals",
    "n_fills",
    "n_partial",
    "n_no_fill",
    "execution_net_eur",
    "canonical_replay_net_eur",
    "gross_sum",
    "fee_sum",
    "slippage_sum",
    "adverse_sum",
    "inventory_sum",
    "expected_net_sum",
    "max_drawdown",
    "positive_windows",
    "negative_windows",
    "deterministic_fingerprint",
    "accounting_identity_status",
)


def _assert_equivalent(legacy: dict, streaming: dict) -> None:
    assert legacy["n_signals"] == streaming["n_signals"]
    assert legacy["CANONICAL_REPLAY_NET"] == streaming["CANONICAL_REPLAY_NET"]
    assert len(legacy["scenario_results"]) == len(streaming["scenario_results"])
    for a, b in zip(legacy["scenario_results"], streaming["scenario_results"], strict=True):
        assert a["scenario_id"] == b["scenario_id"]
        for key in _EXACT_KEYS:
            assert a[key] == b[key], f"{a['scenario_id']} {key}: {a[key]!r} != {b[key]!r}"
        assert a["outcome_counts"] == b["outcome_counts"]
        assert Decimal(str(a["execution_net_eur"])) == Decimal(str(b["execution_net_eur"]))


def test_streaming_vs_legacy_equivalence(tmp_path: Path) -> None:
    windows = make_fixture(24, 3)
    scenarios = screen_subset(4)
    legacy = replay_fixture_legacy(windows, scenarios)
    streaming = replay_fixture_streaming(
        windows,
        scenarios,
        run_dir=tmp_path / "run",
        dataset_fingerprint="eq",
        tape_fingerprint="eq",
    )
    _assert_equivalent(legacy, streaming)
    for row in streaming["scenario_results"]:
        assert row["accounting_identity_status"] == "PASS"


def test_streaming_max_drawdown_matches_equity_curve() -> None:
    pnls = [
        Decimal("10"),
        Decimal("-5"),
        Decimal("-20"),
        Decimal("8"),
    ]
    assert streaming_max_drawdown(pnls) == equity_curve_max_drawdown(pnls)
    assert streaming_max_drawdown(pnls) == Decimal("-25")
    empty: list[Decimal] = []
    assert streaming_max_drawdown(empty) == equity_curve_max_drawdown(empty) == _ZERO
    neg = [Decimal("-1"), Decimal("-2"), Decimal("0.5")]
    assert streaming_max_drawdown(neg) == equity_curve_max_drawdown(neg)


def test_drawdown_on_fixture_matches_legacy_waterfalls() -> None:
    windows = make_fixture(18, 2)
    scen = screen_subset(1)[0]
    signals = [s for _w, sigs in windows for s in sigs]
    wfs = []
    for _wid, sigs in windows:
        wfs.extend(legacy_replay_window(sigs, scen))
    pnls = [wf.execution_net for wf in wfs]
    acc = accumulator_from_waterfalls(wfs, signals)
    assert acc.max_drawdown == equity_curve_max_drawdown(pnls)
    stream_acc, _nets = streaming_replay_window(signals, scen)
    assert stream_acc.max_drawdown == acc.max_drawdown


def test_memory_does_not_retain_waterfalls(tmp_path: Path) -> None:
    windows = make_fixture(80, 2)
    scenarios = screen_subset(6)
    gc.collect()
    replay_fixture_streaming(
        windows,
        scenarios,
        run_dir=tmp_path / "mem",
        dataset_fingerprint="mem",
        tape_fingerprint="mem",
    )
    gc.collect()
    live = sum(1 for obj in gc.get_objects() if isinstance(obj, ExecutionWaterfall))
    assert live < 16, f"streaming retained {live} ExecutionWaterfall objects"

    legacy = replay_fixture_legacy(windows, scenarios, retain_waterfalls=True)
    gc.collect()
    live_legacy = sum(1 for obj in gc.get_objects() if isinstance(obj, ExecutionWaterfall))
    expected_min = 80 * 6
    assert live_legacy >= expected_min, f"legacy expected >={expected_min} waterfalls, got {live_legacy}"
    assert int(legacy["waterfall_count"]) == expected_min
    del legacy
    gc.collect()


def test_memory_not_linear_in_scenarios_times_signals(tmp_path: Path) -> None:
    small = make_fixture(40, 2)
    large = make_fixture(160, 2)
    scenarios = screen_subset(5)

    def _live_after(windows, name: str) -> int:
        replay_fixture_streaming(
            windows,
            scenarios,
            run_dir=tmp_path / name,
            dataset_fingerprint=name,
            tape_fingerprint=name,
        )
        gc.collect()
        return sum(1 for obj in gc.get_objects() if isinstance(obj, ExecutionWaterfall))

    n_small = _live_after(small, "small")
    n_large = _live_after(large, "large")
    assert n_small < 16
    assert n_large < 16
    # 4x signals must not produce 4x retained waterfalls
    assert n_large <= n_small + 8


def test_resume_reuses_valid_artifacts(tmp_path: Path) -> None:
    windows = make_fixture(12, 2)
    scenarios = screen_subset(2)
    run_dir = tmp_path / "resume"
    first = replay_fixture_streaming(
        windows, scenarios, run_dir=run_dir, dataset_fingerprint="r", tape_fingerprint="r"
    )
    assert first["windows_skipped"] == []
    assert len(first["windows_replayed"]) == 4
    # Simulate interruption: delete one artifact after a complete first run
    victim = window_artifact_path(run_dir, "W1", scenarios[1]["scenario_id"])
    assert victim.exists()
    victim.unlink()
    second = replay_fixture_streaming(
        windows,
        scenarios,
        run_dir=run_dir,
        dataset_fingerprint="r",
        tape_fingerprint="r",
        resume=True,
    )
    assert "W1:" + scenarios[1]["scenario_id"] in second["windows_replayed"]
    assert len(second["windows_skipped"]) == 3
    third = replay_fixture_streaming(
        windows,
        scenarios,
        run_dir=tmp_path / "clean",
        dataset_fingerprint="r",
        tape_fingerprint="r",
    )
    _assert_equivalent(
        {"n_signals": first["n_signals"], "CANONICAL_REPLAY_NET": first["CANONICAL_REPLAY_NET"], "scenario_results": first["scenario_results"]},
        third,
    )
    _assert_equivalent(
        {"n_signals": second["n_signals"], "CANONICAL_REPLAY_NET": second["CANONICAL_REPLAY_NET"], "scenario_results": second["scenario_results"]},
        third,
    )


def test_corrupt_artifacts_are_recomputed(tmp_path: Path) -> None:
    windows = make_fixture(8, 2)
    scenarios = screen_subset(1)
    run_dir = tmp_path / "corrupt"
    replay_fixture_streaming(
        windows, scenarios, run_dir=run_dir, dataset_fingerprint="c", tape_fingerprint="c"
    )
    sid = scenarios[0]["scenario_id"]
    path = window_artifact_path(run_dir, "W0", sid)
    original = path.read_text(encoding="utf-8")

    path.write_text(original[:40], encoding="utf-8")
    truncated = replay_fixture_streaming(
        windows, scenarios, run_dir=run_dir, dataset_fingerprint="c", tape_fingerprint="c", resume=True
    )
    assert any(k.startswith("W0:") for k in truncated["windows_replayed"])

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["deterministic_fingerprint"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    bad_fp = replay_fixture_streaming(
        windows, scenarios, run_dir=run_dir, dataset_fingerprint="c", tape_fingerprint="c", resume=True
    )
    assert any(k.startswith("W0:") for k in bad_fp["windows_replayed"])

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scenario_config_hash"] = "deadbeef"
    payload["deterministic_fingerprint"] = canonical_fingerprint(payload)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    bad_hash = replay_fixture_streaming(
        windows, scenarios, run_dir=run_dir, dataset_fingerprint="c", tape_fingerprint="c", resume=True
    )
    assert any(k.startswith("W0:") for k in bad_hash["windows_replayed"])

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["dataset_fingerprint"] = "other-tape"
    payload["deterministic_fingerprint"] = canonical_fingerprint(payload)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    bad_ds = replay_fixture_streaming(
        windows, scenarios, run_dir=run_dir, dataset_fingerprint="c", tape_fingerprint="c", resume=True
    )
    assert any(k.startswith("W0:") for k in bad_ds["windows_replayed"])

    tmp = path.with_suffix(path.suffix + ".partial.tmp")
    tmp.write_text("{", encoding="utf-8")
    assert load_json(tmp) is None
    assert not artifact_is_valid(
        tmp,
        dataset_fingerprint="c",
        scenario_hash=scenario_config_hash(scenarios[0]),
        schema_version=SCHEMA_VERSION,
    )


def test_atomicity_tmp_is_not_valid(tmp_path: Path) -> None:
    dest = tmp_path / "windows" / "W0" / "scenario_x.json"
    atomic_write_json(dest, {"ok": True, "n": 1})
    assert dest.exists()
    leftovers = list(dest.parent.glob("*.tmp"))
    assert leftovers == []
    # Incomplete sibling must not be treated as the artifact
    sibling = dest.parent / (dest.name + ".orphan.tmp")
    sibling.write_text("{not json", encoding="utf-8")
    loaded = load_json(dest)
    assert loaded is not None
    assert loaded["ok"] is True


def test_world_isolation() -> None:
    windows = make_fixture(10, 1)
    scenarios = screen_subset(2)
    signals = windows[0][1]
    acc_a, _ = streaming_replay_window(signals, scenarios[0])
    acc_a.execution_net_sum += Decimal("999999")
    acc_a.outcome_counts["MUTATED"] = 99
    acc_b, _ = streaming_replay_window(signals, scenarios[1])
    assert "MUTATED" not in acc_b.outcome_counts
    assert acc_b.execution_net_sum != acc_a.execution_net_sum
    # Shared tape points are not mutated by replay (SeriesPoint is frozen/slots).
    p0 = signals[0].points[0]
    before = (p0.ts_ns, p0.mid, p0.bid, p0.ask, p0.bid_size, p0.ask_size)
    streaming_replay_window(signals, scenarios[0])
    p1 = signals[0].points[0]
    after = (p1.ts_ns, p1.mid, p1.bid, p1.ask, p1.bid_size, p1.ask_size)
    assert before == after
    a = dict(scenarios[0])
    b = dict(scenarios[1])
    a["fill_model"] = "MUTATED_MODEL"
    assert scenarios[0]["fill_model"] != "MUTATED_MODEL"
    assert scenarios[1]["fill_model"] == b["fill_model"]


def test_determinism_stable_fingerprints(tmp_path: Path) -> None:
    windows = make_fixture(16, 2)
    scenarios = screen_subset(3)
    a = replay_fixture_streaming(
        windows, scenarios, run_dir=tmp_path / "d1", dataset_fingerprint="d", tape_fingerprint="d"
    )
    b = replay_fixture_streaming(
        windows, scenarios, run_dir=tmp_path / "d2", dataset_fingerprint="d", tape_fingerprint="d"
    )
    fps_a = [r["deterministic_fingerprint"] for r in a["scenario_results"]]
    fps_b = [r["deterministic_fingerprint"] for r in b["scenario_results"]]
    assert fps_a == fps_b
    assert all(len(fp) == 64 for fp in fps_a)


def test_accounting_identity_on_streamed_sums() -> None:
    from bot.research.execution_realism.accounting import audit_waterfall
    from bot.research.execution_realism.replay import simulate_one

    windows = make_fixture(12, 1)
    scen = {
        "fill_model": "EXISTING_TRADE_THROUGH",
        "latency_scenario": "NORMAL",
        "hedge_scenario": "NORMAL",
        "cancel_scenario": "NORMAL",
        "scenario_id": scenario_id("EXISTING_TRADE_THROUGH", "NORMAL", "NORMAL", "NORMAL"),
    }
    signals = windows[0][1]
    acc, _ = streaming_replay_window(signals, scen)
    assert acc.accounting_identity_status == "PASS"
    assert acc.accounting_failures == 0
    for sig in signals:
        wf = simulate_one(sig, scen)
        assert audit_waterfall(wf)["ACCOUNTING_AUDIT"] == "PASS"
        if wf.fill_status.value != "NO_FILL":
            assert wf.waterfall_residual() == _ZERO


def test_production_execution_still_disabled() -> None:
    assert EXECUTION_REALISM_PRODUCTION_ENABLED is False


def test_merge_accumulators_additive() -> None:
    a = ExecutionAccumulator()
    b = ExecutionAccumulator()
    a.observe_drawdown(Decimal("10"))
    b.observe_drawdown(Decimal("-3"))
    a.signal_count = 2
    a.execution_net_sum = Decimal("10")
    b.signal_count = 3
    b.execution_net_sum = Decimal("-3")
    merged = a.merge(b)
    assert merged.signal_count == 5
    assert merged.execution_net_sum == Decimal("7")
    # Drawdown is sequential and is NOT min() of independent windows
    assert merged.max_drawdown == a.max_drawdown


def test_benchmark_streaming_uses_less_waterfall_memory() -> None:
    result = run_benchmark(n_signals=60, n_scenarios=4, n_windows=2, artifact_dir="/tmp/er_bench_test")
    assert result["streaming_waterfall_objects"] < 16
    assert int(result["legacy_retained_count"]) >= 60 * 4
    assert int(result["legacy_waterfall_objects"]) >= 60 * 4
    assert result["artifacts_written"] == 2 * 4
    assert result["streaming_seconds"] > 0
    assert result["legacy_seconds"] > 0

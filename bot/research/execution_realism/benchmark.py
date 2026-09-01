"""Small-fixture benchmark: legacy in-memory waterfalls vs streaming replay.

Measures wall clock, peak RSS, signals/sec, artifacts/sec.
Does not change fill/fee/adverse assumptions.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import time
from decimal import Decimal
from pathlib import Path

from bot.research.execution_realism.engine import replay_fixture_legacy, replay_fixture_streaming
from bot.research.execution_realism.memory import current_rss_mb, peak_rss_mb
from bot.research.execution_realism.models import ExecutionWaterfall
from bot.research.execution_realism.replay import WindowSignal
from bot.research.execution_realism.scenario import scenario_id
from bot.research.tournament.tape_index import SeriesPoint


def _points(start_ns: int, n: int = 8, spread_bps: float = 5.0) -> list[SeriesPoint]:
    pts = []
    for i in range(n):
        mid = 1000.0 + (i % 7) * 0.02
        half = mid * spread_bps / 20000.0
        pts.append(
            SeriesPoint(
                ts_ns=start_ns + i * 100_000_000,
                mid=mid,
                bid=mid - half,
                ask=mid + half,
                bid_size=1.0,
                ask_size=1.0,
                exchange_ts_ns=None,
                sequence=i,
            )
        )
    return pts


def make_fixture(
    n_signals: int,
    n_windows: int,
    *,
    start_ns: int = 10**18,
) -> list[tuple[str, list[WindowSignal]]]:
    """Deterministic synthetic tape slices. Not a research conclusion fixture."""
    windows: list[tuple[str, list[WindowSignal]]] = []
    per = max(1, n_signals // n_windows)
    extra = n_signals - per * n_windows
    idx = 0
    for w in range(n_windows):
        count = per + (1 if w < extra else 0)
        sigs: list[WindowSignal] = []
        for j in range(count):
            ts = start_ns + idx * 10_000_000
            pts = _points(ts, n=8)
            fwd = 0.004 if idx % 3 else -0.001
            sigs.append(
                WindowSignal(
                    signal_id=f"S{idx}",
                    strategy_id="H-0005",
                    symbol="BTCEUR" if idx % 2 == 0 else "ETHEUR",
                    route="okx|bitvavo",
                    venue="okx",
                    venue_exit="bitvavo",
                    side="BUY" if idx % 2 == 0 else "SELL",
                    forward=fwd,
                    observed_at_ns=ts,
                    entry_price=Decimal("1000"),
                    exchange_ts_available=False,
                    canonical_net=Decimal("3.37") if fwd > 0 else Decimal("-0.80"),
                    points=pts,
                )
            )
            idx += 1
        windows.append((f"W{w}", sigs))
    return windows


def screen_subset(n: int) -> list[dict[str, str]]:
    fills = ("EXISTING_TRADE_THROUGH", "POST_ONLY_SURVIVAL", "DEPTH_CONSTRAINED", "UNCERTAINTY_BOUNDED")
    lats = ("IDEALIZED", "FAST", "NORMAL", "SLOW", "STRESSED")
    hedges = ("INSTANT", "FAST", "NORMAL", "SLOW")
    rows = []
    for fm in fills:
        for lat in lats:
            for hg in hedges:
                rows.append(
                    {
                        "fill_model": fm,
                        "latency_scenario": lat,
                        "hedge_scenario": hg,
                        "cancel_scenario": "NORMAL",
                        "scenario_id": scenario_id(fm, lat, hg, "NORMAL"),
                    }
                )
                if len(rows) >= n:
                    return rows
    return rows[:n]


def _waterfall_count() -> int:
    gc.collect()
    return sum(1 for obj in gc.get_objects() if isinstance(obj, ExecutionWaterfall))


def run_benchmark(
    *,
    n_signals: int = 400,
    n_scenarios: int = 8,
    n_windows: int = 4,
    artifact_dir: str | None = None,
) -> dict[str, object]:
    windows = make_fixture(n_signals, n_windows)
    scenarios = screen_subset(n_scenarios)
    n_replays = n_signals * n_scenarios

    gc.collect()
    rss0 = current_rss_mb()
    t0 = time.perf_counter()
    legacy = replay_fixture_legacy(windows, scenarios, retain_waterfalls=True)
    t_legacy = time.perf_counter() - t0
    rss_legacy = current_rss_mb()
    peak_legacy = peak_rss_mb()
    wf_legacy = _waterfall_count()
    legacy_held = int(legacy.get("waterfall_count") or 0)
    del legacy
    gc.collect()

    dest = Path(artifact_dir or "/tmp/execution_realism_bench")
    if dest.exists():
        import shutil

        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    t1 = time.perf_counter()
    streaming = replay_fixture_streaming(
        windows,
        scenarios,
        run_dir=dest,
        dataset_fingerprint="bench",
        tape_fingerprint="bench",
    )
    t_stream = time.perf_counter() - t1
    rss_stream = current_rss_mb()
    peak_stream = peak_rss_mb()
    wf_stream = _waterfall_count()
    n_art = len(streaming.get("windows_replayed") or [])
    del streaming
    gc.collect()

    return {
        "n_signals": n_signals,
        "n_scenarios": n_scenarios,
        "n_windows": n_windows,
        "n_replays": n_replays,
        "legacy_seconds": round(t_legacy, 4),
        "streaming_seconds": round(t_stream, 4),
        "legacy_rss_delta_mb": round(rss_legacy - rss0, 2),
        "streaming_rss_delta_mb": round(rss_stream - rss0, 2),
        "legacy_peak_rss_mb": round(peak_legacy, 1),
        "streaming_peak_rss_mb": round(peak_stream, 1),
        "legacy_waterfall_objects": wf_legacy,
        "legacy_retained_count": legacy_held,
        "streaming_waterfall_objects": wf_stream,
        "legacy_signals_per_sec": round(n_replays / t_legacy, 1) if t_legacy > 0 else 0,
        "streaming_signals_per_sec": round(n_replays / t_stream, 1) if t_stream > 0 else 0,
        "artifacts_written": n_art,
        "artifacts_per_sec": round(n_art / t_stream, 2) if t_stream > 0 else 0,
        "baseline_rss_mb": round(rss0, 1),
        "pid": os.getpid(),
    }


def run_isolated_mode(
    mode: str,
    *,
    n_signals: int,
    n_scenarios: int,
    n_windows: int,
) -> dict[str, object]:
    """Run legacy or streaming in a fresh subprocess so peak RSS is not inherited."""
    import json
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent(
        f"""
        import gc, json, time, shutil
        from pathlib import Path
        from bot.research.execution_realism.benchmark import (
            make_fixture, screen_subset, _waterfall_count,
        )
        from bot.research.execution_realism.engine import (
            replay_fixture_legacy, replay_fixture_streaming,
        )
        from bot.research.execution_realism.memory import current_rss_mb, peak_rss_mb
        windows = make_fixture({n_signals}, {n_windows})
        scenarios = screen_subset({n_scenarios})
        gc.collect()
        rss0 = current_rss_mb()
        t0 = time.perf_counter()
        if {mode!r} == "legacy":
            r = replay_fixture_legacy(windows, scenarios, retain_waterfalls=True)
            n_wf = _waterfall_count()
            held = r["waterfall_count"]
            n_art = 0
        else:
            dest = Path("/tmp/er_iso_stream")
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True)
            r = replay_fixture_streaming(
                windows, scenarios, run_dir=dest,
                dataset_fingerprint="iso", tape_fingerprint="iso",
            )
            n_wf = _waterfall_count()
            held = 0
            n_art = len(r.get("windows_replayed") or [])
        dt = time.perf_counter() - t0
        rss1 = current_rss_mb()
        n_replays = {n_signals} * {n_scenarios}
        print(json.dumps({{
            "mode": {mode!r},
            "n_signals": {n_signals},
            "n_scenarios": {n_scenarios},
            "n_windows": {n_windows},
            "n_replays": n_replays,
            "seconds": round(dt, 4),
            "rss0_mb": round(rss0, 2),
            "rss1_mb": round(rss1, 2),
            "rss_delta_mb": round(rss1 - rss0, 2),
            "peak_rss_mb": round(peak_rss_mb(), 2),
            "waterfall_objects": n_wf,
            "retained_count": held,
            "artifacts": n_art,
            "signals_per_sec": round(n_replays / dt, 1) if dt > 0 else 0,
            "artifacts_per_sec": round(n_art / dt, 2) if dt > 0 and n_art else 0,
        }}))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[3]),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main() -> None:
    p = argparse.ArgumentParser(description="Execution realism streaming vs legacy benchmark")
    p.add_argument("--signals", type=int, default=400)
    p.add_argument("--scenarios", type=int, default=8)
    p.add_argument("--windows", type=int, default=4)
    p.add_argument(
        "--isolated",
        action="store_true",
        help="Run legacy and streaming in separate processes for fair peak RSS",
    )
    args = p.parse_args()
    if args.isolated:
        legacy = run_isolated_mode(
            "legacy", n_signals=args.signals, n_scenarios=args.scenarios, n_windows=args.windows
        )
        streaming = run_isolated_mode(
            "streaming", n_signals=args.signals, n_scenarios=args.scenarios, n_windows=args.windows
        )
        print("legacy", json.dumps(legacy, sort_keys=True))
        print("streaming", json.dumps(streaming, sort_keys=True))
        return
    result = run_benchmark(n_signals=args.signals, n_scenarios=args.scenarios, n_windows=args.windows)
    for k, v in result.items():
        print(f"{k}={v}")


if __name__ == "__main__":
    main()

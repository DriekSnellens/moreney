# Execution realism streaming report

Research infrastructure only. Does not claim live alpha. Does not enable
production execution. Does not change fills, fees, adverse, H-0005, H-0007,
or published research conclusions.

---

## 1. Original memory failure

The first execution-realism lab evaluated a stage-1 screen of **80 scenarios**
against the parent-signal tape. A live screen run retained:

```
~80 scenarios × ~36,000 signals × ExecutionWaterfall
  (+ nested ExecutionTimeline, FillResult, HedgeResult)
```

Peak RSS reached **~4.3 GB**. The process was killed before a complete
artifact was written. `--max-events 2000000` produced **0 signals** because
it cut the tape off before the OOS region; the full tape generated signals
and then OOM'd during scenario accumulation.

Production execution was not involved. This was a research-runner failure.

## 2. Exact root cause

`bot/research/execution_realism/engine.py` (pre-streaming) retained every
waterfall:

```python
all_waterfalls: dict[str, list[ExecutionWaterfall]] = {s["scenario_id"]: [] for s in scenarios}
...
all_waterfalls[scen["scenario_id"]].append(wf)
```

After the full nested loop it scanned those lists to build scenario totals.
That is **O(signals × scenarios)** live Pydantic-free but still heavy Python
objects, not O(window) working state.

Measured object size on the streaming benchmark (isolated process,
4000 signals × 40 scenarios = 160,000 waterfalls):

| | |
|---|---|
| Legacy RSS delta | **246.84 MB** |
| Retained waterfalls | **160,000** |
| Bytes per waterfall (RSS delta / count) | **~1.54 KB** |

Extrapolation to the failed live run:

```
80 × 36,000 = 2,880,000 waterfalls
× 1.54 KB  ≈ 4.43 GB
```

This matches the observed **~4.3 GB** peak. The tape index (~935k stride-4
points) is a separate, already-accepted cost. The new OOM was waterfall
retention, not the tape.

## 3. Old retention model

```
load full tape index
for window in windows:
    materialize window points
    for signal in window:
        slice horizon points
        for scenario in scenarios:          # ~80
            wf = simulate_signal(...)
            all_waterfalls[scenario].append(wf)   # RETAINED
for scenario:
    scan all_waterfalls[scenario]             # still in RAM
    emit totals
```

Memory complexity:

```
O(tape_index + windows × signals × scenarios × sizeof(ExecutionWaterfall))
```

At no point were objects released until the process exited.

## 4. New streaming architecture

```
signals
    ↓
window iterator
    ↓
scenario iterator
    ↓
streaming execution replay          # one ExecutionWaterfall at a time
    ↓
incremental ExecutionAccumulator    # counts + Decimal sums + streaming drawdown
    ↓
per-window artifact written to disk # atomic tmp + fsync + rename
    ↓
object references released
    ↓
final reducer combines summaries
```

Hot loop (`streaming_replay_window`):

```
for signal in window:
    wf = simulate_signal(...)
    accumulator.observe(wf)
    nets.append(str(wf.execution_net))   # compact primitive, not the object
    del wf
```

`ExecutionWaterfall` is ephemeral. Accumulators are mergeable for **sums**.
Global max drawdown is reconstructed by the reducer from the compact
`execution_nets` list in window order (independent window max-drawdowns
are **not** min()'d — that would be wrong).

Default CLI remains sequential. `--workers` is accepted and ignored with a
warning; this lab does not spawn 80 scenario processes.

## 5. Memory complexity before / after

| | Before | After |
|---|---|---|
| Live waterfalls | O(signals × scenarios) | O(1) ephemeral |
| Accumulators | none (full lists) | O(1) per in-flight scenario |
| Window working set | window points + all prior waterfalls | window signals + current acc + compact nets |
| Disk | one JSON at the end | one compact artifact per window×scenario |

Conceptually:

```
before: O(signals × scenarios × waterfall object size)
after:  O(window size + accumulator state + bounded diagnostics)
        + O(tape_index) which was already required
```

Measured isolated-process RSS (fresh Python each mode):

| Fixture | Replays | Legacy ΔRSS | Stream ΔRSS | Legacy objects | Stream objects |
|---|---:|---:|---:|---:|---:|
| 800 × 12 × 4 | 9,600 | 22.69 MB | 0.90 MB | 9,600 | 0 |
| 2,000 × 20 × 5 | 40,000 | 90.86 MB | 1.09 MB | 40,000 | 0 |
| 4,000 × 40 × 5 | 160,000 | 246.84 MB | 1.42 MB | 160,000 | 0 |

Streaming RSS delta stays ≈ **1 MB** while legacy grows with replays.
A 4× increase in replays (40k → 160k) increased streaming ΔRSS by **0.33 MB**
and legacy ΔRSS by **156 MB**.

## 6. Artifact layout

Schema version: `execution_realism_streaming_v1`
(`ARTIFACT_SCHEMA_VERSION`). Independent of research
`PROTOCOL_VERSION = execution_realism_v1`. Old in-memory JSON blobs are not
reinterpreted as streaming artifacts.

```
data/research/execution_realism/runs/<run_id>/
    manifest.json
    config.json
    windows/<window_id>/scenario_<safe_id>.json
    summaries/scenario_<safe_id>.json
```

Each window×scenario artifact contains:

- `schema_version`
- `git_commit` (if available)
- `dataset_fingerprint`
- `tape_fingerprint` (dataset_id + content fingerprint + stride + min_ts)
- `window_id`, `scenario_id`
- `scenario_config_hash` (scenario + protocol hash + schema)
- `signal_count`, `fill_count`
- waterfall sums (gross, fees, slippage, adverse, inventory, latency, hedge)
- `canonical_replay_net` / `parent_canonical_net_sum`
- `expected_net` (`signal_expected_net` sidecar; currently zero in the simulator)
- `execution_net`
- `max_drawdown` (window-local in the window file; global in the summary)
- `accounting_identity_status`
- `deterministic_fingerprint`
- `execution_nets`: compact list of per-signal execution NET strings for
  exact sequential drawdown reconstruction

Writes are **atomic**: tempfile in the same directory → `flush` + `fsync` →
`os.replace`. A killed process cannot leave a valid-looking corrupt JSON
at the final path. Sibling `*.tmp` files are never treated as artifacts.

Dashboard still reads `data/research/execution_realism_results.json` after
the reducer finishes. That path is unchanged.

## 7. Resume behavior

```
python -m bot.research.execution_realism.runner --run-id <id> --resume
```

Skip rule (all must match):

- `schema_version == execution_realism_streaming_v1`
- `dataset_fingerprint` equals the current tape
- `scenario_config_hash` equals the current scenario + protocol
- stored `deterministic_fingerprint` recomputes identically

Otherwise the window×scenario cell is recomputed.

Never mixes artifacts from a different dataset, stride, protocol, or
scenario configuration. Truncated JSON, wrong fingerprint, wrong scenario
hash, and leftover temp files all force recomputation.

Final results are always produced by the **reducer** from disk, so a resumed
run and a clean run share one code path.

## 8. Determinism guarantees

- Same tape + same config + same window/scenario order → identical
  `deterministic_fingerprint` (SHA-256 of additive stats + max drawdown).
- Scenario specs are dicts copied per cell; a scenario cannot mutate
  another scenario, the parent strategy, the tape, or cached decisions.
- Shared window `points` slices are read-only (`SeriesPoint` is frozen).
- Fill / fee / adverse / latency tables are unchanged.
- Canonical labels remain explicit: parent canonical replay NET is not
  mixed with execution sidecar NET or expected-net.

Quantiles (p50/p95/p99) are **not** computed by this lab today. No
approximate sketch was introduced. If exact distribution statistics are
required later, write sorted chunks to disk and merge; do not retain the
full sample in RAM.

## 9. Benchmark results

Command (isolated processes):

```
python -c 'from bot.research.execution_realism.benchmark import run_isolated_mode; ...'
```

or:

```
python -m bot.research.execution_realism.benchmark --isolated --signals 4000 --scenarios 40 --windows 5
```

Largest isolated fixture (4000 signals × 40 scenarios × 5 windows = 160,000 replays):

| Metric | Legacy (retain waterfalls) | Streaming |
|---|---:|---:|
| Wall clock | 5.5643 s | 6.3714 s |
| RSS before | 105.35 MB | 105.31 MB |
| RSS after | 352.19 MB | 106.73 MB |
| RSS delta | **246.84 MB** | **1.42 MB** |
| Peak RSS | 363.82 MB | 106.27 MB |
| Waterfall objects live | 160,000 | **0** |
| Signals/sec (replays) | 28,754.9 | 25,112.1 |
| Artifacts written | 0 | 200 |
| Artifacts/sec | n/a | 31.4 |

Streaming is **not** faster. It is **memory-bounded**. The extra wall time
is artifact JSON write + reduce, not a weaker realism calculation.
`simulate_signal` / fill / hedge / fee paths are unchanged.

In-process (same PID, legacy then streaming) peak RSS is not a fair
comparison because kernel `ru_maxrss` is monotonic. Use `--isolated`.

## 10. Exact correctness comparison against legacy

Frozen synthetic fixture, 24 signals × 3 windows × 4 scenarios:

Legacy in-memory result **equals** streaming+reducer for:

- signal count
- fill count / partial / no-fill
- gross, fees, slippage, adverse, inventory
- canonical replay NET
- expected NET
- execution NET
- max drawdown
- outcome counts
- deterministic fingerprint
- accounting identity status

Equality is **exact** (stringified Decimal), not approximate.

Streaming max drawdown equals the legacy full equity-curve scan, including
the identity `max_dd([10, -5, -20, 8]) = -25` with start equity 0 as a peak.

Per-signal waterfall identity is unchanged:

```
gross − maker − taker − slippage − latency − queue − partial − adverse − hedge − inventory
= execution_net
```

NO_FILL ⇒ execution_net = 0.

Parent identity is unchanged: canonical replay NET is the parent
`event["net"]` sum (same value on every scenario result), not a fill-rate
overlay.

Accumulator merges use Decimal precision 80 so grouped window sums match a
single pass. Default Decimal precision (28) would disagree at the last ULP
when reducing window artifacts; that is a summation grouping issue, not a
change in economics.

## 11. Known limitations

- The **tape index** is still fully in memory. Stride=1 on the 58G tape
  remains an OOM risk (~2.9 GB historically). Streaming does not shrink
  the tape. Use stride=4 as before.
- One research window's signals + horizon point slices are materialized
  before scenarios run. Memory is O(window), not O(full tape × scenarios).
- `execution_nets` on disk is O(window signals) strings per cell. Fine for
  thousands of signals; not a substitute for a columnar format if windows
  become millions of signals.
- No p50/p95/p99 in this lab; none were silently approximated.
- Parallelism is **not** enabled. `--workers != 1` logs a warning.
- `signal_expected_net` on the waterfall is still zero (simulator never
  set it). The accumulator records it; it is not a new economic claim.
- Dashboard markdown report `docs/EXECUTION_REALISM_REPORT.md` is still
  overwritten only when the full tape runner completes with
  `write_markdown_report=True`. This document is separate.

## 12. Recommended safe scenario / window scale

Safe on a machine with ~2 GB free **after** the stride-4 tape index:

- Scenarios: **full stage-1 screen (80)** or `full_matrix` (240)
- Windows: all complete sequential windows on the current tape
- Signals: tens of thousands per window as long as **one window** fits

The previous hard fail was 80 × 36k waterfalls ≈ 4.3 GB. Streaming holds
**zero** of those objects. Remaining RAM budget is:

```
tape_index (stride 4)
+ largest window's WindowSignal list
+ one accumulator
+ compact nets for the current window
+ JSON writer buffers
```

Do not drop stride below 4 to “use the extra RAM” without a separate tape
streaming project.

Do not enable production execution. Do not retune H-0005 or H-0007.

---

## Acceptance

| Criterion | Status |
|---|---|
| Results stream instead of retaining all waterfalls | PASS (0 live waterfalls after streaming 160k replays) |
| Per-window/scenario artifacts persisted incrementally | PASS |
| Resume | PASS (tests) |
| Corrupt artifacts detected | PASS (truncated / wrong fingerprint / wrong hash / wrong dataset / tmp) |
| Reducer reconstructs final results | PASS |
| Canonical accounting identities exact | PASS (existing accounting tests + per-waterfall audit) |
| Streaming = legacy on frozen fixture | PASS (exact) |
| Max drawdown matches legacy exactly | PASS |
| Scenario worlds isolated | PASS |
| Deterministic fingerprints stable | PASS |
| Existing research tests still pass | PASS (65 tests: realism + streaming + accounting + attribution) |
| Production trading logic unchanged | PASS (no executor/risk/strategy edits; `EXECUTION_REALISM_PRODUCTION_ENABLED is False`) |
| Production execution disabled | PASS |
| Memory benchmark bounded | PASS (ΔRSS 1.42 MB vs 246.84 MB at 160k replays) |
| Report written | PASS |
| No alpha claims changed | PASS |

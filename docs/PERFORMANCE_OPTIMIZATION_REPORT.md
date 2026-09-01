# Performance optimization report

> **Superseding profile (post-Redis):** see [`POST_REDIS_PROFILE_REPORT.md`](./POST_REDIS_PROFILE_REPORT.md).  
> New primary bottleneck: **strategy_scan / candidate_creation**. Do not redesign Redis hydrate unless it regresses.

**Constraint:** same inputs → same decisions, fills, and realized NET. No trading-logic changes.

**Enable live histograms:** `PERF_INSTRUMENTATION_ENABLED=true` (optional `PERF_INSTRUMENTATION_WINDOW=512`).

## Phase 1 — Execution map (measured)

```
Publisher WS events
  → LocalOrderBook apply
  → Redis SET book/tick/health (throttled ~250ms / exchange)
Shared paper bots (×5 processes)
  → hydrate_from_redis every ~100ms
  → PaperRunner._run_cycle (~1s)
       → collect books
       → HMM mid observe
       → match/expire resting quotes
       → strategy scan → GOE (NET → EV → route belief → risk → rank)
       → paper executor
Research
  → causal_walkforward.walk_forward (no FastAPI/Redis/WS)
```

### Highest-frequency / costliest (before)

| Path | Finding |
|------|---------|
| `hydrate_from_redis` | **46 sequential Redis GETs** per poll (4 exch × 5 sym × book+tick + health + funding + equity). At 1ms RTT ≈ **46ms**; ×5 bots ≈ **230ms** fleet network wait every 100ms. |
| JSON decode | Full `OrderBook.model_validate` every poll even when nonce/ts unchanged. |
| Paper cycle | `_collect_books()` called twice (HMM + match) per cycle. |
| GOE | Per-opportunity NET/EV/risk (necessary; not shared across processes). |
| Pandas | HMM candle path only — not on every book update. |

### Ranked plan (impact × frequency × safety)

1. **Redis pipeline hydrate** — done  
2. **Skip decode on identical payloads** — done  
3. **Publisher pipeline SET** — done  
4. **One books snapshot per cycle** — done  
5. **Configurable latency histograms** — done  
6. Cross-process shared candidate gen — **not done** (bots are separate processes; would need a shared decision service — high risk / out of scope)  
7. Incremental route-stat rebuilds — **not needed yet** (not on hydrate hot path; causal replay already O(events))

## Phase 2 — Instrumentation

`bot/perf/cycle_metrics.py` — ring buffer, mean / p50 / p95 / p99 / count.

Phases recorded when enabled:

- `redis_read`, `hydrate_parse`, `hydrate_total` (market-data consumer)
- `collect_books`, `hmm_regime`, `match_expire`, `strategy_scan`, `ingest_cycle`, `total_cycle` (paper runner)
- `net_calc`, `route_belief`, `ev_calc`, `risk_cap`, `ranking` (GOE batch)

Exposed on paper `status()["latency"]` and `last_cycle.latency`. No per-event logging.

## Phases 3–5 — What changed

| Optimization | Behavior impact |
|--------------|-----------------|
| Pipeline GET hydrate | Same keys, same values, one RTT |
| Identical-payload skip | Local books unchanged iff Redis bytes unchanged (still latest publisher snapshot) |
| Pipeline SET on publisher flush | Same keys written together |
| Shared books in HMM + match | Same dict contents; one collection |
| `slots` latency stats | Instrumentation only |

**Not shared across bots:** cash, inventory, risk, sizing, caps (correct).

## Phase 7 — Event coalescing (documented)

| Layer | What may coalesce | Why safe |
|-------|-------------------|----------|
| Publisher `market_data_cache_interval_ms` (default 250) | Intermediate WS book deltas between Redis publishes | Consumers never traded on those unpublished intermediates |
| Consumer Redis poll (~100ms) | Intermediate publisher writes between polls | Hydrate always takes **latest** key values |
| Paper cycle books snapshot | Multiple WS updates within one cycle | Match/scan use one coherent snapshot at cycle start |

**Must not coalesce:** resting quote match events that depend on trade-through / book evolution within a cycle when fills are simulated against the current book — we keep match on the cycle snapshot (unchanged semantics). Causal replay does not drop events.

## Phase 8 — Async / CPU

- Independent Redis GETs → **one pipeline** (primary win).  
- No process pool added (hydrate was I/O bound; GOE remains sequential for determinism).  
- Blocking CPU on event loop unchanged for HMM refit (already wall-clock gated ~hours).

## Phase 9 — Research runner

`causal_walkforward` remains a direct chronological path (no FastAPI/Redis/WS). Runtime + events/sec attached to report output.

## Phase 10 — Correctness

```
pytest tests/test_hydrate_pipeline.py tests/test_perf_regression.py \
       tests/test_shared_market_data.py tests/test_causal_walkforward_leakage.py
```

Causal fingerprints for configs A–D on frozen `data/paper_25000live.json` trades are deterministic across re-runs. Trading thresholds / fees / fills / EV formulas untouched.

## Phase 11 — Before / after

From `scripts/bench_perf.py` → `data/perf_benchmark.json` (1ms simulated Redis RTT):

| Path | Baseline | Optimized | Speedup | Identical outputs |
|------|----------|-----------|---------|-------------------|
| Hydrate (4×5 books) | ~46ms / 46 RTTs | ~2.3ms / **1 RTT** cold; ~1.2ms warm | **~20×** | Yes (same books) |
| Causal WF (3200 events) | — | ~29k events/s | research path | Deterministic NET |

Fleet implication: 5 bots × hydrate poll moves from ~230ms aggregate sequential wait toward ~5×1ms pipeline waits (plus parse), without changing freshness semantics.

### Intentionally deferred

- Shared cross-bot candidate generation (multi-process).  
- Replacing Pydantic `OrderBook` on the API boundary.  
- Incremental DataFrame stats (not on measured live hot path).  
- Process-pool strategy evaluation (determinism risk; not I/O-bound after Redis fix).

## How to operate

```bash
# histograms
PERF_INSTRUMENTATION_ENABLED=true

# bench
.venv/bin/python scripts/bench_perf.py

# causal (no Redis)
.venv/bin/python -m bot.opportunity.causal_walkforward data/paper_25000live.json
```

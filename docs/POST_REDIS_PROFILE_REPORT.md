# Post-Redis performance profile

**Date:** 2026-08-15  
**Principle:** The sequential Redis GET bottleneck is gone. This report measures the system *after* that change and does **not** redesign hydrate.

**Harness:** `.venv/bin/python scripts/profile_post_redis.py` → `data/post_redis_profile.json`  
**Instrumentation:** `PERF_INSTRUMENTATION_ENABLED=true`

---

## A. New performance profile (active market, ranked by total cost)

End-to-end loop = `hydrate` + `paper_cycle` (30 cycles, 4×5 books, 0.2 ms simulated Redis RTT).

| Rank | Stage | Mean ms | % of e2e |
|------|-------|---------|----------|
| 1 | **paper_cycle** | 5.10 | **44.4%** |
| 2 | hydrate (all) | 3.31 | 28.8% |
| 3 | paper::strategy_scan | 2.03 | 17.7% |
| 4 | hydrate_parse (JSON→OrderBook) | 2.02 | 17.6% |
| 5 | redis_read (1 pipeline RTT) | 1.26 | 11.0% |
| 6 | paper::candidate_creation | 1.21 | 10.6% |
| 7 | paper::collect_books | 0.82 | 7.2% |
| 8 | hmm_regime | 0.32 | 2.8% |
| — | match/expire, ingest | ~0.24 each | ~5% |
| — | GOE net/ev/risk/rank/executor | ~0* | — |

\*GOE sub-phases recorded 0 because this fixture’s maker scan produced no approved candidates to score — scan/candidate creation still ran fully.

**Inside paper_cycle only (active):**

1. `strategy_scan` 46%  
2. `candidate_creation` 28%  
3. `collect_books` 19%  
4. HMM / match / ingest — single-digit %

**Warm / quiet (payloads unchanged):**

| Stage | Mean ms | % of e2e |
|-------|---------|----------|
| paper_cycle | 4.24 | 75% |
| hydrate | 1.37 | 24% (almost all `redis_read`) |
| hydrate_parse | ≈0 | skipped |
| strategy_scan | 1.93 | 34% of e2e |

Mean e2e: **warm 5.6 ms** vs **active 11.5 ms**.

---

## B. Before/after Redis impact

| | Sequential GETs (old) | Pipelined (current) |
|--|----------------------|---------------------|
| Cold hydrate | ~46 ms @ 1 ms RTT / 46 RTTs | ~2.3 ms / **1 RTT** |
| Warm hydrate | same GETs + full decode | ~1.2 ms; **byte-compare skip** |
| Active hydrate | N/A | ~3.3 ms (parse dominates over RTT) |

Identical-payload audit:

- Byte compare **~126× cheaper** than JSON decode + `OrderBook.model_validate` (5000 iters).  
- Book / tick / health / funding / equity invalidate **independently** (tests).  
- Warm poll: **95.7%** keys unchanged, **0** book/tick changes after first apply.  
- Active poll: **87%** keys changed (forced).

---

## C. New primary bottleneck

**Primary (active markets, e2e wall-clock): `paper_cycle`, specifically `strategy_scan` → `candidate_creation`.**

Rationale:

- Largest share of active e2e (44% paper_cycle; strategy_scan is its dominant child).  
- Remains #1 on the warm path as well (hydrate becomes cheap when payloads are unchanged).  
- Hydrate is #2 on active only because **parse/rebuild** after changed bytes — not because of sequential Redis RTTs. That path is left alone per freeze rule.

Not selected as primary: further Redis hydrate redesign.

---

## D. Scaling analysis

Parallel bot processes (critical-path = max per-bot wall; aggregate CPU separate):

| Fleet | Mode | Critical-path s | Aggregate CPU s | Redis ops/s | ≈RSS aggregate |
|------|------|-----------------|-----------------|-------------|----------------|
| 1 | active | 0.19 | 0.17 | ~1.0k | ~123 MB |
| 5 | active | 0.20 | 0.65 | ~2.9k | ~616 MB |
| 10 | active | **0.58** | 1.23 | ~2.4k | ~1.2 GB |
| 25 | active | **0.99** | 2.71 | ~1.9k | ~3.1 GB |
| 5 | warm | 0.12 | 0.29 | ~1.4k | ~616 MB |

**First scaling bottleneck:** host **CPU + memory** under many concurrent bots (critical-path stretches after ~5–10 processes). Redis RTT is no longer the limiter.

**Duplicated market-side CPU (quantified, not implemented):**

```
pure_shared ≈ hydrate 3.3 ms/bot/cycle
× (5−1) = 13.2 ms fleet savings potential
≈ 23% of fleet e2e CPU at 5 bots
```

Recommendation: **defer** a shared hydrate/candidate service until this fraction grows or fleet ≫ 5; portfolio/risk/sizing must stay per-bot.

---

## E. Implemented this pass (measurement only)

No trading-logic or fill/fee/threshold changes.

1. Cycle metrics: `total_ms`, `pct_of_cycle`, ranked phases  
2. TradingEngine spans: `candidate_creation`, `goe_evaluate`, `executor`  
3. Polling efficiency counters on `MarketDataCache`  
4. Post-Redis harness: warm/active/fleet/replay (`bot/perf/post_redis_bench.py`)  
5. Identical-payload component tests (`tests/test_identical_payload_cache.py`)

---

## F. Deferred (measured impact too small or too risky)

| Idea | Why deferred |
|------|----------------|
| Shared cross-process candidate generation | ~23% theoretical fleet CPU; high complexity; sizing still per-bot |
| Redis pub/sub / version counters | Warm polls are 96% no-ops, but cycle work ≪ `paper_cycle_interval` sleep — not the dominant wall-clock cost yet |
| Micro-optimize causal walk-forward | ~16k events/s already; not a practical bottleneck |
| Further Redis hydrate redesign | Explicitly frozen unless regression |
| Aggressive candidate-scan micro-opts | Touches trading path; needs separate correctness budget |

Polling note: `useful_key_updates_ratio` ≈ **0** on warm. Architecture change is **proposed only**, not implemented.

---

## G. Correctness evidence

```text
pytest tests/test_identical_payload_cache.py \
       tests/test_perf_regression.py \
       tests/test_hydrate_pipeline.py \
       tests/test_causal_walkforward_leakage.py
→ 21 passed
```

Causal A/B/C/D fingerprints remain deterministic on frozen `data/paper_25000live.json` trades. Payload skip tests cover independent book/tick/health/funding invalidation and “change cannot be missed.”

---

## Replay

~15.7k events/s on scaled paper trades. **No further replay optimization.**

---

## Next optimization target (if continuing)

**Done (follow-on):** see [`CANDIDATE_HOTPATH_REPORT.md`](./CANDIDATE_HOTPATH_REPORT.md) — strategy_scan / candidate_creation optimized under GOE-emitting fixtures with frozen trading behavior.

Keep hydrate pipeline as-is. Consider shared decode **only** if fleet size or active parse cost grows past the ~23% savings bar.

### cProfile notes (active)

Hot `tottime` symbols include Pydantic `validate_python`, `maker_inventory._candidate_from_quote`, `LocalOrderBook.to_order_book`, and hydrate apply — consistent with strategy_scan + active parse. Bench harness `_seed_books` also appears in profiles (test-only publisher simulation; not production path).

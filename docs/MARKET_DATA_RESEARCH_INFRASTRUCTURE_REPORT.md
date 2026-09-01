# Market-Data Research Infrastructure Report

**Label:** RESEARCH INFRASTRUCTURE (not a trading strategy)  
**Final verdict:** **DATA_NOT_READY**

No synchronized research tape has been collected yet on disk. Infrastructure is in place; readiness requires recorded dual-timestamp events from the publisher.

---

## A. Current problem

Lead-lag research returned `INSUFFICIENT_DATA`: missing synchronized tape, Bitvavo treated local wall clock as exchange time, Redis hydrate overwrote `received_at` with poll time.

## B. Existing timestamp architecture

```
Exchange WS → adapter parse → MarketDataService.handle_event
  → LocalOrderBook → (throttled) Redis latest snapshot → shared hydrate
```

Research recorder now enqueues on the publisher **before** Redis publish.

## C. Venue-specific capabilities

| Venue | Exchange ts | Sequence | Quality |
|---|---|---|---|
| Binance | Yes (depth `E`) | Yes | MEDIUM |
| OKX | Yes (`ts`) | Yes | MEDIUM |
| Bitvavo | **No** | Yes (nonce) | **UNSUPPORTED** |

Bitvavo now records `exchange_ts_ns=null` with `timestamp_quality=UNSUPPORTED` — **no invented clocks**.

## D. Schema

`research_md_v1` / `ResearchMarketEvent`:

- dual clocks: `exchange_ts_ns` (nullable), `received_ts_ns`, `local_monotonic_ns`
- L1 + up to L10 depth levels
- sequence, quality flags, crossed/locked/stale

Format: JSONL partitioned `date/venue/symbol` (no pyarrow dependency).

## E. Recording pipeline

- Flag: `RESEARCH_MARKETDATA_RECORDING_ENABLED` (default true)
- Path: `./data/research_marketdata`
- Async buffered thread; hot path only enqueues
- Drops / queue depth / completeness exposed

## F. Redis integration

Redis remains transport. Book metadata now carries:

`received_at`, `exchange_ts_available`, `timestamp_quality`, `exchange_ts`

Hydrate preserves publisher `received_at` (does not pretend poll time is exchange_ts).

## G. Replay architecture

`MarketDataReplayEngine`: event-by-event, `until_ns`, `visible_at` (causal).  
Same dataset → same fingerprint.

## H. Synchronization

Latest-valid per venue with exchange clocks within tolerance; receive-only fallback marked UNSUPPORTED.

## I–J. Data quality / horizons

Without recordings: all `LEAD_LAG_*` → **NOT_READY**.  
50ms rejected when timestamp uncertainty > horizon.

## K. Performance impact

Enqueue-only on trading path; disk I/O off-thread. Incomplete data never silently claimed complete.

## L. Failure modes

Missing exchange_ts → null; queue overflow → `dropped` + `complete=false`; sequence gaps diagnosed.

## M. Reproducibility

Session manifests include schema, git commit, coverage, ordering, latency.

## N. Tests

See `tests/test_market_data_research_infra.py` (+ existing causal/toxicity suites).

## O. Exact next step for lead/lag

1. Run `moreney-marketdata` publisher with research recording enabled.  
2. Collect multi-hour tape for binance/bitvavo/okx on maker symbols.  
3. Re-run:

```bash
PYTHONPATH=. python -m bot.market_data.research.runner
```

4. Only if verdict becomes `DATA_READY_FOR_LEAD_LAG` or partial with caution on ≥500ms — return to lead-lag discovery.  
5. Do **not** optimize alpha, enable lead-lag execution, or change maker economics now.

---

## Regenerate

```bash
PYTHONPATH=. python -m bot.market_data.research.runner \
  --path data/research_marketdata \
  --out data/market_data_research_report.json
```

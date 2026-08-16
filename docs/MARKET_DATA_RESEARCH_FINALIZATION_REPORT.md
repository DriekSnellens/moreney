# Market-data research pipeline — finalization report

**Branch:** `cursor/market-data-finalize-3d96`  
**Label:** RESEARCH_TAPE_ACCEPTANCE (not alpha, not trading optimization)

## Prompt improvements applied

The original prompt was applied with these operational corrections:

1. **Default recording** — Keep `RESEARCH_MARKETDATA_RECORDING_ENABLED=true` on this deployment so the live publisher tape is not interrupted. Document `false` as the safe greenfield default in `.env.example` comments; do not flip production mid-collection.
2. **Dual layout** — New writes use `date=/venue=/symbol=/session=/events.jsonl`; scanners still read the legacy `YYYYMMDD/<venue>/<SYMBOL>.jsonl` tape already on disk.
3. **Streaming inventory** — Acceptance uses streaming scan + bounded sync sample; do not require loading ~1M events into RAM for CLI.
4. **Separate runtime vs acceptance** — Dashboard exposes `CURRENT_STATE` / recorder metrics separately from `FINAL_ACCEPTANCE_VERDICT`.
5. **No synthetic proof** — Runtime evidence comes from live `data/research_marketdata/` only.
6. **Bitvavo clocks** — `exchange_ts_ns=null` is correct; never invent; fast horizons stay NOT_READY.
7. **No trading changes** — Fees, fills, NET, toxicity live-blocking, lead-lag execution remain untouched.

---

## A. Architecture audit

```
EXCHANGE EVENT
  -> venue adapter (Binance / Bitvavo / OKX / …)
  -> MarketDataEvent (+ exchange_ts metadata; Bitvavo null)
  -> MarketDataService.handle_event
  -> ResearchMarketDataRecorder.enqueue_live   [non-blocking]
  -> queue -> async drain -> JSONL tape
  -> tape_scan / integrity / manifest
  -> PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA
  -> horizon readiness + cross-venue matrix
  -> chronological DEV / FREEZE / OOS
  -> dashboard RESEARCH DATA STATUS + CLI runner
```

**Connected:** publisher `handle_event` → research recorder (before Redis).  
**Previously weak:** acceptance CLI, session boundaries, operational state vocabulary, dashboard status panel.  
**Still legacy on disk until publisher restart:** live PID still writing legacy paths; new code writes session layout.

## B. Effective runtime configuration

| Key | Value |
|-----|-------|
| RESEARCH_MARKETDATA_RECORDING_ENABLED | true |
| RESEARCH_MARKETDATA_OUTPUT_DIR | ./data/research_marketdata |
| RESEARCH_MARKETDATA_QUEUE_SIZE | 50000 |
| RESEARCH_MARKETDATA_FLUSH_INTERVAL_MS | 50 |
| RESEARCH_MARKETDATA_FLUSH_EVERY | 64 |
| RESEARCH_MARKETDATA_DEPTH_LEVELS | 10 |

Observable via `MarketDataService.research_recorder_status()` / recorder `snapshot()`.

## C. Real event recording evidence

- Live process: `python -m bot.market_data.publisher` (PID observed during audit).
- Tape root: `data/research_marketdata/` (legacy partitions under `20260816/`).
- Acceptance run (CLI): **~1.07M events**, **~5.23 h** duration, **0** recorder drops / write errors in offline report inputs.
- Venues present: binance, bitvavo, okx, kraken, coinbase.
- Core research venues all above volume thresholds.

## D. End-to-end event traces (real tape)

| Venue | symbol | exchange_ts_ns | received_ts_ns | local_monotonic_ns | sequence |
|-------|--------|----------------|----------------|--------------------|----------|
| binance | ADAEUR | 1786896959642793984 | 1786896959642793984 | 370088704305820 | 1744865241 |
| bitvavo | ADAEUR | **null** | 1786896939903857920 | 370068965404588 | 283886437 |
| okx | ADAEUR | 1786896938704000000 | 1786896938780084992 | 370067841623494 | 6886822048 |

Bitvavo correctly records absence of exchange clock (`exchange_ts_available=false`).

## E. Recorder health

- Path connected on publisher hot path.
- Drops exposed (counter + dashboard).
- Write errors exposed.
- Restart ⇒ new `session_id` (session layout); no silent append across unknown restarts for new sessions.

## F. Tape files produced

- ~83+ JSONL files under legacy layout (growing while publisher runs).
- Hundreds of MB of real exchange traffic.
- Format: JSONL research schema `research_md_v1`.

## G. Dataset manifest

- **DATASET_ID:** `mdresearch-research_md_v1-c88eeba170a56f89`
- Content fingerprint derived from file checksums + event count (stable for identical tape).
- Artifact: `data/research_marketdata_manifest.json`
- Compact dashboard report: `data/market_data_research_report.json`

## H. Data integrity results

- Streaming validator tracks malformed JSON, missing fields, duplicates, regressions, sequence gaps, L1/depth, crossed/locked, Bitvavo invented-ts.
- Sample (50k): 0 malformed, 0 invalid schema; sequence gaps are common on multi-channel books (diagnostic; not used to fabricate sync).

## I. Timestamp quality

| Venue | exchange_ts % | received_ts % | monotonic % | sequence % |
|-------|---------------|---------------|-------------|------------|
| binance | ~76% | 100% | 100% | 100% |
| bitvavo | **0%** (correct) | 100% | 100% | ~72% |
| okx | 100% | 100% | 100% | 100% |

## J. Cross-venue synchronization

- Exchange-clock triad sync usable_rate ≈ **0** while Bitvavo lacks exchange_ts (not fabricated).
- Directed routes involving Bitvavo: fast horizons **NOT_READY**; slow labeled **READY_WITH_CAUTION** (receive-clock / uncertainty explicit).
- `binance->okx` / `okx->binance` can support stronger sync when sampled on exchange clocks alone; core triad remains Bitvavo-limited.

## K. Horizon readiness

| Horizon | Verdict |
|---------|---------|
| 50ms | NOT_READY |
| 100ms | NOT_READY |
| 250ms | NOT_READY |
| 500ms | READY_WITH_CAUTION |
| 1000ms | READY_WITH_CAUTION |
| 2000ms | READY_WITH_CAUTION |
| 5000ms | READY_WITH_CAUTION |

## L. DEV / FREEZE / OOS boundaries

Chronological split (no shuffle, zero overlap) stored conceptually with exact ns boundaries from tape span (see CLI / manifest consumers via `chrono_split`). OOS remains untouched by fitting/tuning.

## M. Regression verification

- Paper executor / maker inventory / economics: no research acceptance imports.
- `LEAD_LAG_EXECUTION_ENABLED=false`, toxicity remains shadow.
- Fees / fill baseline / Live-equivalent PnL math not modified.

## N. Exact remaining blockers

1. **FIRST_BLOCKER (fast horizons):** Bitvavo has no exchange timestamp → cannot support causal 50–250ms cross-venue readiness for routes including Bitvavo.
2. Live publisher must be **restarted** to emit new **session=** partitions (legacy tape remains valid and readable).
3. Exchange-clock sync usable_rate for the full triad stays ~0 until a Bitvavo clock exists or research scopes to binance↔okx only.
4. Do not enable lead-lag execution or claim alpha.

## O. Final operational verdict

**DATA_READY_FOR_SLOW_HORIZONS**

Real tape exists, was read back, has a deterministic manifest, and the acceptance runner processed it. Fast horizons remain NOT_READY.

### CLI

```bash
python -m bot.market_data.research.runner
```

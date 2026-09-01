# Lead-Lag Hedged Dislocation — Repository Audit & Implementation Plan

**Status:** PLAN ONLY — no strategy implementation in this commit.  
**Strategy name (planned):** `lead_lag_hedged_dislocation`  
**Mode (planned default):** RESEARCH / SHADOW FIRST  
**Branch:** `cursor/lead-lag-research-plan-3d96`

This document is the gate before code. Implementation must not start until the data-quality constraints and reuse map below are accepted.

---

## 1. Repository findings

### Architecture (do not break)

```
Market books → Redis → strategies → NetProfitCalculator → GlobalOpportunityEngine
  → EV / calibration → risk → portfolio caps → PaperExecutor → fills → tracker
  → delayed markout / causal observation
```

Confirmed invariants:

1. Strategies never call exchange APIs (`BaseStrategy` / `Strategy` protocol).
2. Shared Redis is the multi-bot market-data source (`MarketDataCache`, `hydrate_from_redis`).
3. Profitability is NET via `NetProfitCalculator` / `DefaultProfitabilityEngine` / `build_fill_economics`.
4. Risk approves every executable trade.
5. Paper is default; live is a separate path.
6. Maker fill assumptions remain trade-through-conservative (fill lab: do not loosen).
7. Shadow research (toxicity, fill lab) must not alter production headline PnL.

### Closest existing research patterns to reuse

| Concern | Reuse |
|---|---|
| Shadow-only research package | `bot/opportunity/toxicity/` (types, shadow admit, walkforward, runner, report JSON) |
| Causal event order | `bot/opportunity/causal_walkforward.py` + `toxicity/walkforward.py` |
| Offline study + dashboard panel | `bot/opportunity/fill_lab/` + `status().fill_model_lab` |
| Executable VWAP / depth | `CrossExchangeArbitrageStrategy` depth walk + `NetProfitCalculator` |
| Feature flags | `bot/core/config.py` (`toxicity_shadow_*` pattern) |
| Dashboard panel | toxicity + FILL MODEL LAB sections in `bot/paper/dashboard.py` |

### What does **not** exist yet

- No lead-lag package, detector, or horizon grid.
- No directed venue-pair discovery report.
- No sub-second outcome tape joined to decision times.
- `data/market_data/` **does not exist**; `market_data_recording_enabled=False`.

### Known economics motivating the experiment (unchanged)

- maker_inventory: ~17 completed RTs, ~−€62 live-equivalent NET.
- ~100% observed maker fills TRADE_THROUGH; adverse ≈ 27 bps vs ~8–12 bps expected NET margin.
- Toxicity pre-trade: `reject_model_as_not_predictive` (shadow-only).
- Fill mechanism study: **REQUIRE BETTER DATA**; keep TRADE_THROUGH baseline.

---

## 2. Data-quality verdict (critical)

### Preliminary classification: **LOW → UNSUPPORTED** for sub-second lead-lag OOS claims

| Clock / artifact | Quality | Notes |
|---|---|---|
| Exchange event timestamps | **MEDIUM / MIXED** | Binance depth has `E`; OKX has `ts`; **Bitvavo books use `datetime.now(UTC)`** (local, not exchange event). |
| Local `received_at` | **MEDIUM in-process** | Set at parse in adapters; dual clock exists on `MarketTick` / `MarketDataEvent`. |
| Redis publish / hydrate | **LOW for latency research** | Latest snapshot only; identical-payload skip; hydrate resets `received_at` to now — **receive skew not preserved** across Redis hop. |
| Polling cadence | **~100 ms shared poll** | Sub-50/100 ms horizons often **UNSUPPORTED_BY_DATA** under shared mode. |
| Historical L2 / top-of-book tape | **UNSUPPORTED** | No recordings on disk. |
| Paper dumps | **UNSUPPORTED for lead-lag** | Trade/decision sparse; not a synchronized multi-venue book path. |

### Horizon support (predeclared grid) — honest defaults **without new recordings**

| Horizon | Support without tape | With continuous TOB recording (exchange_ts + receive_ts) |
|---|---|---|
| 50 ms | **UNSUPPORTED_BY_DATA** | Conditionally SUPPORTED only if event clocks are ms-grade on **both** venues |
| 100 ms | **UNSUPPORTED_BY_DATA** (shared poll ~100 ms) | PARTIALLY_SUPPORTED |
| 250 ms | PARTIALLY_SUPPORTED at best (live shadow only) | SUPPORTED if tape exists |
| 500 ms | PARTIALLY_SUPPORTED | SUPPORTED |
| 1000 ms | PARTIALLY_SUPPORTED | SUPPORTED |
| 2000 ms | PARTIALLY_SUPPORTED | SUPPORTED |
| 5000 ms | PARTIALLY_SUPPORTED | SUPPORTED |

**Bitvavo caveat:** substituting local wall clock for exchange event time means Bitvavo↔* pairs cannot claim exchange-time precedence until adapter timestamps improve or research uses **receive_at-only** with explicit LOW quality label.

### Verdict for Phase B/C offline today

Likely final research verdict without new data: **INSUFFICIENT_DATA**.

That is an acceptable success outcome. Do not fabricate precision or interpolate fictional books.

---

## 3. Primary research question (restated)

Does

> leader return at decision time *t*

predict

> follower **executable** price movement after *t + Δ*

such that predicted move − fees − VWAP impact − latency haircut − hedge costs − inventory costs remains positive under causal walk-forward and untouched OOS?

Not: maximize historical spread.  
Not: green dashboard via non-participation.

---

## 4. Architecture changes (planned — incremental)

### Package layout (new)

```
bot/opportunity/lead_lag/
  __init__.py
  types.py          # LeadLagObservation, Signal, Opportunity, Outcome (dataclasses; avoid hot-path Pydantic)
  timestamps.py     # dual-clock audit; quality grades HIGH/MEDIUM/LOW/UNSUPPORTED
  horizons.py       # predeclared grid + UNSUPPORTED_BY_DATA
  pairs.py          # directed same-symbol venue pairs
  models_a_d.py     # interpretable baselines A–D (causal rolling only)
  economics.py      # wraps NetProfitCalculator + depth VWAP; no second NET formula
  hedge.py          # FULLY_HEDGED default; reject if hedge not executable
  shadow.py         # shadow admit; never production gate
  walkforward.py    # extend causal ordering from toxicity/causal_walkforward
  observer.py       # Phase A: collect observations from cycle books (no orders)
  runner.py         # CLI → data/lead_lag_report.json
  states.py         # OBSERVED … PAPER_FAILED
```

### Integration points (shadow-safe)

1. **Config** (`bot/core/config.py`):
   - `LEAD_LAG_ENABLED=true`
   - `LEAD_LAG_SHADOW_ONLY=true`
   - `LEAD_LAG_EXECUTION_ENABLED=false` (hard default)
2. **Observer hook** in paper cycle / GOE metadata path — **observation only**; must not consume portfolio capacity, alter route calibration, maker calibration, EV capture, or production ranking.
3. **Optional later:** child strategy under `GlobalCompositeStrategy` gated by execution flag — **not in v1**.
4. **Dashboard:** separate **LEAD-LAG LAB** panel; never merge into Live-equivalent PnL.
5. **Report:** `docs/LEAD_LAG_HEDGED_DISLOCATION_REPORT.md` (sections A–O).

### Explicit non-goals for v1

- No XGBoost / RF / NN / large HPO.
- No cross-symbol pairs until same-symbol cross-venue works.
- No loosening fills/fees/thresholds/PnL.
- No automatic paper execution.
- Do not hardcode Binance as leader.

---

## 5. Phased delivery plan

### Phase 0 — this document

Audit + plan + data-quality honest fail paths. **← current step**

### Phase 1 — scaffolding + safety (code, still no alpha claims)

- Domain types + states + config gates.
- Timestamp/data-quality auditor (per venue, per horizon).
- Dashboard empty/honest panel + CLI runner skeleton.
- Tests: existing maker fingerprints unchanged; lead-lag cannot alter maker/fill path; shadow cannot touch production PnL; execution flag default false.

### Phase 2 — Phase A observation (live or recorded)

- Collect `LeadLagObservation` from in-cycle multi-venue books **or** JSONL tape.
- Prefer enabling / extending `MarketDataRecorder` to persist **exchange_ts + received_at + bid/ask(+sizes) + depth top-N** per venue/symbol.
- No fills, no ranking effect.

### Phase 3 — Phase B causal signal discovery

- Models A–D only; versioned; causal rolling stats.
- Walk-forward: release_due → update from known outcomes only → predict → immutable decision → wait → observe.
- Directed pairs among binance/bitvavo/okx (and others only if synchronized).
- Predeclared horizons; mark unsupported honestly.

### Phase 4 — Phase C shadow execution economics

- Executable VWAP from asks/bids (never mid when depth exists).
- FULLY_HEDGED default; missing hedge → reject.
- Latency grid: 0 / 50 / 100 / 250 / 500 / 1000 ms — report all; do not pick winner.
- `conservative_net > 0` with predeclared uncertainty methodology (sparse → more uncertainty).

### Phase 5 — OOS protocol

- Development period: define pairs/horizons/models/admission; **freeze**.
- Untouched OOS: report **every** predeclared candidate (including losers).
- Verdict ∈ {A…E} from the prompt’s closed set.

### Phase 6 — Phase D gate only

- Keep `LEAD_LAG_EXECUTION_ENABLED=false`.
- Document what would be required to flip it (not flip it).

---

## 6. Prerequisite data work (before claiming B/C/D verdicts other than INSUFFICIENT_DATA)

Minimum recording spec:

1. Enable `market_data_recording_enabled` (or a lighter TOB recorder).
2. Fields per event: `exchange`, `symbol`, `event_timestamp`, `received_at`, `bid`, `ask`, `bid_size`, `ask_size`, optional depth levels, `sequence/nonce`.
3. Never silently substitute event_ts ↔ received_at.
4. Fix or label Bitvavo event timestamps (currently local now).
5. Prefer publisher-local recording **before** Redis hop so receive clocks remain meaningful.
6. Collect enough synchronized multi-venue samples for walk-forward + frozen OOS (order of magnitude: thousands of leader shocks, not dozens of paper trades).

Until then, implementation may still ship Phase 1–2 scaffolding and an **INSUFFICIENT_DATA** report.

---

## 7. Test plan (must stay green)

1. Existing strategy / maker fingerprints identical.
2. Lead-lag cannot alter maker behavior or fill model.
3. No future observation in prediction.
4. Outcome unavailable before horizon.
5. Unsupported resolution → horizon rejected.
6. Mid not used as executable price when depth exists.
7. Missing hedge rejects opportunity.
8. Shadow opportunities cannot affect production PnL.
9. Dev/OOS boundary no leak; freeze before OOS.
10. Deterministic replay.
11. Existing causal walk-forward + toxicity tests remain green.

---

## 8. Acceptance checklist (from prompt)

- [ ] Existing maker fingerprints unchanged
- [ ] Conservative fill model unchanged
- [ ] No fee / threshold / PnL definition changes
- [ ] No production execution by default
- [ ] No future leakage
- [ ] Horizons predeclared
- [ ] OOS candidates frozen before OOS
- [ ] Executable prices use book depth
- [ ] Hedge feasibility explicit
- [ ] Latency sensitivity reported
- [ ] Production PnL untouched; shadow labeled
- [ ] Unsupported data fails honestly
- [ ] Tests pass

---

## 9. Expected near-term verdict (hypothesis, not result)

Given current absence of a synchronized book tape and mixed venue clocks:

**Expected honest outcome after scaffolding + audit run: `INSUFFICIENT_DATA`.**

A later outcome of `PREDICTIVE_BUT_NOT_EXECUTABLE` or `PROMISING_OOS_RESEARCH_SIGNAL` requires recorded data and a frozen OOS pass — not dashboard optimism.

---

## 10. Next action

**Stop here until plan is approved.**

Then implement **Phase 1 only** (scaffolding + safety + timestamp auditor + empty LEAD-LAG LAB + INSUFFICIENT_DATA report skeleton), reusing toxicity/fill_lab patterns — no giant speculative rewrite, no execution enablement, no fill loosening.

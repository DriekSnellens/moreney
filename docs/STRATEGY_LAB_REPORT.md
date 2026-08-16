# Strategy Research Lab Report

**Label separation:** SYNTHETIC (this first tournament) ≠ OBSERVED market tape ≠ live-equivalent paper fills.  
**Execution:** `STRATEGY_LAB_EXECUTION_ENABLED=false` (shadow/research only).  
**Tournament id:** `synthetic_lab_v1`  
**Fingerprint:** `4ee58fd739400944ea5185aa882fbe425d668c4df90005b08f78db1b3e10aea9`

---

## A. Research question

Which strategy has the best **real, NET, risk-adjusted, out-of-sample** edge on the **same** market data — after fees, slippage, adverse/latency, capital lock, causal walk-forward, and frozen OOS — without optimizing for trade count or gross spread?

## B. Dataset

| Field | Value |
|-------|-------|
| Observed research tape | `data/research_marketdata` — **1 event** (insufficient) |
| Tournament tape | **SYNTHETIC** causal multi-venue books (`synthetic_research_tape`, 80 cycles) |
| Symbols | BTCEUR, ETHEUR, ATOMEUR |
| Venues | binance, okx, bitvavo |
| Split | Chronological 70% DEVELOPMENT → freeze → 30% OOS |
| Claim | Synthetic is **plumbing + methodology validation**, not alpha discovery |

## C. Data quality

| Check | Result |
|-------|--------|
| Synchronized observed tape | **NOT_READY** (1 JSONL event) |
| Dual clocks on synthetic | exchange_ts present (MEDIUM quality label) |
| Bitvavo exchange_ts (live) | UNSUPPORTED (unchanged infra) |
| Next step | Collect multi-hour publisher tape, then re-run tournament with `--no-synthetic` when `n_events` ≥ readiness bar |

## D. Strategies

| ID | Adapter | Source |
|----|---------|--------|
| `maker_inventory` | Wrap existing | `MakerInventoryStrategy` — **not rewritten** |
| `executable_cross_venue_arb` | New | Depth VWAP taker-taker; reject if hedge depth missing |
| `lead_lag` | Reuse | `bot/opportunity/lead_lag` economics + hedge |
| `order_book_imbalance` | New | L1/L5 imbalance, microprice vs mid, spread |
| `funding_basis` | Wrap | Existing strategy; **INSUFFICIENT_DATA** without funding_rate |
| `control_no_trade` | Control | Zero accepts always |

## E. Common economic model

- `CommonEconomics` → `NetProfitCalculator` / `DefaultProfitabilityEngine.estimate_sync`
- Cross-venue legs via `walk_book` executable VWAP (**never mid when depth exists**)
- Waterfall shape from `bot/opportunity/waterfall.expected_waterfall`
- Capital velocity: `net_eur / (capital_eur × lock_seconds)`

## F. Development methodology

1. Load identical cycles for all adapters  
2. Causal `generate_decisions(cycle)` only on visible books  
3. DEVELOPMENT scorecards  
4. **FREEZE** config + criteria version `strategy_lab_verdict_v1`  
5. Untouched OOS  
6. Verdict engine (frozen thresholds; SYNTHETIC caps PROMISING/ROBUST → `IN_SAMPLE_ONLY`)

## G. Frozen configuration

See `data/strategy_lab/synthetic_lab_v1/frozen_config.json`.

- Criteria version: `strategy_lab_verdict_v1`  
- Min OOS completed / independent events: 30 / 20  
- Capital mode: ISOLATED sleeves (€25k total)  
- Execution enabled: **false**

## H. OOS methodology

Chronological split only. OOS never used to tune features, venues, horizons, or thresholds. Shadow outcomes = conservative expected NET (explicitly **not** trade-through fill simulation).

## I. Strategy comparison (SYNTHETIC · shadow expected)

| Strategy | Verdict | Dev NET | OOS NET | Velocity | Participation | Independent events |
|----------|---------|--------:|--------:|---------:|--------------:|-------------------:|
| maker_inventory | **IN_SAMPLE_ONLY** | +89.99 | +38.57 | 0.048 | 0.22 | (shadow) |
| control_no_trade | NO_EDGE | 0 | 0 | 0 | 0 | 0 |
| executable_cross_venue_arb | INSUFFICIENT_DATA | 0 | 0 | 0 | — | — |
| lead_lag | INSUFFICIENT_DATA | 0 | 0 | 0 | — | — |
| order_book_imbalance | INSUFFICIENT_DATA | 0 | 0 | 0 | — | — |
| funding_basis | INSUFFICIENT_DATA | 0 | 0 | 0 | — | no funding_rate |

**Interpretation:** Maker’s positive shadow expected NET on synthetic books does **not** contradict the observed live-equivalent **−€62 / TRADE_THROUGH** paper result. Shadow expected ≠ adverse-selection-realized fills.

## J. NET waterfall

Per-strategy waterfalls are in `leaderboard.json` / dashboard “Where did the money go?”. Maker path: gross edge − maker fees − buffer/adverse − → conservative NET (expected). Cross-venue / OBI / lead-lag produced no accepts on this synthetic geometry under conservative gates.

## K. Capital velocity

Primary ranking key alongside total NET, DD, and OOS evidence. Maker shadow velocity ≈ 0.048 €/(€·s) on synthetic — **not** a deployment metric until observed tape + fill lab.

## L. Drawdowns

Reported on scorecards (`max_drawdown_eur`). Control DD = 0 by construction.

## M. Opportunity coverage

Baseline universe = all symbol|buy|sell pairs with valid books. Participation = accepts / baseline. Control participation = 0 (proves “trade less” is not automatic superiority).

## N. Failure analysis

| Strategy | Why thin / fail |
|----------|-----------------|
| cross_venue_arb | Conservative NET / depth gates reject most synthetic dislocations |
| lead_lag | Hedge + conservative NET rarely admit; signal is lab proxy |
| OBI | Weak imbalance / net-too-small rejects |
| funding | No funding_rate on research books → INSUFFICIENT_DATA |
| maker (live paper) | Outside this tournament: adverse selection / trade-through |

## O. OOS results

Under SYNTHETIC, no strategy may receive `OOS_PROMISING` / `OOS_ROBUST`. Maker capped to `IN_SAMPLE_ONLY`.

## P. Final ranking (this run)

1. maker_inventory — IN_SAMPLE_ONLY (synthetic shadow only)  
2. control_no_trade — NO_EDGE (valid control)  
3–6. others — INSUFFICIENT_DATA  

**No winner for capital deployment.**

## Q. Final verdict

| Claim type | Verdict |
|------------|---------|
| OBSERVED alpha | **Not established** — observed research tape not ready |
| SYNTHETIC plumbing | **PASS** — tournament, scorecards, dashboard, fingerprints deterministic |
| Live-equivalent maker | Remains **negative** per existing paper audit (not re-litigated here) |
| Next research | Collect real synchronized tape → re-run with fill-lab / trade-through outcomes → only then consider OOS_PROMISING |

---

## How to regenerate

```bash
PYTHONPATH=. python -m bot.strategy_lab.runner \
  --dataset-id synthetic_lab_v1 \
  --out data/strategy_lab/synthetic_lab_v1

# Dashboard
# GET /strategy-lab
```

## Tests

```text
pytest tests/test_strategy_lab.py \
       tests/test_candidate_hotpath.py \
       tests/test_causal_walkforward_leakage.py \
       tests/test_lead_lag_lab.py \
       tests/test_maker_inventory.py
→ 71 passed
```

Note: `tests/test_toxicity_pretrade.py` expects 17 trades in `data/paper_25000live.json`; current dump has a different trade count (environment data), unrelated to Strategy Lab code.

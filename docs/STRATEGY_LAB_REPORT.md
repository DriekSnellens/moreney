# Strategy Research Lab Report

**Label separation:** SYNTHETIC plumbing ≠ OBSERVED market tape ≠ live-equivalent paper fills.  
**Execution:** `STRATEGY_LAB_EXECUTION_ENABLED=false` (shadow/research only).  
**Outcomes (default):** trade-through conservative replay via shared `execution_replay_net` (fill_rate=0.55 + extra adverse; no queue fills).

---

## Latest OBSERVED run (`observed_tt_lab_v1`)

| Field | Value |
|-------|-------|
| Data label | **OBSERVED** |
| Sample | streamed `max_events=80000` `stride=5` (EUR × binance/bitvavo/okx) — **not** a full-tape claim |
| Cycles | 45347 (DEV 31742 / OOS 13605), chronological 70/30 |
| Outcome mode | `trade_through` |
| Fingerprint | `f58fda886a9b8a2e31688e9fd87982e28a23703f482c82e9b9f0cd1f4234a907` |
| Artifacts | `data/strategy_lab/observed_tt_lab_v1/` |

| Strategy | Verdict | Dev NET (TT) | OOS NET (TT) | Notes |
|----------|---------|-------------:|-------------:|-------|
| control_no_trade | NO_EDGE | 0 | 0 | Control intact |
| maker_inventory | **EDGE_NEGATIVE_AFTER_COSTS** | −0.39 | −124.83 | Aligns directionally with live-equivalent trade-through toxicity; not deployment |
| executable_cross_venue_arb | INSUFFICIENT_DATA | 0 | 0 | No accepts under conservative gates |
| lead_lag | INSUFFICIENT_DATA | 0 | 0 | No accepts |
| order_book_imbalance | INSUFFICIENT_DATA | 0 | 0 | No accepts |
| funding_basis | INSUFFICIENT_DATA | 0 | 0 | No funding_rate on research books |

**Observed alpha:** not established. **No PAPER / capital deployment winner.**  
Gated family tournament on the full indexed tape (`docs/STRATEGY_RESEARCH_TOURNAMENT_REPORT.md`) independently rejects all five research families — same conclusion.

Companion market-data acceptance: `DATASET_ID=mdresearch-research_md_v1-27116902be243a23`, ~7.73M events, ~10.3 h, `DATA_READY_FOR_SLOW_HORIZONS`.

---

## A. Research question

Which strategy has the best **real, NET, risk-adjusted, out-of-sample** edge on the **same** market data — after fees, slippage, adverse/latency, capital lock, causal walk-forward, and frozen OOS — without optimizing for trade count or gross spread?

## B. Dataset

| Field | SYNTHETIC (plumbing) | OBSERVED (this report) |
|-------|----------------------|------------------------|
| Source | `synthetic_research_tape` | `data/research_marketdata` streamed sample |
| Claim | Methodology only | Directional research; subsample ≠ full-tape OOS promotion |
| Split | Chronological 70% DEV → freeze → 30% OOS | Same |

Full-tape family research uses `bot.research.tournament` (L1 index), not this lab’s depth adapters.

## C. Data quality

| Check | Result |
|-------|--------|
| Observed tape | Ready for slow horizons; fast NOT_READY (Bitvavo `exchange_ts`) |
| Lab load path | Streaming + `max_events` / `stride` (avoids multi-GB OOM) |
| Dual clocks | Unchanged schema |

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
- Accept outcomes default to `execution_replay_net` (shared with gated tournament)
- Optional `--outcome-mode shadow` for expected-NET plumbing only
- Capital velocity: `net_eur / (capital_eur × lock_seconds)`

## F. Development methodology

1. Stream identical cycles for all adapters  
2. Causal `generate_decisions(cycle)` only on visible books  
3. DEVELOPMENT scorecards  
4. **FREEZE** config + criteria version `strategy_lab_verdict_v1`  
5. Untouched OOS  
6. Verdict engine (frozen thresholds; SYNTHETIC still caps PROMISING/ROBUST → `IN_SAMPLE_ONLY`)

## G. Frozen configuration

See `data/strategy_lab/observed_tt_lab_v1/frozen_config.json` (OBSERVED) and `data/strategy_lab/synthetic_lab_v1/frozen_config.json` (plumbing).

- Criteria version: `strategy_lab_verdict_v1`  
- Capital mode: ISOLATED sleeves (€25k total)  
- Execution enabled: **false**

## H. OOS methodology

Chronological split only. OOS never used to tune features, venues, horizons, or thresholds.

## I. Strategy comparison

### OBSERVED · trade-through (authoritative for this report)

See table above — maker negative after trade-through haircut; others insufficient accepts.

### SYNTHETIC · historical shadow plumbing (`synthetic_lab_v1`)

| Strategy | Verdict | Notes |
|----------|---------|-------|
| maker_inventory | IN_SAMPLE_ONLY | Positive **shadow expected** NET on synthetic — **not** OBSERVED; capped |
| control_no_trade | NO_EDGE | Valid control |
| others | INSUFFICIENT_DATA | |

Shadow expected ≠ adverse-selection-realized fills. Synthetic must not contradict live-equivalent paper toxicity.

## J–M. Waterfall / capital / drawdowns / coverage

Per-run artifacts under `data/strategy_lab/<id>/`. Control participation = 0.

## N. Failure analysis

| Strategy | OBSERVED note |
|----------|----------------|
| maker_inventory | Trade-through replay → EDGE_NEGATIVE_AFTER_COSTS |
| cross_venue / lead_lag / OBI | Conservative NET / depth gates → no accepts on subsample |
| funding | No funding_rate → INSUFFICIENT_DATA |

## O. OOS results

OBSERVED maker OOS NET strongly negative under trade-through. No `OOS_PROMISING` / `OOS_ROBUST`.

## P. Final ranking (OBSERVED TT)

1. control_no_trade — NO_EDGE  
2. maker_inventory — EDGE_NEGATIVE_AFTER_COSTS  
3–6. others — INSUFFICIENT_DATA  

**No winner for capital deployment.**

## Q. Final verdict

| Claim type | Verdict |
|------------|---------|
| OBSERVED alpha (lab subsample) | **Not established** — maker negative under TT; others thin |
| OBSERVED alpha (gated tournament) | **ALL REJECTED** — see tournament report |
| SYNTHETIC plumbing | **PASS** — fingerprints deterministic; TT haircut tested |
| Live-equivalent maker | Remains adverse / trade-through toxic (paper audit) |
| Next research | Longer multi-regime tape; re-run **same** criteria; do not enable execution |

---

## How to regenerate

```bash
# OBSERVED bounded sample + trade-through outcomes
PYTHONPATH=. python -m bot.strategy_lab.runner \
  --dataset-id observed_tt_lab_v1 \
  --out data/strategy_lab/observed_tt_lab_v1 \
  --no-synthetic \
  --max-events 80000 \
  --stride 5 \
  --outcome-mode trade_through

# Gated full-tape family tournament
PYTHONPATH=. python -m bot.research.tournament.runner

# Dashboard: GET /strategy-lab
```

## Tests

```text
pytest tests/test_strategy_lab.py
→ 14 passed (incl. trade-through haircut ≤ shadow NET)
```

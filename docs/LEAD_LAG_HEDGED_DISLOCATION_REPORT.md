# Lead-Lag Hedged Dislocation — Research Report

**Label:** RESEARCH ONLY / SHADOW COUNTERFACTUAL  
**Strategy:** `lead_lag_hedged_dislocation`  
**Production execution:** **OFF** (`LEAD_LAG_EXECUTION_ENABLED=false`)

---

## O. Final verdict

**INSUFFICIENT_DATA**

No synchronized multi-venue book/tick tape is present (`data/market_data` absent).  
Sub-second causal lead-lag OOS claims are not supportable. Non-participation is not alpha.

Allowed closed set (for future re-runs with tape):

- NO_STABLE_PREDICTIVE_RELATIONSHIP
- PREDICTIVE_BUT_NOT_EXECUTABLE
- EXECUTABLE_IN_SAMPLE_ONLY
- PROMISING_OOS_RESEARCH_SIGNAL
- INSUFFICIENT_DATA

---

## A. Hypothesis

A liquid leader venue/instrument may lead price discovery; a follower temporarily lags.  
If predicted follower **executable** move after costs, latency, and hedge remains positive under causal walk-forward and untouched OOS, a research signal exists.

## B. Motivation

Maker inventory ~−€62 / 17 RTs; ~100% TRADE_THROUGH; adverse ≈ 27 bps vs ~8–12 bps expected NET margin.  
Toxicity shadow rejected as not predictive. Fill lab: require better data; keep TT baseline.  
Lead-lag is a **separate** thesis — does not loosen maker fills.

## C. Timestamp / data sufficiency

| Item | Status |
|---|---|
| Historical tape | **Missing** |
| Binance event clock | MEDIUM (exchange `E` on depth) |
| OKX event clock | MEDIUM |
| Bitvavo event clock | **LOW** (local `now`) |
| Redis receive skew | **Lost** on hydrate |
| Dual-clock policy | event_ts and received_at stored separately; never silently substituted |

Overall quality without tape: **UNSUPPORTED**.

## D. Causal ordering

```
release_due(t)
  → update model only from outcomes already known
  → predict at t
  → immutable shadow decision
  → wait horizon
  → observe outcome
  → only then enter training state
```

## E. Pair universe

Directed same-symbol pairs among `binance`, `bitvavo`, `okx` (all 6 directions).  
No hardcoded Binance leader. No cross-symbol until same-symbol works.

## F. Predeclared horizons

50, 100, 250, 500, 1000, 2000, 5000 ms.

Without tape: largely **UNSUPPORTED_BY_DATA** (especially ≤100 ms under ~100 ms poll).

## G. Signal models

| ID | Description |
|---|---|
| A_SIGNED_LEADER_v1 | Signed leader return |
| B_INCREMENTAL_v1 | Leader − contemporaneous follower |
| C_STANDARDIZED_SHOCK_v1 | Leader / causal rolling vol |
| D_DISLOCATION_v1 | Gap vs causal rolling fair |

No ML / HPO.

## H. Executable cost model

- Entry: follower depth **VWAP** (never mid when depth exists)
- Hedge: **FULLY_HEDGED** via leader depth VWAP; missing hedge → `HEDGE_UNAVAILABLE`
- Costs: fees + slippage/buffer + latency haircut + uncertainty allowance
- Admission: `conservative_net > 0` (predeclared uncertainty; sparse → more uncertainty)
- Aligns with `NetProfitCalculator` structure (no second NET definition for production)

## I. Latency sensitivity

Predeclared grid: 0 / 50 / 100 / 250 / 500 / 1000 ms — report all; do not pick winner.  
With no tape: empty / N/A.

## J. Hedge assumptions

Default FULLY_HEDGED. Explicit venue/side/price/depth/fees/delay. Never assume “probably fills.”

## K. Development results

Without tape: no causal discovery samples. Phase A observer can collect live TOB pairs in paper cycles (research buffer only).

## L. Frozen candidate definitions

Frozen before OOS: all directed pairs × horizons ≥250 ms × models A–D × latency grid.  
No post-hoc selection.

## M. Untouched OOS

Not runnable without tape. Synthetic path exists for **unit tests only** — never live-equivalent.

## N. Failure analysis

1. No `data/market_data` recordings  
2. Bitvavo local clock undermines exchange-time precedence  
3. Redis latest-snapshot + receive_at reset  
4. Paper dumps are trade-sparse, not a book path  

**Required before non-INSUFFICIENT verdicts:** enable recording (exchange_ts + received_at + bid/ask/depth) on publisher side; improve Bitvavo timestamps; collect large synchronized sample; re-run frozen OOS once.

---

## Production safety

| Gate | Value |
|---|---|
| LEAD_LAG_ENABLED | true (observation) |
| LEAD_LAG_SHADOW_ONLY | true |
| LEAD_LAG_EXECUTION_ENABLED | **false** |
| Alters maker fills | No |
| Alters production PnL | No |
| Alters route calibration | No |

Dashboard: **LEAD-LAG LAB** panel — RESEARCH ONLY; not merged into Live-equivalent PnL.

---

## Regenerate

```bash
PYTHONPATH=. python -m bot.opportunity.lead_lag.runner --out data/lead_lag_report.json
```

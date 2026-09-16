# Pre-trade toxicity model report

**Constraint:** simulator frozen (queue off, trade-through unchanged, fees/fills/PnL unchanged).  
**Mode:** shadow predictions only — live execution is not blocked.  
**Data:** `data/paper_25000live.json` (17 completed round trips).  
**Artifact:** `data/toxicity_pretrade_report.json`  
**Run:** `.venv/bin/python -m bot.opportunity.toxicity.runner data/paper_25000live.json`

---

## A. Forensic diagnosis of the losses

| Metric | Value |
|--------|-------|
| Completed RTs | 17 |
| Wins / losses | 1 / **16** |
| Gross | +€71.78 |
| Fees | −€34.85 |
| Slippage | −€5.32 |
| Adverse | −€93.76 |
| Realized NET | **−€62.22** (~−€3.66 / fill) |

**Dominant cost = adverse selection** under trade-through fills.

Loss patterns (hypothesis labels only — n=17):

- **15/16 losses** involve `bitvavo` on at least one leg; many are `bitvavo->bitvavo`.
- **100%** of labeled maker fills are `fill_type=trade_through` (queue fills off).
- Adverse bps proxy often **18–35 bps**, while expected NET is only **~8–12 bps of notional** and the adverse *buffer* in the quote is ~1–5 bps of notional.
- Early route-stop eventually halted `bitvavo->bitvavo`, but only after large cumulative loss (consistent with prior audit).

Hypothesis (not proven): trade-through on Bitvavo selects for informed flow; quote economics do not pay for the realized adverse.

Full per-loss table: `A_forensics_losses` in the JSON report.

---

## B. Which pre-trade features predict toxicity?

**Available at decision time in this dump:** venue, route, symbol, side, strategy, expected fill type, spread (from gross/notional), expected NET/fees/slippage/buffer.

**Missing / sparse in historical rows:** book/quote age, live vol, imbalance, microprice, fair-value deviation, regime.

**Finding:** with n=17, walk-forward split of predictions does **not** separate high vs low observed adverse:

| Half by predicted adverse | Mean predicted bps | Mean observed bps |
|---------------------------|--------------------|-------------------|
| Low predicted | 16.6 | **29.1** |
| High predicted | 26.1 | 26.2 |

`separates_tail = false`. Route/venue identity is correlated with losses descriptively, but the hierarchical predictor does not yet rank toxicity reliably out of chronological order.

---

## C. Toxicity model comparison (causal walk-forward)

Target label: adverse bps proxy ≈ `realized_adverse_eur / notional × 1e4` (5s primary; per-trade horizon join not stored in markout export).

| Model | Taken | Rejected | Realized NET |
|-------|------:|---------:|-------------:|
| A Global | 1 | 16 | −€1.26 |
| B Route | 1 | 16 | −€1.26 |
| C Hierarchical | 1 | 16 | −€1.26 |
| D Bucketed | 1 | 16 | −€1.26 |

Policy comparison:

| Policy | Taken | Rejected | Realized NET |
|--------|------:|---------:|-------------:|
| A Baseline (take all) | 17 | 0 | **−€62.22** |
| B Conditional EV | 9 | 8 | −€21.66 |
| C Toxicity shadow | 0–1 | 16–17 | ~€0 to −€1.26 |
| D Toxicity + early stop | 0–1 | 16–17 | ~€0 to −€1.26 |

Toxicity admission is **near-total rejection**: expected NET margin cannot cover E[adverse|trade_through] ≈ 15–30 bps.

---

## D. Calibration

Not calibrated for ranking. High-predicted half is not higher-adverse in observation. Mean prediction error on the rare take ≈ +20 bps (underestimates adverse).

Sparse cells correctly shrink toward global and carry higher uncertainty (unit-tested).

---

## E. Causal replay

Order enforced: `predict → decide → (if take) observe label`. Rejects never update beliefs. Same-trade adverse cannot affect that trade’s prediction (tests).

---

## F. Untouched OOS

Split ~40/30/30 by time. Warm on train+val, then walk-forward on test (n=6):

| Policy | Taken | NET |
|--------|------:|----:|
| Baseline take-all | 6 | −€25.56 |
| C Hierarchical toxicity | 2 | −€2.51 |
| B Route | 0 | €0 |

OOS NET looks “better” only because almost everything is rejected. That is **not** proven selective toxicity alpha — it is mostly non-participation on a tiny sample. **Do not optimize on OOS.**

---

## G. Quote-age ablation

`book_age_ms` is **absent** (0 / unknown) on all 17 historical trades in this dump.

All MAX_AGE_MS ∈ {60000,30000,10000,4000,2000} → **inconclusive**.  
Live shadow path records `book_age_ms` / `quote_age_bucket` going forward for a future ablation.

---

## H. Trade-through analysis

| | |
|--|--|
| Fraction trade-through | **100%** |
| Mean adverse bps | ~27.6 |
| Mean NET / fill | ~−€3.66 |

**Explicit scope:** the model estimates  
`E(adverse | TRADE_THROUGH, state)`  
under this simulator. It does **not** generalize to neutral resting maker fills on a live exchange.

---

## I. Shadow policy results

- Wired into `GlobalOpportunityEngine` as metadata only (`live_blocks_quote=false`).
- Paper status exposes `toxicity_shadow` (predicted bps, uncertainty, shrinkage, tox-adjusted NET, shadow reason).
- Dashboard panel: **Toxicity shadow (pre-trade)**.
- Completing a round-trip observes the toxicity model; rejects never do.

---

## J. Production recommendation

### **reject_model_as_not_predictive** (for live blocking)

Reasons:

1. Predictions do not separate higher vs lower adverse (`separates_tail=false`).
2. Admission either rejects nearly everything or fails success criterion 3 (selective toxic-tail filter).
3. n=17 is too small for production confidence.
4. OOS improvement is non-participation, not calibrated selection.

### Keep **shadow instrumentation** enabled

Continue collecting pre-trade features + delayed labels. Re-evaluate when:

- hundreds of trade-through fills exist with book age / vol features populated, and
- walk-forward separation and untouched OOS both hold without rejecting all quotes.

---

## Simulator freeze

Fingerprint (unchanged by this work):

- trades=17, realized NET sum=−62.222…  
- fill types include `trade_through`  
- fees/adverse/slippage/gross sums locked in `simulator_fingerprint`

Tests: `tests/test_toxicity_pretrade.py` + causal leakage suite — **19 passed**.

---

## Final principle

We did not make the bad trades look better.

We asked whether toxic fills are identifiable **before** they occur.

**On this sample: not reliably.** Adverse dominates economics; pre-trade ranking is not yet predictive enough for live admission.

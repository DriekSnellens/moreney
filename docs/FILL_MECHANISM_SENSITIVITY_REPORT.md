# Fill Mechanism Sensitivity Study

**Verdict:** Success criterion **C** — historical data is insufficient to evaluate alternative fill mechanisms.

**Production recommendation:** **REQUIRE BETTER DATA**, and **KEEP TRADE-THROUGH BASELINE**.

Do **not** enable experimental fill models for live-equivalent PnL. Do **not** loosen the simulator.

Machine-readable report (local): `data/fill_mechanism_report.json` (regenerate via CLI below; `data/` is gitignored).

---

## Frozen TRADE_THROUGH_ONLY baseline

| Metric | Value |
|---|---|
| Quotes (post-only with `placed_ms`) | 22 |
| Baseline TT fills | 6 |
| Completed round trips | 17 |
| Realized NET | ≈ **−62.22 EUR** |
| Fill types | `trade_through` only |
| Fees (trade sum) | ≈ 34.85 EUR |
| Adverse (trade sum) | ≈ 93.76 EUR |

Production headline PnL source remains **TRADE_THROUGH_ONLY**. Fees, quote decisions, inventory, routes, and markout accounting are unchanged by this lab.

---

## A. Historical data sufficiency

| Capability | Present? |
|---|---|
| Top-of-book updates after quote | No (`data/market_data` absent) |
| Depth levels | No |
| Trade prints | No |
| Quote timestamps (`placed_ms`) | Partial (22/24 orders) |
| Fill timestamps (`created_at`) | Often null |
| Markout horizons (1s/5s/30s/60s) | Yes (export lists; not per-fill joined) |
| Fill-type labels | Yes (`trade_through`) |

| Model | Support |
|---|---|
| TRADE_THROUGH_ONLY | **SUPPORTED** |
| TOUCH_ONLY | **UNSUPPORTED** |
| TOUCH_PERSISTENCE_{100,250,500,1000} | **UNSUPPORTED** |
| DEPTH_CONSUMPTION | **UNSUPPORTED** (queue not estimable; no fabrication) |

---

## B. Fill model definitions

- **QuoteEvent** — decision-time quote. Quote generation is not modified.
- **FillEvent** — fill type, timestamp, price, quote age, market state at fill, markouts, fees, lock time.
- Economics consume **FillEvent**; multiple experimental mechanisms can replay the same quote stream when books exist.
- **TRADE_THROUGH_ONLY** — conservative production baseline.
- **TOUCH_ONLY** — first post-quote at-touch eligibility (bid≥buy / ask≤sell); not auto-fill.
- **TOUCH_PERSISTENCE_*** — predeclared grid **100 / 250 / 500 / 1000 ms** (not tuned on OOS).
- **DEPTH_CONSUMPTION** — refused unless honest queue position is known.

All alternatives are labeled **EXPERIMENTAL / OBSERVATIONAL / COUNTERFACTUAL**, never live-equivalent.

---

## C. Fill eligibility counts

| Model | n | Status |
|---|---|---|
| TRADE_THROUGH_ONLY | 6 | CONSERVATIVE_BASELINE |
| TOUCH_ONLY | 0 | UNSUPPORTED |
| TOUCH_PERSISTENCE_100 | 0 | UNSUPPORTED |
| TOUCH_PERSISTENCE_250 | 0 | UNSUPPORTED |
| TOUCH_PERSISTENCE_500 | 0 | UNSUPPORTED |
| TOUCH_PERSISTENCE_1000 | 0 | UNSUPPORTED |
| DEPTH_CONSUMPTION | 0 | UNSUPPORTED |

---

## D. Markout distributions by fill type

Observed export (TT-dominated; **not** attributed to touch models):

| Horizon | n | mean bps | median | p25 | p75 |
|---|---|---|---|---|---|
| 1s | 28 | 20.84 | 11.06 | 1.68 | 42.23 |
| 5s | 28 | 27.17 | 15.34 | 7.88 | 59.58 |
| 30s | 28 | 31.22 | 13.28 | 7.39 | 64.74 |
| 60s | 28 | 33.37 | 19.69 | 8.43 | 67.69 |

TOUCH_PERSISTENCE_* : **n = 0** (UNSUPPORTED). No cross-model distribution comparison is possible.

Tiny-n: bootstrap CIs in the JSON are descriptive only — no significance claims.

---

## E. Capital lock analysis

Joinable quote→round-trip lock times: **n = 0** in the dump (fill `created_at` often null; opportunity joins incomplete). Quote-age reconstruction is unreliable when fill timestamps are missing. Experimental models: N/A.

---

## F. Causal / OOS comparison

- Causal: quote at t0; only post-t0 market evolution may create experimental eligibility.
- OOS: development split holds the predeclared grid; untouched OOS reports every predeclared model with **no** post-hoc selection.
- Without books, experimental models stay UNSUPPORTED on both splits.

---

## G. Is trade-through an unusually toxic fill selector?

**INSUFFICIENT_DATA.**

Cannot compare TT adverse distributions to touch/persistence eligibility. Only the TT conditional distribution is observed.

---

## H. Production recommendation

1. **REQUIRE BETTER DATA** (primary) — record top-of-book (ideally depth + trade prints) with ms timestamps after each quote.
2. **KEEP TRADE-THROUGH BASELINE** — remain default until better data exists.
3. **ABANDON MAKER THESIS UNDER CURRENT ECONOMICS** — not selected as the fill-study verdict. TT economics are separately deeply negative; that still does **not** authorize loosening fills.

**Do not** promote experimental fill models to live-equivalent PnL without real queue/fill evidence.

---

## Dashboard

Production headline = TRADE_THROUGH_ONLY only.

**FILL MODEL LAB** panel lists each model as CONSERVATIVE BASELINE or EXPERIMENTAL COUNTERFACTUAL / UNSUPPORTED.

---

## Regenerate

```bash
PYTHONPATH=. python -m bot.opportunity.fill_lab.runner --paper data/paper_25000live.json --out data/fill_mechanism_report.json
```

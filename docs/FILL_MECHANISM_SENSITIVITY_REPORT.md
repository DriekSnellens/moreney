# Fill Mechanism Sensitivity Study

**Verdict:** Success criterion **C** — historical data is insufficient to evaluate alternative fill mechanisms.

**Production recommendation:** **REQUIRE BETTER DATA**, and **KEEP TRADE-THROUGH BASELINE**.

Do **not** enable experimental fill models for live-equivalent PnL. Do **not** loosen the simulator.

Baseline fingerprint (frozen): see `data/fill_mechanism_report.json` → `baseline_fingerprint`.

Observed economics (unchanged): 17 completed round trips, realized NET ≈ **−62.22 EUR**, maker fills **trade_through**.

---

## A. Historical data sufficiency

| Capability | Present? |
|---|---|
| Top-of-book updates after quote | No (`data/market_data` absent) |
| Depth levels | No |
| Trade prints | No |
| Quote timestamps (`placed_ms`) | Partially (orders with `placed_ms`) |
| Fill timestamps (`created_at`) | Often null |
| Markout horizons (1s/5s/30s/60s) | Yes (export lists; not per-fill joined) |
| Fill-type labels | Yes (`trade_through`) |

| Model | Support |
|---|---|
| TRADE_THROUGH_ONLY | **SUPPORTED** |
| TOUCH_ONLY | **UNSUPPORTED** |
| TOUCH_PERSISTENCE_{100,250,500,1000} | **UNSUPPORTED** |
| DEPTH_CONSUMPTION | **UNSUPPORTED** (queue position not estimable; no fabrication) |

---

## B. Fill model definitions

- **QuoteEvent** — decision-time quote (price, side, venue, `posted` ms). Quote generation unchanged.
- **FillEvent** — fill type, timestamp, price, quote age, market state at fill, markouts, fees, lock time.
- **TRADE_THROUGH_ONLY** — conservative production baseline (current executor).
- **TOUCH_*** — experimental counterfactual eligibility on post-quote book paths (replay-only when books exist).
- **DEPTH_CONSUMPTION** — refused unless honest queue position is known.

Persistence grid is **predeclared**: 100 / 250 / 500 / 1000 ms. Not tuned on OOS.

---

## C. Fill eligibility counts

On current paper dump:

| Model | n eligible fills |
|---|---|
| TRADE_THROUGH_ONLY | observed baseline TT fills |
| All experimental models | **0** (UNSUPPORTED — no book path) |

---

## D. Markout distributions by fill type

Only **TRADE_THROUGH** has observed markout samples (export horizons). Touch-persistence variants have **n = 0**.

Do not attribute export markout lists to touch models. Tiny-n: report medians/means/p25/p75 + bootstrap CI as descriptive only — no significance claims.

---

## E. Capital lock analysis

Computed for baseline fills when `placed_ms` joins to completed round-trip timestamps. Fill `created_at` often missing → lock/age partially reconstructible. Experimental models: N/A (no fills).

---

## F. Causal / OOS comparison

- Causal rule: quote at t0; market evolution only after t0 may create experimental eligibility.
- OOS: development split defines the predeclared model grid; untouched OOS reports **every** predeclared model. No post-hoc selection.
- Without books, experimental models remain UNSUPPORTED on both splits.

---

## G. Is trade-through an unusually toxic fill selector?

**Answer: INSUFFICIENT_DATA.**

We cannot compare the trade-through adverse distribution to touch / persistence eligibility distributions. We only observe the TT conditional distribution. Therefore we cannot claim TT is (or is not) an unusually toxic subset of maker fills.

---

## H. Production recommendation

Allowed set only:

1. **KEEP TRADE-THROUGH BASELINE** — yes (always, until better data).
2. **REQUIRE BETTER DATA** — **primary**. Need recorded top-of-book (and ideally depth + trade prints) with ms timestamps causally after each quote.
3. **ABANDON MAKER THESIS UNDER CURRENT ECONOMICS** — not selected as the fill-study conclusion. Separately, TT economics remain deeply negative; that does **not** authorize loosening fills or claiming counterfactual profits.

**Not allowed:** promoting any experimental fill model to live-equivalent PnL without real queue/fill evidence.

---

## Dashboard

Production headline remains **TRADE_THROUGH_ONLY**.

Panel **FILL MODEL LAB** shows each model with CONSERVATIVE BASELINE vs EXPERIMENTAL COUNTERFACTUAL / UNSUPPORTED labels.

---

## How to regenerate

```bash
PYTHONPATH=. python -m bot.opportunity.fill_lab.runner --paper data/paper_25000live.json --out data/fill_mechanism_report.json
```

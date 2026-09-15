# Candidate hot-path optimization (strategy_scan → candidate_creation)

**Date:** 2026-08-15  
**Principle:** Same frozen inputs → same candidates, GOE decisions, fills, and NET. Do not redesign Redis hydrate.

**Harness:** `.venv/bin/python scripts/profile_candidate_hotpath.py`  
**Fixture:** multi-venue × multi-symbol books that emit **12 GOE opportunities / cycle** (not an empty reject path).

---

## A. Candidate hot-path profile (where the time went)

Uninstrumented GOE fixture baseline (40–60 cycles): **~4.6 ms** mean in `evaluate_markets` (this is the real `candidate_creation` cost when opportunities exist; the prior post-Redis ~1.2 ms fixture mostly rejected before gate).

cProfile (50 cycles, ranked by `tottime`):

| Rank | Symbol | Notes |
|------|--------|-------|
| 1 | `_gate_candidate` | Dominant; built full `TradeOpportunity` + profitability **even for rejects** |
| 2 | Pydantic `validate_python` | Opportunity / estimate / result construction |
| 3 | `_candidate_from_quote` | Spread / fee / fair-value filters |
| 4 | `NetProfitCalculator.estimate` | Same math required for NET gate |
| 5 | `venue_maker_fee` | **15.5k** calls with repeated `strip().lower()` |
| 6 | `model_copy` | Market strip + post-NET metadata copy |

Fine-grained spans (ranking only; nested spans inflate absolute ms):

| Substage | Share of measured scan | Role |
|----------|------------------------|------|
| candidate_filtering (`_build_candidate`) | high | pair loop |
| validation_model_construction | high | NET estimate |
| candidate_object_construction | high | Pydantic opportunity (was 2× per accept) |
| fee_lookup / price_extraction / context_copying | medium | repeated per pair |
| symbol_normalization / fair_value | low | once per symbol / cycle |

---

## B. Allocation profile

| Source | Cadence | Finding |
|--------|---------|---------|
| `TradeOpportunity` + `ProfitabilityResult` | was **once per gated pair** (incl. rejects) | Largest avoidable cost |
| `MarketSnapshot.model_copy(order_book=None)` | per gated pair | Copied full snapshot to drop book |
| `opportunity.model_copy` after NET | per **accept** | Second Pydantic copy |
| `venue_maker_fee` string/key work | repeatedly per pair | Same venues every cycle |
| `_book_age_ms` | repeatedly on same snapshots | Recomputed wall-clock age |
| Decimal / fee strings | per candidate | Cacheable per cycle |

Classification: **C** (per candidate / gated pair) dominated; **B** (per symbol) small; **A** (per cycle) fair-value + grouping only.

---

## C. Implemented optimizations (top measured wins only)

1. **Cycle-local fee + book-age caches** — compute `venue_maker_fee` / fee strings / book age once per cycle; cleared at every `evaluate_markets` entry (no cross-cycle leakage).
2. **Lightweight `_QuoteDraft` NET gate** — run `DefaultProfitabilityEngine.estimate_sync` (identical `NetProfitCalculator` math) **before** building `TradeOpportunity`; reject without Pydantic opportunity / `ProfitabilityResult`.
3. **Single opportunity construction on accept** — thin market via `MarketSnapshot.model_construct(..., order_book=None)` (same fields as `model_copy`); embed NET metadata in the one `TradeOpportunity` (no second `model_copy`).

Not done (measured impact too small or unsafe): Decimal→float, cross-process shared candidates, changing pair eligibility / loop pruning of economically possible routes, Redis hydrate changes.

---

## D. Before / after

GOE-emitting fixture, uninstrumented `evaluate_markets` (3×60-cycle means):

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| candidate_creation mean | 4.61 ms | 4.17 ms | **−9.5%** |
| candidate_creation p50 | 4.48 ms | 4.12 ms | −8.1% |
| emits / cycle | 12 | 12 | same |
| validate_python calls / 50 cycles | 10 600 | 8 000 | −25% |
| total Python calls / 50 cycles | 593 k | 504 k | −15% |
| candidate fingerprint | `a7310623…2d45` | `a7310623…2d45` | **identical** |

Paper e2e with GOE books (noisy first-cycle skew on `total_cycle` mean; use strategy metrics above for scan cost). RSS/bot unchanged (~65 MB process / ~123 MB e2e harness).

Fleet scaling (process pool, GOE fixture, 12 cycles/bot):

| Bots | Critical-path before | Critical-path after | Agg CPU before | Agg CPU after | RSS/bot |
|------|----------------------|---------------------|----------------|---------------|---------|
| 1 | 0.058 s | 0.051 s | 0.058 | 0.050 | ~65 MB |
| 5 | 0.269 s | **0.079 s** | 0.352 | 0.273 | ~65 MB |
| 10 | 0.318 s | **0.122 s** | 0.662 | 0.550 | ~65 MB |
| 25 | 0.541 s | **0.412 s** | 1.645 | 1.389 | ~64 MB |

Previous CPU/RSS scaling limit (host CPU under many bots) **moved outward**: critical-path stretch is milder at 5–10 bots; RSS/bot unchanged → memory still scales ~linearly with processes. No cross-process shared candidate service introduced.

---

## E. Correctness evidence

```text
pytest tests/test_candidate_hotpath.py \
       tests/test_maker_inventory.py \
       tests/test_identical_payload_cache.py \
       tests/test_perf_regression.py \
       tests/test_causal_walkforward_leakage.py
→ 47 passed
```

| Check | Result |
|-------|--------|
| Candidate fingerprint (frozen nonce=99) | identical before/after |
| Emit ordering | deterministic NET-desc; stable across runs |
| Duplicate route keys | none in emitted set |
| Symbol/route normalization | upper symbol / lower venues |
| Cycle-local cache invalidation | fees + book ages cleared each `evaluate_markets` |
| Draft NET == opportunity NET | `estimate_sync` matches metadata nets |
| Causal A/B/C/D field presence | net/gross/fees/routes still string economic fields |

---

## F. Files

- `bot/strategies/maker_inventory.py` — caches + draft gate + single construct  
- `bot/profitability/engine.py` — `estimate_sync`  
- `bot/perf/hotpath_profile.py`, `candidate_fingerprint.py`, `candidate_hotpath_bench.py`  
- `scripts/profile_candidate_hotpath.py`  
- `tests/test_candidate_hotpath.py`

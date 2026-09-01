# Decision attribution audit (A/B/C/D)

**No tuning. No fill/fee/threshold changes. Ex-post outcomes are evaluation-only.**

Source: `data/paper_25000live.json` · runner: `bot/opportunity/decision_attribution.py`

---

## Answers (section 9)

### 1. Redundant, overlapping, or complementary?

**Path-dependent overlap; conditional EV dominates the combined path.**

Independent gates are *partially complementary* (5 CEV-only + 1 ES-only + 3 both), but on the **combined (D) path** early-stop **never fires** (`d_early_stop_reject_count=0`). Conditional EV rejects the intermediate losers that would have built early-stop evidence for B. Therefore **C ≡ D** (−€19.07).

### 2. Which trades explain −60.97 → −31.53 → −19.07?

| Bucket | n | Ex-post baseline NET | Role |
|--------|--:|---------------------:|------|
| ALL_TAKE | 7 | ≈ −€11.42 | Taken by everyone |
| CONDITIONAL_EV_ONLY_BLOCK | 5 | ≈ −€20.11 | Blocked by C (and D); still taken by B |
| EARLY_STOP_ONLY_BLOCK | 1 | ≈ −€7.65 | Blocked by B only; **still taken by C and D** |
| BOTH_BLOCK | 3 | ≈ −€21.79 | Blocked by B and C (and D) |

Bridge:

```
A (−60.97) = ALL_TAKE + CEV_only + ES_only + BOTH
B (−31.53) = ALL_TAKE + CEV_only          (avoids ES_only + BOTH ≈ +€29.43)
C (−19.07) = ALL_TAKE + ES_only           (avoids CEV_only + BOTH ≈ +€41.90)
D (−19.07) = C                            (early-stop inert on D's path)
```

### 3. Improvement sources (ex-post avoided PnL)

| Source | Improvement vs baseline |
|--------|------------------------:|
| Early-stop unique blocks | +€7.65 (only realized on path B, **not** on C/D) |
| Conditional-EV unique blocks | +€20.11 |
| Overlapping blocks | +€21.79 |
| Sum of independent deltas | +€71.33 ≠ combined +€41.90 → **overlap / path dependence** |

### 4. Profitable trades rejected?

**0** by early-stop · **0** by conditional EV (this sample).

### 5. Large losses missed (|NET| > €3 taken)?

Early-stop path: **4** · Conditional-EV path: **2**

---

## Table A — Decision overlap

| Metric | Count |
|--------|------:|
| Blocked by early-stop (vs baseline take) | 4 |
| Blocked by conditional EV | 8 |
| Intersection | 3 |
| Early-stop unique | 1 |
| Conditional-EV unique | 5 |

## Table B — PnL attribution by category

| Category | n | Baseline NET (ex-post) | Net avoided if blocked |
|----------|--:|-----------------------:|-----------------------:|
| ALL_TAKE | 7 | −11.42 | 0 |
| EARLY_STOP_ONLY_BLOCK | 1 | −7.65 | +7.65 (on B only) |
| CONDITIONAL_EV_ONLY_BLOCK | 5 | −20.11 | +20.11 |
| BOTH_BLOCK | 3 | −21.79 | +21.79 |
| BASELINE_REJECT | 0 | — | — |

## Table C — Conditional EV errors

- **False rejects** (pred neg / realized pos): **0**
- **False accepts** (taken / realized < −€3): **2** (details in JSON)

## Table D — Mechanism timing (bitvavo→bitvavo)

| Mechanism | First reject event | Timestamp |
|-----------|-------------------:|-----------|
| Conditional EV | **4** | ~11:17 |
| Early-stop | **9** | ~11:21 |
| Acted first | conditional EV | gap = −5 events |

---

## Path-dependence proof that C == D

One row has `path_dependence_signal=true`:

```
11:21:02  bitvavo→bitvavo XRPEUR  −€7.65
A take · B reject · C take · D take
```

Independent B has early-stop armed. On D, historical n never reaches early-stop because CEV already skipped the evidence trades → D takes this loss just like C.

---

## Tests

`tests/test_decision_attribution.py` — 7 passed (categories, independent state, ex-post label, overlap determinism, D internal consistency, C≡D path dependence).

Full machine-readable output: `data/decision_attribution_report.json`

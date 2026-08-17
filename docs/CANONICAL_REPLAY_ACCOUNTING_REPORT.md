# Canonical replay accounting report

Research-only. This document does not claim profitability and does not enable
execution. Strategy parameters, tape splits, fees, fill rules, adverse model,
thresholds, stability caps, and OOS criteria were not changed.

Schema: `canonical-accounting-v1`  
Replay: `canonical_execution_replay_v1`  
Artifact: `data/research/canonical_accounting_results.json`

---

## 1. Old accounting failure

H-0005 first-lab OOS published:

| Label | Value |
|---|---|
| `NET` | 2218.37 EUR |
| `fills` | 363 |
| `NET/fill` | **0.00503** |
| `EXPECTED_NET` | 3.361 EUR per signal |

Canonical arithmetic on the same `NET` and `fills`:

```
2218.3727597787497 / 363 = 6.111219723908401 EUR per estimated fill
```

The dashboard figure 0.00503 was **not** `NET / fills`.

---

## 2. Root cause

`window_metrics` set:

```
NET_per_fill = EXECUTION_NET / fills
```

where

```
EXECUTION_NET = fill_rate * (EXPECTED_NET - extra_adverse_eur)
```

That is a **mean-edge execution overlay** (per signal), while `NET` is the
**sum of per-signal waterfalls**.

For H-0005:

```
EXPECTED_NET = 3.36117084814962 EUR / signal
extra_adverse = 4 bps * 100 EUR = 0.04 EUR
fill_rate = 0.55
EXECUTION_NET = 0.55 * (3.36117084814962 - 0.04) = 1.826643966482291
EXECUTION_NET / 363 = 0.005032077042650939
```

The system therefore exposed incompatible quantities under the same generic
`NET/fill` label.

H-0007 had the same defect: published `NET/fill = 0.00093` vs
`10743.53957537466 / 1854 = 5.794789414980939`.

---

## 3. Canonical metric schema

Package: `bot/research/accounting/`

Every public quantity is a labeled dataclass (`LabeledQuantity` /
`LabeledCount`) with:

1. numerator  
2. denominator  
3. unit  
4. notional basis  
5. fill model  
6. adverse model  
7. fee model  
8. replay version  
9. expected vs realized  
10. aggregation (aggregate / per signal / per fill / count)

Naked `net`, `pnl`, `edge`, `profit`, and `net_per_fill` are not used as
unlabeled cross-module fields.

Canonical waterfall (frozen research costs, unchanged):

```
gross
- fees          (retail taker round-trip)
- slippage      (2 bps of 100 EUR notional)
- adverse       (8 bps of 100 EUR notional)
- funding       (0)
- transfer      (0)
- other_costs   (latency penalty 2 bps of notional)
= realized_replay_net
```

Fill count remains the existing estimate:

```
EstimatedFillCount = max(1, round(SignalCount * 0.55))   if SignalCount > 0
```

Identities (Decimal tolerance `0.0001`):

```
assert_waterfall_identity(signal lines)
sum(signal realized_replay_net) = aggregate RealizedReplayNetEUR
RealizedReplayNetEUR / EstimatedFillCount = RealizedReplayNetPerFillEUR
RealizedReplayNetEUR / SignalCount = RealizedReplayNetPerSignalEUR
```

Consumers must use `CanonicalEconomics`. Dashboards and tournament windows
must not recompute the waterfall.

The old 0.00503 quantity is retained only as:

**`MeanEdgeExecutionReplayNetPerFillEUR`**

with numerator `MeanEdgeExecutionReplayNetPerSignalEUR`, denominator
`EstimatedFillCount`, replay version `mean_edge_execution_replay_v1`.
It must never occupy generic `NET/fill`.

---

## 4. Expected vs replay vs observed

| World | Meaning | Examples |
|---|---|---|
| `SIGNAL_EXPECTATION` | What the strategy predicts before the decision | `ExpectedNetPerSignalEUR`, `ExpectedNetEUR` |
| `EXECUTION_REPLAY` | What the frozen simulator says would have happened | `RealizedReplayNetEUR`, per-fill / per-signal replay |
| `OBSERVED` | Paper/live observation | `ObservedRealizedRoundtripNetEUR` |

Cross-world ratios require `CrossWorldComparison` (for example `EV_CAPTURE =
observed_realized_net / predicted_expected_net`) with both definitions
recorded. Silent substitution raises `CrossWorldError`.

Observed paper trading was **not run** for these hypotheses (`NOT_RUN`).

---

## 5. H-0005 corrected results

First-lab untouched OOS (frozen split; parameters unchanged):

| Count | Value | Definition |
|---|---|---|
| SIGNALS | 660 | admitted gated signals |
| CANDIDATES | 2277 | parent-universe candidates |
| ADMITTED | 660 | quote_age_ms < 250 |
| REJECTED | 1617 | not labels |
| FILLS | 363 | `round(660 * 0.55)` |

**EXPECTED WORLD** (`SIGNAL_EXPECTATION`)

| Quantity | Value | Numerator / denominator |
|---|---|---|
| `expected_net_per_signal_eur` | 3.36117084814962 | mean-edge waterfall / one signal |
| `expected_net_total_eur` | 3.36117084814962 × 660 | product, not the replay sum |

**EXECUTION REPLAY WORLD** (`canonical_execution_replay_v1`)

| Quantity | Value EUR | Numerator / denominator |
|---|---|---|
| `replay_gross_eur` | 2528.572759778749 | sum(notional × forward) |
| `replay_fees_eur` | 231.00 | sum(round-trip taker fees) |
| `replay_slippage_eur` | 13.20 | 2 bps × 100 × 660 |
| `replay_adverse_eur` | 52.80 | 8 bps × 100 × 660 |
| `replay_other_costs_eur` | 13.20 | 2 bps latency × 100 × 660 |
| `replay_net_eur` | **2218.3727597787497** | waterfall remainder |
| `replay_net_per_signal_eur` | **3.36117084814962** | replay_net / 660 |
| `replay_net_per_fill_eur` | **6.111219723908401** | replay_net / 363 |

Waterfall check:

```
2528.572759778749 - 231.00 - 13.20 - 52.80 - 0 - 0 - 13.20 = 2218.372759778749
```

**Sidecar (not generic NET/fill)**

| Quantity | Value |
|---|---|
| `mean_edge_execution_replay_net_per_fill_eur` | 0.005032077042650939 |
| numerator | `fill_rate * (expected_net_per_signal - extra_adverse)` |
| denominator | EstimatedFillCount = 363 |

Mechanical first-lab verdict remains **OOS_PASS**. Gate selectivity
1617/2277 = **0.710**. Route universe: `okx|bitvavo` (`ROUTE_UNIVERSE_LIMITED`).

---

## 6. H-0007 corrected results

First-lab untouched OOS:

| Count | Value |
|---|---|
| SIGNALS | 3370 |
| CANDIDATES | 3370 |
| ADMITTED | 3370 |
| REJECTED | 0 |
| FILLS | 1854 |

| World | Quantity | Value |
|---|---|---|
| SIGNAL_EXPECTATION | `expected_net_per_signal_eur` | 3.187993939280314 |
| SIGNAL_EXPECTATION | `expected_net_total_eur` | 3.187993939280314 × 3370 |
| EXECUTION_REPLAY | `replay_net_eur` | 10743.53957537466 |
| EXECUTION_REPLAY | `replay_net_per_signal_eur` | 3.187993939280314 |
| EXECUTION_REPLAY | `replay_net_per_fill_eur` | **5.794789414980939** |
| SIDECAR | `mean_edge_execution_replay_net_per_fill_eur` | 0.000933870909710989 |

Mechanical verdict remains **OOS_PASS**. That does **not** imply a selective
gate.

---

## 7. Parent vs child paired incremental results

Paired on the **same candidate universe, timestamps, prices, costs, fill
model, adverse model, and notional**. Child is a filter of parent candidates
(`child_only_signals = 0` in every window). Identity:

```
parent_replay_net = child_replay_net + excluded_signal_net
  (+ unsupported, which was 0)
```

Walk-forward uses the frozen robustness window grid (W0 = first-lab OOS
bounds; W1+ = sequential 1800s windows). 15 complete windows, 1 incomplete.

### H-0005 window table (canonical execution replay, EUR)

| window | parent_replay_net | child_replay_net | delta | parent_signals | child_signals | parent_fills | child_fills | shared_signal_net | excluded_signal_net |
|---|---|---|---|---|---|---|---|---|---|
| W0_FIRST_OOS | 8192.56 | 2411.45 | **-5781.10** | 2318 | 667 | 1275 | 367 | 2411.45 | 5781.10 |
| W1 | 5244.89 | 1460.18 | -3784.71 | 1325 | 381 | 729 | 210 | 1460.18 | 3784.71 |
| W2 | 5091.15 | 1421.13 | -3670.02 | 1489 | 415 | 819 | 228 | 1421.13 | 3670.02 |
| W3 | 5367.39 | 1336.91 | -4030.48 | 1554 | 392 | 855 | 216 | 1336.91 | 4030.48 |
| W4 | 4381.74 | 893.44 | -3488.30 | 1324 | 270 | 728 | 148 | 893.44 | 3488.30 |
| W5 | 4307.88 | 713.08 | -3594.80 | 1392 | 248 | 766 | 136 | 713.08 | 3594.80 |
| W6 | 4288.65 | 1215.60 | -3073.05 | 1317 | 357 | 724 | 196 | 1215.60 | 3073.05 |
| W7 | 4173.85 | 1038.88 | -3134.97 | 1257 | 306 | 691 | 168 | 1038.88 | 3134.97 |
| W8 | 4202.37 | 946.24 | -3256.13 | 1077 | 250 | 592 | 138 | 946.24 | 3256.13 |
| W9 | 3877.26 | 658.56 | -3218.70 | 1186 | 213 | 652 | 117 | 658.56 | 3218.70 |
| W10 | 3886.36 | 587.70 | -3298.66 | 1145 | 210 | 630 | 116 | 587.70 | 3298.66 |
| W11 | 3337.53 | 675.80 | -2661.73 | 1130 | 203 | 622 | 112 | 675.80 | 2661.73 |
| W12 | 3896.22 | 499.41 | -3396.81 | 1231 | 165 | 677 | 91 | 499.41 | 3396.81 |
| W13 | 3587.88 | 495.44 | -3092.45 | 1067 | 155 | 587 | 85 | 495.44 | 3092.45 |
| W14 | 2261.20 | 281.82 | -1979.38 | 745 | 106 | 410 | 58 | 281.82 | 1979.38 |
| W15 (incomplete) | 1197.27 | 195.61 | -1001.66 | 411 | 61 | 226 | 34 | 195.61 | 1001.66 |

Aggregate (15 complete windows):

| Statistic | Value |
|---|---|
| mean delta | -3430.75 EUR |
| median delta | -3298.66 EUR |
| positive window fraction | **0.0** |
| worst window | W0_FIRST_OOS, -5781.10 |
| best window | W14, -1979.38 |
| aggregate delta | **-51461.29 EUR** |

No p-values. Assumptions for a time-block bootstrap were not justified here.

**Interpretation:** excluded (stale) parent candidates still have **positive**
canonical replay net in every window. The freshness gate reduces traded
notional more than it removes losing trades. H-0005 is **not** incrementally
better than its parent on paired aggregate `RealizedReplayNetEUR`.

This is compatible with the earlier mixed *per-signal expected-net* comparison:
those compared different normalizations. Canonical aggregate replay is the
evaluation output required by this protocol.

---

## 8. Cost stress methodology

Every cell applies multipliers to the **canonical waterfall sums** of the
first-lab OOS replay (not the mean-edge overlay, not fill-rate rescaling of
NET):

```
stressed_net = gross
  - fees * fee_multiplier
  - slippage * slippage_multiplier
  - adverse * adverse_multiplier
  - funding - transfer - other_costs
```

Grid (research overlay only; production costs unchanged):

- fee_multiplier ∈ {1.0, 1.10, 1.25, 1.50}
- slippage_multiplier ∈ {1.0, 1.50, 2.0, 3.50}  (maps frozen add-bps onto the 2 bps base)
- adverse_multiplier ∈ {1.0, 1.125, 1.25, 1.625, 2.25}  (maps frozen add-bps onto the 8 bps base)

80 cells. H-0005 first-lab OOS: **80 / 80 positive**.

| Cell | replay_net_eur |
|---|---|
| worst | 2003.87 |
| median | 2140.82 |
| best | 2218.37 |

Break-even extra adverse against the **canonical economic scale**:

| Field | Value | Definition |
|---|---|---|
| `extra_cost_eur` | 2218.3727597787497 | additional adverse EUR to zero replay_net |
| `notional_eur` | 66000.0 | 100 EUR × 660 signals |
| `extra_cost_bps_of_notional` | **336.117** | extra_cost_eur / notional_eur × 10000 |

This is 336 bps **of the 66,000 EUR notional base**, not 336 bps of the 0.005
mean-edge NET/fill scale.

---

## 9. Exact denominators for every ratio

| Ratio | Numerator | Denominator |
|---|---|---|
| `replay_net_per_fill_eur` | `RealizedReplayNetEUR` | `EstimatedFillCount` = round(signals × 0.55) |
| `replay_net_per_signal_eur` | `RealizedReplayNetEUR` | `SignalCount` |
| `expected_net_per_signal_eur` | mean-edge waterfall | one signal |
| `expected_net_total_eur` | `ExpectedNetPerSignalEUR` | × `SignalCount` (product) |
| `mean_edge_execution_replay_net_per_fill_eur` | `fill_rate × (expected_net_per_signal − extra_adverse)` | `EstimatedFillCount` |
| `extra_cost_bps_of_notional` | extra cost EUR | `CanonicalNotionalEUR × SignalCount` |
| `delta_replay_net_eur` | child `RealizedReplayNetEUR` − parent `RealizedReplayNetEUR` | same paired universe (not a ratio) |
| `EV_CAPTURE` | observed realized net | predicted expected net (explicit cross-world only) |

---

## 10. Accounting verdict

**ACCOUNTING_AUDIT = PASS**

- Waterfall identity holds.  
- Aggregate equals the sum of signal nets (or the stored-sum reconstruction
  for first-lab OOS).  
- Per-fill and per-signal arithmetic match the labeled denominators.  
- Expected / replay / observed cannot be mixed unlabeled.  
- The 0.00503 figure is labeled as the mean-edge sidecar.  
- Production execution remains disabled. PASS is not profitability.

---

## 11. H-0005 research state

Replication state machine: `CANDIDATE → FIRST_OOS_PASS → REPLICATING →
REPLICATION_PASS → ROBUST_PAPER_CANDIDATE | REJECTED`.

H-0005 is **REPLICATING**.

| Criterion | Result |
|---|---|
| accounting audit PASS | yes |
| ≥ 20 independent complete windows | **no (15)** — protocol assumption, not fit on OOS |
| paired child-vs-parent comparison | yes |
| positive aggregate paired delta | **no (0 / 15 windows)** |
| no window > 70% concentration | yes (top window share 0.165) |
| no symbol > 70% | yes (max 0.679) |
| route limitation reported | yes (`okx\|bitvavo`, `ROUTE_UNIVERSE_LIMITED`) |
| frozen cost stress remains positive | yes (80 / 80) |
| no leakage | yes (forensic timestamps rejected) |
| no parameter retune after OOS | yes |

Positive first-lab aggregate NET is **not** sufficient to advance.
Live alpha is **not** declared.

**RESEARCH_DECISION:** `PROMISING_REPLICATION_REQUIRED`  
(mechanical OOS_PASS + selective gate, but incremental paired replay is
negative and window count is below the frozen 20-window bar)

---

## 12. H-0007 disposition

**RESEARCH_STATUS = GATE_INACTIVE**

- First OOS admitted 3370 / 3370.  
- Selectivity 0.0 (below the frozen 0.05 floor).  
- Walk-forward rejects only ~20 / 28878 candidates; parent SHMR already
  fires almost exclusively WIDE.  
- OOS_PASS does not imply selective strategy improvement.  
- No automatic child hypotheses (`h0007_auto_child_generation = false`).  
- Do not retune the WIDE threshold.  
- Future work requires a materially different candidate regime or more
  diverse data.

---

## 13. Production execution status

**DISABLED**

`ROBUST_PAPER_CANDIDATES = []`

ACCOUNTING_FAIL would also block `ROBUST_PAPER_CANDIDATE`. The audit passed;
H-0005 still cannot receive that status (window count + failed paired delta).

Realtime hot path was not modified (no Pydantic on quote/fee/Redis cycles).
Research microbench on 2000 synthetic signals:

| Step | seconds |
|---|---|
| `attach_event_economics` | 0.0017 |
| `window_metrics` (now canonical) | 0.0198 |
| `assemble_canonical` | 0.0181 |
| full H-0005+H-0007 live paired replay | 274.5 |

---

## Tests

`tests/test_canonical_accounting.py` covers waterfall identity, aggregate
sum, per-fill / per-signal arithmetic, world mixing, observed substitution,
dashboard unlabeled NET/fill regression, H-0005 paired universe, excluded
signal accounting, H-0007 gate inactivity, canonical stress grid, replay
fingerprint, no look-ahead, frozen OOS parameters, and metadata emission.

Related labs (`tests/test_edge_robustness_lab.py`,
`tests/test_regime_hypothesis_lab.py`) remain green.

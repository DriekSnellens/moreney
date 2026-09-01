# Cross-venue dislocation — final validation

Research-only. Not live alpha. Production execution remains DISABLED.
No new strategies. No parameter tuning. No hypothesis generation.

```
FINAL_VALIDATION_VERDICT: ROBUST_PAPER_CANDIDATE
```

## WHY

- BASELINE EXECUTION_NET 212011.7776899441939407528776553516 EUR with accounting PASS.
- MILD_REALISM 166564.0142838215472023416063898164 EUR and MODERATE_REALISM 75449.0876486249965808878960733458 EUR stay positive.
- 62 complete windows (>= 20); MODERATE 62 positive / 0 negative.
- Top window share 0.039 < 0.70.

## NEXT_ACTION

Start SHADOW_PAPER_VALIDATION with the strategy frozen; do not enable production execution; do not optimize parameters.

---

## 1. Strategy identity and frozen fingerprint

| | |
|---|---|
| STRATEGY | cross_venue_dislocation |
| protocol_version | final_validation_v1 |
| configuration_hash | bd2f80d56885cae01a582c5081da013d07b914dda36f9e7dd85f3a25bb462ca4 |
| matrix_fingerprint | b323aaa4498774187d4822902d90d36d41f6d280ffa025383e2391bb2710552e |
| code_commit | 60ee55fa41262245df30628993bead68ad45f6dd |
| EXECUTION | RESEARCH_ONLY |
| PRODUCTION_EXECUTION | DISABLED |
| NEW_STRATEGIES_CREATED | [] |

Frozen universe:

| id | classification | reason |
|---|---|---|
| H-0007 | REJECT | The wide-spread regime gate does not materially change the traded universe. It has no demonstrated incremental value. |
| H-0005 | REJECT_AS_INCREMENTAL_FILTER | The quote freshness gate removes economically positive parent trades. Paired delta is negative across the published windows. Do NOT retune quote_age_ms. |
| cross_venue_dislocation | PRIMARY_VALIDATION_CANDIDATE | Only currently observed strategy with meaningful positive canonical replay evidence across multiple independent windows. |

## 2. Dataset fingerprint

| | |
|---|---|
| DATASET | mdresearch-research_md_v1-cf21dcf254e50536 |
| DATASET_FINGERPRINT | cf21dcf254e505362ec02b3b7205404b53c87deb484a0e1e0d208498f0a80db2 |

## 3. Time range / 4–6. Sample

| | |
|---|---|
| window_ids | ['W0_FIRST_OOS', 'W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7', 'W8', 'W9', 'W10', 'W11', 'W12', 'W13', 'W14', 'W15', 'W16', 'W17', 'W18', 'W19', 'W20', 'W21', 'W22', 'W23', 'W24', 'W25', 'W26', 'W27', 'W28', 'W29', 'W30', 'W31', 'W32', 'W33', 'W34', 'W35', 'W36', 'W37', 'W38', 'W39', 'W40', 'W41', 'W42', 'W43', 'W44', 'W45', 'W46', 'W47', 'W48', 'W49', 'W50', 'W51', 'W52', 'W53', 'W54', 'W55', 'W56', 'W57', 'W58', 'W59', 'W60', 'W61'] |
| independent complete windows | 62 |
| signals (parent candidates) | 67443 |
| estimated fills (round(n × 0.55)) | 37094 |
| CANONICAL_REPLAY_NET | 212011.77768994423804883243 EUR |

## 7. Scenario matrix

Frozen before replay. Overlay values come from the existing robustness-lab grids.

| scenario | class | candidates | fills | missed | partial | CANONICAL_REPLAY_NET | EXECUTION_NET | +w/n | max_dd | accounting |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BASELINE | BASELINE | 67443 | 67443 | 0 | 0 | 212011.77768994423804883243 | 212011.7776899441939407528776553516 | 62/62 | -41.2764592277683211860000 | PASS |
| MILD_REALISM | REALISTIC | 67443 | 60799 | 6644 | 60799 | 212011.77768994423804883243 | 166564.0142838215472023416063898164 | 62/62 | -35.7543329922648255576400000000 | PASS |
| MODERATE_REALISM | REALISTIC | 67443 | 33768 | 33675 | 33768 | 212011.77768994423804883243 | 75449.0876486249965808878960733458 | 62/62 | -23.2172472464390242370000 | PASS |
| HARSH_REALISM | STRESS | 67443 | 33754 | 33689 | 33754 | 212011.77768994423804883243 | 46421.4845560489402777266238798592 | 62/62 | -15.7189292868736885693000000000 | PASS |
| STRESS | ADVERSARIAL | 67443 | 33688 | 33755 | 33688 | 212011.77768994423804883243 | 44461.6588804478154395472240779523 | 62/62 | -17.7440760627502352677000000000 | PASS |

## 8. Baseline result

| | |
|---|---|
| EXECUTION_NET | 212011.7776899441939407528776553516 EUR |
| CANONICAL_REPLAY_NET | 212011.77768994423804883243 EUR |
| candidate_count | 67443 |
| fill_count | 67443 |
| missed_fill_count | 0 |
| partial_fill_count | 0 |
| CANONICAL_REPLAY_NET_PER_SIGNAL | 3.143569795085394155788331332 |
| CANONICAL_REPLAY_NET_PER_FILL | 5.715527516308412089524786488 |
| EXECUTION_NET_PER_SIGNAL | 3.143569795085393501783029783 |
| EXECUTION_NET_PER_FILL | 3.143569795085393501783029783 |
| max_drawdown | -41.2764592277683211860000 |
| windows +/− | 62 / 0 |
| median / worst / best window | 3335.885412470472 / 1897.905965583297056394690 / 8239.5828537410690378819650 |
| top_window_share | 0.038863797773494524 |
| accounting | PASS |


## 9. Mild realism result

| | |
|---|---|
| EXECUTION_NET | 166564.0142838215472023416063898164 EUR |
| CANONICAL_REPLAY_NET | 212011.77768994423804883243 EUR |
| candidate_count | 67443 |
| fill_count | 60799 |
| missed_fill_count | 6644 |
| partial_fill_count | 60799 |
| CANONICAL_REPLAY_NET_PER_SIGNAL | 3.143569795085394155788331332 |
| CANONICAL_REPLAY_NET_PER_FILL | 5.715527516308412089524786488 |
| EXECUTION_NET_PER_SIGNAL | 2.469700551337003798798119989 |
| EXECUTION_NET_PER_FILL | 2.739584767575478991469294008 |
| max_drawdown | -35.7543329922648255576400000000 |
| windows +/− | 62 / 0 |
| median / worst / best window | 2564.5712204905203 / 1483.019760216187398507941 / 6432.5950276106013821303735 |
| top_window_share | 0.03861935637940135 |
| accounting | PASS |


## 10. Moderate realism result

| | |
|---|---|
| EXECUTION_NET | 75449.0876486249965808878960733458 EUR |
| CANONICAL_REPLAY_NET | 212011.77768994423804883243 EUR |
| candidate_count | 67443 |
| fill_count | 33768 |
| missed_fill_count | 33675 |
| partial_fill_count | 33768 |
| CANONICAL_REPLAY_NET_PER_SIGNAL | 3.143569795085394155788331332 |
| CANONICAL_REPLAY_NET_PER_FILL | 5.715527516308412089524786488 |
| EXECUTION_NET_PER_SIGNAL | 1.118708949018059644157108908 |
| EXECUTION_NET_PER_FILL | 2.234336876588041831938163234 |
| max_drawdown | -23.2172472464390242370000 |
| windows +/− | 62 / 0 |
| median / worst / best window | 1199.007652545103 / 656.1705496292291500704950 / 2941.1418986754355230734375 |
| top_window_share | 0.038981808665104985 |
| accounting | PASS |


## 11. Harsh realism result

| | |
|---|---|
| EXECUTION_NET | 46421.4845560489402777266238798592 EUR |
| CANONICAL_REPLAY_NET | 212011.77768994423804883243 EUR |
| candidate_count | 67443 |
| fill_count | 33754 |
| missed_fill_count | 33689 |
| partial_fill_count | 33754 |
| CANONICAL_REPLAY_NET_PER_SIGNAL | 3.143569795085394155788331332 |
| CANONICAL_REPLAY_NET_PER_FILL | 5.715527516308412089524786488 |
| EXECUTION_NET_PER_SIGNAL | 0.6883069340932185738731465664 |
| EXECUTION_NET_PER_FILL | 1.375288397109940755991189900 |
| max_drawdown | -15.7189292868736885693000000000 |
| windows +/− | 62 / 0 |
| median / worst / best window | 720.0187759893657 / 402.191496745811216794825 / 1883.934662602257613188680 |
| top_window_share | 0.04058324891199051 |
| accounting | PASS |


## 12. Stress result

| | |
|---|---|
| EXECUTION_NET | 44461.6588804478154395472240779523 EUR |
| CANONICAL_REPLAY_NET | 212011.77768994423804883243 EUR |
| candidate_count | 67443 |
| fill_count | 33688 |
| missed_fill_count | 33755 |
| partial_fill_count | 33688 |
| CANONICAL_REPLAY_NET_PER_SIGNAL | 3.143569795085394155788331332 |
| CANONICAL_REPLAY_NET_PER_FILL | 5.715527516308412089524786488 |
| EXECUTION_NET_PER_SIGNAL | 0.6592479409345345764504429530 |
| EXECUTION_NET_PER_FILL | 1.319807019723575618604465212 |
| max_drawdown | -17.7440760627502352677000000000 |
| windows +/− | 62 / 0 |
| median / worst / best window | 672.188599789713 / 347.084756738410423517970 / 1757.1371697476884092949475 |
| top_window_share | 0.03952027913471299 |
| accounting | PASS |


## 13. Window stability

See per-scenario positive/negative/median/worst/best/top_window_share above.
A strategy is not robust if one window dominates (`top_window_share` ≥ 0.70).

## 14. Concentration

| | |
|---|---|
| ROUTE_UNIVERSE | ['okx|bitvavo'] |
| ROUTE_UNIVERSE_LIMITED | True |
| top_symbol | ETHEUR |
| top_symbol_share | 0.5818227933929285 |
| concentration | ROUTE_UNIVERSE_LIMITED |

## 15. Break-even sensitivities

Analytical from BASELINE totals. No interpolation.

| | |
|---|---|
| extra_adverse_required_to_zero_NET_bps | 314.3569795085393501783029783 |
| extra_slippage_required_to_zero_NET_bps | 314.3569795085393501783029783 |
| fee_multiplier_required_to_zero_NET | 9.981627985958266378097686298 |
| fill_rate_required_to_zero_NET | 0_sign_invariant_under_uniform_miss |
| latency_degradation_required_to_zero_NET_ms | 31435.69795085393501783029783 |
| hedge_delay_required_to_zero_NET_ms | 15717.84897542696750891514892 |

Uniform missed fills scale expected NET by p and do not cross zero while p>0 and BASELINE>0. Partial-fill inventory is in the scenario cells.

## 16. Unsupported assumptions

- `quote_disappearance_after_decision`: UNSUPPORTED_BY_DATA — Quote lifetime until fill is not on the L1 tape. Applying extra staleness would change signal discovery (forbidden) or invent fills.
- `missing_follower_book`: UNSUPPORTED_BY_DATA — Follower-book dropout is not an independent tape event stream. Not fabricated.
- `cross_venue_sync_beyond_exchange_ts_flag`: UNSUPPORTED_BY_DATA — Bitvavo exchange_ts coverage may be 0%. Uncertainty is flagged, not replaced with a synthetic clock.

## 17. Accounting audit

BASELINE accounting_identity_status: **PASS**

Canonical identities remain: parent waterfall = gross − fees − slippage − adverse − latency.
Scenario EXECUTION_NET is the overlay result and is labeled separately.
FILL_RATE 0.55 is EstimatedFillCount only, never unlabeled NET/fill.

This live tape run is **not** a rewrite of the published 15-window paired figure
(ALL_PARENT 19557 signals, 10757 estimated fills, CANONICAL_REPLAY_NET 66096.91 EUR).
That published sample remains immutable. This run uses the current tape fingerprint
and 62 complete sequential windows (W0 + later complete slices).

## 17b. Performance / memory

| | |
|---|---|
| wall seconds | 576.9106478289468 |
| replays | 337215 |
| replays/sec | 584.5185927301237 |
| peak RSS MB | 722.1 |
| live waterfalls retained | 0 (overlay objects discarded per signal) |

## 17c. SHADOW_PAPER_VALIDATION (next phase only; not started)

Triggered only because the frozen rules returned ROBUST_PAPER_CANDIDATE.

| | |
|---|---|
| production execution | DISABLED |
| strategy parameters | frozen (okx→bitvavo, horizon 5000 ms, dislocation 40 bps) |
| parameter optimization | forbidden |
| LLM / new hypotheses | forbidden |
| record every candidate | required |
| compare expected vs realized | required |
| predeclared min complete windows | 20 additional unseen windows or 7 additional calendar days, whichever is later |
| enable PaperExecutor | no |

Do not start live trading.

## 18. Final verdict

```
FINAL_VALIDATION_VERDICT: ROBUST_PAPER_CANDIDATE
```

WHY:

- BASELINE EXECUTION_NET 212011.7776899441939407528776553516 EUR with accounting PASS.
- MILD_REALISM 166564.0142838215472023416063898164 EUR and MODERATE_REALISM 75449.0876486249965808878960733458 EUR stay positive.
- 62 complete windows (>= 20); MODERATE 62 positive / 0 negative.
- Top window share 0.039 < 0.70.

NEXT_ACTION: Start SHADOW_PAPER_VALIDATION with the strategy frozen; do not enable production execution; do not optimize parameters.

PRODUCTION_EXECUTION: DISABLED
NO_NEW_ALPHA_CLAIMED: True

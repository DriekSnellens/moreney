# Live vs Research Attribution Report

## 1. Executive Summary

**Generated:** 2026-09-01T15:54:01.046652+00:00

The positive research expectancy (+€212k canonical cross_venue_dislocation replay) and negative/near-zero live session PnL co-exist primarily because they measure different strategies, execution models, and universes — not because of a single tunable filter.

**Key findings:**
- Research=cross_venue_dislocation vs live=maker_inventory / alt-beta micro recycle. 0/10 core components identical. Canonical replay +€212011.77768994423804883243 vs live session €-9.50678359993131475000000000.
- 14,547 inventory-related skip events of 23,299 total. Live-only exit/inventory gates (time_stop_below_be, buy_quality_pause) have no research equivalent.
- Mild realism NET €166564.0142838215472023416063898164 (-21.4% vs canonical). Moderate €75449.0876486249965808878960733458. Live fills=90 with maker/taker mix.
- 23,299 skip events logged but expected NET per skip is INSUFFICIENT_DATA. High skip count alone is not evidence of destructive filtering.

**Primary root cause:** STRATEGY_MISMATCH
**Confidence:** HIGH

## 2. What Research Actually Trades

- **Strategy:** `cross_venue_dislocation`
- **Canonical replay NET:** €212011.77768994423804883243
- **Signals (final validation):** 67443
- **Route:** okx|bitvavo (frozen CVD candidate)
- **Execution model:** Instant round-trip taker arb at depth-VWAP with canonical replay assumptions

## 3. What Live Actually Trades

- **Strategy:** `maker_inventory / alt-beta micro recycle`
- **Executor:** `MicroBudgetLiveExecutor`
- **Session realized PnL:** €-9.50678359993131475000000000
- **Live fills (audit):** 90
- **Execute venues:** Bitvavo + OKX
- **Execution model:** Maker paper + live taker; trail/exit engine; inventory FIFO

## 4. Strategy Match / Mismatch

| Component | Research | Live | Same? |
|---|---|---|---|
| Signal | CrossExchangeArbitrageStrategy / CVD frozen candidate — simultaneous buy-low sel… | MakerInventoryStrategy — per-venue maker/taker alt recycle, momentum gates… | NO |
| Universe | okx|bitvavo route, 67k signals, ETHEUR-heavy (~58% share) | 40+ EUR alts on Bitvavo+OKX, focus_bases allowlist, ETH long-hold | NO |
| Entry | Taker-taker round-trip on dislocation above NET threshold | Maker buys with momentum/headroom gates; taker only when BE+ exit | NO |
| Fees | Frozen venue taker fees in canonical replay | Actual exchange fees; maker/taker mix; fee-aware BE tracking | PARTIAL |
| Slippage | Modeled slippage + execution buffer in profitability engine | Order-book depth + partial fills; maker queue simulation for paper legs | PARTIAL |
| Execution | Canonical replay — instant round-trip fills at VWAP | Live taker on Bitvavo/OKX; maker paper; resting order management | NO |
| Exit | Immediate round-trip close in replay | Trail/soft-arm/exit-engine, time_stop_below_be, momentum exits | NO |
| Inventory | No persistent inventory (arb round-trip) | FIFO session_lots, velocity sleeve, underwater blocks, cross-venue dedup | NO |
| GOE | Not on CVD replay path | Opportunity engine available but disabled (observation mode intelligence) | NO |
| Risk | Research gates (OOS, stability, concentration) | RiskEngine + micro budget cap + daily loss + kill switch | PARTIAL |

Research validates cross-venue dislocation round-trip arb on historical tape. Live executes a distinct maker/taker alt-beta recycle book with inventory, trail exits, and live-only skip gates. Positive research replay NET does not imply the live book should be positive.

## 5. Opportunity Funnel

| Stage | Count | Note |
|---|---:|---|
| MARKET_OBSERVED | NULL | Not logged as discrete counter |
| SIGNAL_CREATED | NULL | Maker emits not persisted to audit |
| PROFITABILITY_EVALUATED | NULL | INSUFFICIENT_DATA at per-opportunity level live |
| GOE_EVALUATED | 953 | Replay-only on submitted buys |
| RISK_EVALUATED | NULL | INSUFFICIENT_DATA |
| SKIP / REJECT | 23299 | Bridge skip counters (aggregate) |
| ORDER_SUBMITTED | 9984 |  |
| FULL_FILL | 90 |  |
| ROUND_TRIP_REALIZED | NULL | FIFO pairs not exported to audit |

## 6. Skip Attribution

**Total skip events:** 23299

| Reason | Count | % of skips | Expected NET |
|---|---:|---:|---|
| `time_stop_below_be` | 12111 | 51.98% | NULL |
| `focus_base_required` | 2820 | 12.1% | NULL |
| `trail_dust` | 2396 | 10.28% | NULL |
| `buy_quality_pause` | 2380 | 10.22% | NULL |
| `trail_no_trusted_cost` | 2280 | 9.79% | NULL |
| `trail_hold_rising` | 380 | 1.63% | NULL |
| `momentum_block` | 299 | 1.28% | NULL |
| `corr_sector_momentum_block` | 243 | 1.04% | NULL |
| `exit_quote_mark_below_maker_be` | 200 | 0.86% | NULL |
| `trail_mark_spike` | 62 | 0.27% | NULL |
| `underwater_cross_venue_block` | 56 | 0.24% | NULL |
| `trail_peak_rewound` | 30 | 0.13% | NULL |
| `live_resting` | 16 | 0.07% | NULL |
| `dust_exit_breakeven` | 7 | 0.03% | NULL |
| `holding_base_buy_block` | 6 | 0.03% | NULL |

**INSUFFICIENT_DATA:**
- Per-skip expected NET requires decision-time economics logging on bridge skips.
- Ex-post positive rate of skipped opportunities requires counterfactual replay.

## 7. Profitability Attribution

INSUFFICIENT_DATA for per-opportunity live profitability rejections. Economic parity audit has 6295 paper CVD rows (diagnostic-only, Aug 2025). Live bridge does not export profitability_result per skip.

## 8. GOE Attribution

- Historical audit replay candidates: 953
- Rejected (GOE replay): 749 (0.786)
- Estimated NET (accepted, replay): €34.04
- Live GOE enabled: **False** (default)
- Note: Replay applies GOE to submitted buys only — selection bias.

## 9. Risk Attribution

INSUFFICIENT_DATA — RiskEngine decisions not written to live_audit.jsonl. Audit order_blocked count: 1124 (mostly max open orders).

## 10. Execution Attribution

- Filled orders: 90
- Buy/Sell: 0 / 90
- Total notional: €4536.115563668261708697971958
- Realized trade PnL (bridge): €-9.50678359993131475000000000

**Degradation categories:**
- FEE_DEGRADATION: 90

**INSUFFICIENT_DATA:**
- Expected entry/exit prices at decision time not in audit payload.
- Round-trip realized NET per fill requires FIFO lot pairing (partial in bridge state).
- Post-fill markouts require mark price time series aligned to fill timestamps.
- Adverse selection at fill time: intelligence attribution store is empty.

## 11. Adverse Selection

- Live attribution records: 0
- Observation mode: False
- Phase21 toxic proxy (historical): 283
- Phase21 avg adverse score: 0.529
- live_micro_attribution_state.json records[] is empty — post-fill markouts not persisted live.
- Phase21 ablation provides proxy adverse scores on historical audit buys only.

## 12. Position / Inventory Effects

- Inventory-related skips: 16836
- Locked notional: €298.33
- Blocked sells (session): 12114
- Open lots: 4

**Top inventory skips:**
- `time_stop_below_be`: 12111
- `buy_quality_pause`: 2380
- `trail_no_trusted_cost`: 2280
- `underwater_cross_venue_block`: 56
- `holding_base_buy_block`: 6
- `sell_below_break_even`: 3

## 13. Exit Attribution

Live-only: trail, soft-arm, time_stop_below_be (12,111 skips), exit_engine. Research: immediate round-trip in replay.

## 14. Capital Efficiency

- Locked notional: €298.33
- Free quote: €3700.6533699025101
- Portfolio value: €4086.030820863461799225
- Research NET/capital-hour: 0.020800
- Live realized NET/capital-hour: -0.03186666979496300992189856870
Live NET/capital-hour is session realized / locked notional — approximate, not annualized. Capital allocation replay is counterfactual on historical audit.

## 15. Regime Analysis

INSUFFICIENT_DATA — regime not tagged on live fills in audit.

## 16. Venue Analysis

| Venue | Fills | Notional EUR |
|---|---:|---:|
| bitvavo | 90 | 4536.115563668261708697971958 |

## 17. Canonical vs Mild vs Moderate vs Live

| Level | NET EUR | Notes |
|---|---:|---|
| Canonical replay | €212011.77768994423804883243 | cross_venue_dislocation, 62 windows |
| Mild realism | €166564.0142838215472023416063898164 | +fee/slip/adverse/latency band |
| Moderate realism | €75449.0876486249965808878960733458 | stronger degradation |
| Live realized (session) | €-9.50678359993131475000000000 | alt-beta maker book |
| Matched live sample | NULL | research↔live match |

**Interpretation:** Research and live are different strategies; direct NET comparison is diagnostic only.

## 18. Data Quality / Accounting Audit

- Fill event IDs unique: True
- Exchange order IDs unique: True
- Timestamps monotonic: True
- Attribution store empty: True
- Missing sources: []

## 19. Root Cause Ranking

| Rank | Cause | Confidence | Evidence |
|---:|---|---|---|
| 1 | STRATEGY_MISMATCH | HIGH | Research=cross_venue_dislocation vs live=maker_inventory / alt-beta micro recycle. 0/10 core components identical. Canon |
| 2 | INVENTORY_LOCK / EXIT_MANAGEMENT | MEDIUM | 14,547 inventory-related skip events of 23,299 total. Live-only exit/inventory gates (time_stop_below_be, buy_quality_pa |
| 3 | EXECUTION_DEGRADATION | MEDIUM | Mild realism NET €166564.0142838215472023416063898164 (-21.4% vs canonical). Moderate €75449.087648624996580887896073345 |
| 4 | EXCESSIVE_FILTERING | LOW | 23,299 skip events logged but expected NET per skip is INSUFFICIENT_DATA. High skip count alone is not evidence of destr |
| 5 | OBSERVABILITY_GAP | HIGH | No opportunity_id in live audit; attribution store empty; fill_id unique=True. Cannot join opportunity→fill→realized NET |
| 6 | RESEARCH_EXECUTION_MODEL_BIAS | MEDIUM | Canonical replay assumes instant round-trip arb. Live holds inventory with trail exits — structurally different PnL path |

## 20. Recommended Next Experiments

### Live execution degradation exceeds signal degradation for matched symbols
- **Change:** Replay live_audit fills with moderate execution realism assumptions
- **Control:** Current canonical CVD replay on same symbol universe
- **Metric:** NET per fill, fill rate
- **Min sample:** n≥200 matched fills
- **Success:** Measured live degradation within ±20% of moderate realism model
- **Rollback:** N/A — research-only replay
- **OOS protection:** Use frozen CVD fingerprint; no parameter tuning on live sample

### Inventory skips destroy positive entry economics
- **Change:** Counterfactual replay: entries that later hit time_stop_below_be
- **Control:** Actual realized NET from bridge FIFO
- **Metric:** Ex-post NET of skipped vs executed entries
- **Min sample:** n≥50 paired entry events
- **Success:** Document whether skipped entries were ex-post positive
- **Rollback:** N/A — analysis only
- **OOS protection:** No live parameter changes; log decision-time economics first

### Opportunity ID propagation enables attribution
- **Change:** Add read-only opportunity_id + decision_economics to micro_order_result audit payload
- **Control:** Current audit without opportunity_id
- **Metric:** Match rate EXACT+PROBABLE
- **Min sample:** n≥100 orders
- **Success:** >80% fills traceable to strategy opportunity
- **Rollback:** Remove audit fields — logging only, no trading impact
- **OOS protection:** Audit-only instrumentation

### Shadow paper of frozen CVD on live tape measures strategy gap
- **Change:** Run shadow_validation observer on live market data window
- **Control:** Historical final_validation canonical NET
- **Metric:** Shadow NET vs live realized on same calendar window
- **Min sample:** ≥24h tape
- **Success:** Quantify strategy mismatch independent of execution
- **Rollback:** Shadow observer already research-only
- **OOS protection:** Frozen strategy fingerprint bd2f80d5…

### Adverse selection proxy correlates with live fill quality
- **Change:** Enable attribution store persistence (observation mode stays on)
- **Control:** Empty attribution store baseline
- **Metric:** post_fill_markout_5s for GOOD vs TOXIC fills
- **Min sample:** n≥100 fills with mark data
- **Success:** Statistically significant markout difference GOOD vs TOXIC
- **Rollback:** Disable persistence — no auto_apply
- **OOS protection:** observation_mode=true, auto_apply=false


---

## Final Conclusions

1. Research and live are NOT the same strategy (cross_venue_dislocation vs maker_inventory / alt-beta micro recycle). This is the primary explanation for divergent expectancy.
2. Research canonical replay shows +€212011.77768994423804883243 on cross_venue_dislocation; live session realized €-9.50678359993131475000000000 on alt-beta maker book.
3. 23,299 live skip events logged; economic weight per skip is INSUFFICIENT_DATA without decision-time NET logging.
4. Execution realism lab shows canonical→moderate NET drop of ~64% (€212011.77768994423804883243 → €75449.0876486249965808878960733458) on the research strategy alone.
5. Next valid step is observability (opportunity_id in audit) and shadow-paper of frozen CVD on live tape — not parameter tuning.

## What NOT to Change Yet

- live_micro_opportunity_engine_enabled — attribution incomplete
- live_micro_intelligence_auto_apply — observation mode must stay on
- momentum thresholds / time_stop_below_be — skip economic weight unknown
- focus_bases universe — no counterfactual NET evidence
- GOE weights — GOE not active live; replay shows 78.6% reject on historical buys
- risk limits and live unlock flags — safety invariant
- execution buffers / profitability thresholds — no OOS evidence on live book
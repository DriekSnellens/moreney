# Live Underperformance Diagnosis — vs €20–100/day Expectation

**Generated:** 2026-09-02T08:35:58.490486Z

Research-only. No live logic, parameters, or safety gates changed.

## 1. Executive Summary

Live underperformance vs €50–100/day (and even vs €20–50) is primarily an architectural capital deadlock plus a strategy/expectation mismatch: the bot that is supposed to harvest maker velocity cannot deploy (€0 active ring) while the numbers that look like €50+/day come from CVD paper/research or alt-beta MTM — neither of which is the live hot path today. Parameter easing alone cannot hit the target while never-loss + underwater soft-block keep cash idle.

### Snapshot

| Metric | Value |
|--------|------:|
| Strategy | `maker_inventory` |
| Budget / free EUR | €2000.0 / €3810.6513556815764 |
| Portfolio | €4082.832741907573471275 |
| Bridge realized (cum) | €-9.622744997389643006513026041 |
| Session realized NET | €0.00 |
| Capital deployed / locked | €0.00 / €186.08 |
| Session elapsed | 1.798083333333333333333333333 h |

**Active ring:** ACTIVE_RING bitvavo=€0/€1000 NEED okx=€0/€1000 NEED

**Underwater blocks:** `{'bitvavo': ['ATOM', 'BNB'], 'okx': ['SOL']}`

## 2. Where does €50–100/day come from?

In-repo live dashboard target is €20–50/day netto on the €2k maker micro path (weekly 140–350). €50–100/day is not a documented maker target; it aligns with paper odds alt-beta 2–5% of €2000 (coin move, not bid/ask harvest). Commit lineage tuned toward €20–50/day maker velocity without a second strategy.

## 3. Throughput math (what €50/day actually requires)

Idealized fee-free: full exit of €55 clip at +1.200% = €0.660/trade. Soft partial 15% ≈ €0.09900/trade.

| Target | Full soft exits / day | Ring turns @ 1.2% on €1k |
|--------|----------------------:|-------------------------:|
| Doc €20 | 31 | 1.7× |
| Doc €50 | 76 | 4.2× |
| User €50 | 76 | 4.2× |
| User €100 | 152 | 8.3× |

Current session: **0** new live trades → **0** recycles → target unreachable.

## 4. Recent daily history (dashboard)

| Day | Points | Realized end | Session end | Session peak | Free end |
|-----|-------:|-------------:|------------:|-------------:|---------:|
| 2026-08-31 | 123 | 0.262374710873693500 | -0.96402825045117375 | 0.38256342742025890 | 3650.3212364466136 |
| 2026-09-01 | 1076 | -9.622744997389643006513026041 | -0.430902235952273850 | 5.521651608953877325 | 3810.6513556815764 |
| 2026-09-02 | 286 | -9.622744997389643006513026041 | -1.144452342938281875 | 1.862108202785135175 | 3810.6513556815764 |

## 5. Ranked root causes

### #1 [CRITICAL] `CAPITAL_DEADLOCK` (architecture)

**Active ring stays €0 despite ~€3.8k free cash — underwater bags block Util-B deploy path**

ACTIVE_RING counts only focus inventory above break-even. Never-loss forbids selling underwater bags. live_micro_ring_soft_block_underwater_eur=25 disables soft-momentum and focus-relax while underwater book ≥ €25 — which is true whenever a single €55 clip is stuck. Free EUR cannot refill the ring.

Evidence:

- `free_eur=3810.6513556815764`
- `capital_deployed_eur=0.00`
- `micro_locked_notional_eur=186.08`
- `underwater_blocked_bases={'bitvavo': ['ATOM', 'BNB'], 'okx': ['SOL']}`
- `sell_below_break_even skips=107299`
- `time_stop_below_be skips=30416`
- `UNDERWATER_BASE_BLOCK bitvavo:ATOM,BNB; okx:SOL (new bases only)`
- `SELLS_BLOCKED_NEVER_LOSS sell_be=107299 time_stop_be=30416`
- `ACTIVE_RING bitvavo=€0/€1000 NEED okx=€0/€1000 NEED`

Levers:

- Raise/zero live_micro_ring_soft_block_underwater_eur (unblock Util-B without forced loss)
- Differentiate live_micro_ring_momentum_min_return below paper_buy_momentum_min_return
- Product decision only: re-enable cut_loss_below_be to recycle stuck bags
- Natural unlock: wait until ATOM/BNB/SOL ≥ fee-aware BE

### #2 [CRITICAL] `STRATEGY_EXPECTATION_MISMATCH` (strategy)

**€50–100/day expectation is not evidenced by live maker_inventory; paper fleet profits came from CVD inject**

Live hot path runs maker_inventory with research hooks disabled. Dashboard documents €20–50/day as aspirational maker velocity. User €50–100 maps closest to alt-beta 2–5% of €2000 (odds.py), not maker recycle math. Paper fleet +€3.4k is CVD inject, not maker. Paper lab maker realized ≈ €0.02779682630784743437828839.

Evidence:

- `live strategy=maker_inventory`
- `paper_lab_realized=0.02779682630784743437828839`
- `bridge_realized=-9.622744997389643006513026041`
- `live_disable_research_hooks=true (CVD off hot path)`
- `docs/PAPER_VS_RESEARCH_PNL_GAP_REPORT.md STRATEGY_MISMATCH`

Levers:

- Treat €20–50 as aspirational until ring velocity is proven live
- Do not use CVD paper/research totals as live maker forecast
- Only consider CVD live after shadow VALIDATED (currently NO-GO)

### #3 [HIGH] `ZERO_VELOCITY` (architecture)

**€50/day needs ~76 full soft recycles; current session has 0 new live trades**

Idealized model: €55 clip × 1.2% soft arm ≈ €0.66/trade → 76 full exits for €50/day (~4.2× ring turns). With capital_deployed=0 and session fills=backfill-only, realized harvest is structurally impossible regardless of scan volume.

Evidence:

- `live_trades_executed=0`
- `approved_opportunities=0`
- `session_live_fill_count=159`
- `backfill_mirrored_count=158`
- `scan opportunities_emitted=21328`
- `cross_venue opportunities_emitted=0`

Levers:

- Unlock capital deadlock (cause #1) before tuning harvest partials
- Measure fills/hour after deploy resumes — not scan emits

### #4 [HIGH] `ENTRY_GATE_STACK` (parameter)

**Even if soft-block lifts, entry/profit stack still starves new BE+ bags**

Session sets paper_buy_momentum_min_return=0.0015 AND live_micro_ring_momentum_min_return=0.0015 (soft floor is a no-op). Plus focus-only, rising-mark requirements, corr-sector block, buy_quality_pause, and NET floors €0.03 / 4 bps. why_not_trade profitability rejects dominate with €0 estimated missed profit.

Evidence:

- `focus_base_required=2820`
- `momentum_block=299`
- `buy_quality_pause=2380`
- `corr_sector_momentum_block=243`
- `why_not_trade=[{'reason': 'profitability', 'count': 10741}, {'reason': 'risk', 'count': 3431}]`

Levers:

- Actually lower ring momentum floor while NEED (today equal to full floor)
- While ring NEED: modestly ease paper_maker_min_net_return / min_profit_eur
- Keep never-loss; do not confuse entry easing with cut-loss

### #5 [HIGH] `MAKER_EDGE_THIN` (strategy)

**maker_inventory shows near-zero edge after costs in paper lab and live**

Paper lab maker sandbox ~flat. Live GOE profitability gate theoretical sum is negative. Scan rejects dominated by stale_edge + fees_eat_edge. Unlocking deploy alone does not magically create €50–100/day without proven maker NET after fees and fills.

Evidence:

- `paper_lab_realized=0.02779682630784743437828839`
- `paper_lab_equity=1999.389680591269860159367542`
- `scan reject_counts top=[('stale_edge', 157559), ('fees_eat_edge', 96969), ('crossed_book', 65231), ('venue_inventory', 37870), ('insufficient_overlapping_liquidity', 35887)]`
- `underwater_holdings_notional≈182.86`

Levers:

- Ablate maker floors vs observed fill/markout on live tape
- Separate 'can deploy' from 'has positive expectancy' experiments

### #6 [MEDIUM] `EXECUTION_HISTORY` (execution)

**Historical buy-fill gap / exchange errors reduced realized harvest**

Prior live_audit showed thousands of OKX clOrdId rejects and Bitvavo buy submits without fills. Fixes landed; current session still shows 0 new live trades — deadlock dominates now, but execution debt remains in cumulative realized (−€9.62).

Evidence:

- `bridge_realized=-9.622744997389643006513026041`
- `See docs/LIVE_EXECUTION_DIAGNOSIS_REPORT.md / live_execution_fixes PR`

Levers:

- Keep monitoring micro_order_exception rate after restart
- Do not attribute current €0/day mainly to clOrdId anymore

## 6. Recommended routes

1. Route B-lite (recommended first): unblock Util-B soft deploy without cut-loss (raise soft_block_underwater_eur; make ring momentum floor actually softer; measure fills/hour and NET/hour for 24–48h).
2. Route B-product: explicit cut-loss / bag-clear product decision to recycle ATOM/BNB/SOL underwater — frames realized loss vs continued idle cash.
3. Route A (CVD): only after shadow VALIDATED; do not treat paper CVD €3.4k as current live forecast (shadow currently NO-GO).
4. Reset expectation: until ring velocity >0 and paper/lab maker shows positive NET/hour, treat €50–100/day as unsupported for maker_inventory.

## 7. What this is *not*

- Not primarily a missing-coin / focus-list issue (see prior focus what-if).
- Not primarily current OKX clOrdId failures (historical; fixes landed).
- Not evidence that CVD research €212k extrapolates to this €2k pocket.

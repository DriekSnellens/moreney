# Ambition search — best structural / green-week packs

asof `2026-10-03`  book €20,000  wet Bitvavo next-open  
**1,354 daily-owner packs** × windows `fair_2y` / `last_12w` / `last_90d` + AlphaI overlays on real picks only.

## Hard truth

| Goal | Achieved? |
|---|---|
| Every week green on 90d / 2y | **No** — `all_weeks_green_count = 0` |
| Every week green on AlphaI overlap (3 weeks) | **Yes** — `btc50 + prefer/overlap @07` |
| Max total PnL | Yes, but with many red weeks |

“Elke week netto plus” is **not available** as a robust multi-month residual setting in this grid. Closest structural packs still have ~30–50% red weeks when chasing max PnL.

## Winners by objective

### Last 90 days (best recent structural sample)

| Objective | Pack | Green weeks | PnL | maxDD | Worst week |
|---|---|---:|---:|---:|---:|
| Highest green% (any) | `btc_hold` | **71.4%** | +€6,994 | 6.9% | −€1,052 |
| Best **alt** green% + profit | `btc50` trail10, lb20, **reb14**, sma20 | **64.3%** | **+€9,921** | **6.7%** | −€1,767 |
| Best PnL | residual_full-style `f0 lb10 sk0 tr10 fl3.5% rq+cash` (sma100 focus) | 28.6% | +€30,190 | 10.8% | −€6,058 |
| Low DD profit | `btc75` trail10 floor8% | 42.9% | +€11,609 | ~low | — |

### Fair ~2y

| Objective | Pack | Green weeks | PnL | maxDD |
|---|---|---:|---:|---:|
| Best PnL | residual_full-like `f0_lb10_sk0_tr10_fl0.035_all_rq1_c1` | 29.1% | **+€410,891** | 30.9% |
| Green% leader | `btc_hold` | 50% | +€5,088 | 51.7% |

Max-PnL residual is real on the long tape — and still only ~**29% green weeks**, with worst week **−€53k** on a compounded book. That is **not** “every week green”.

### AlphaI overlap only (13 days / 3 weeks — real picks)

| Pack | Green weeks | PnL | maxDD |
|---|---:|---:|---:|
| **`btc50__prefer07` / `overlap07`** | **100% (3/3)** | +€1,118 | 5.3% |
| `clip__intersect16` | 67% | +€1,808 | 2.4% |
| `residual_full__off` | 67% | +€6,948 | 10.7% |

## Recommended “best version” for your goals

You asked for **structural profit** and **weekly green**. In this search that points to:

1. **Primary book:** **~50% BTC / 50% residual alt**, trail **10%**, lookback **20**, rebalance **14d** (or 7d), SMA flatten — optionally AlphaI **prefer/overlap** (not hard gate).  
   - Recent 90d: ~**64% green weeks**, +€9.9k, DD ~7%.  
   - AlphaI window: **3/3 green** with prefer/overlap.

2. **Do not** use 100% alt if weekly green is the priority — it wins total PnL and loses the green-week race.

3. **Satellite / risk sleeve only:** residual_full (floor 3.5%, trail 10%, rq+cash) if you explicitly want max PnL and accept many red weeks.

## What this search did *not* fully retune

- 15m WR desk (different tape; prior research already set 7/13/16)  
- Ignition / top_day OKX sleeve (separate daily sniper tape)  
- Avoid-lists (not in pick_outcomes history)  
- Intraday sizing / partial exits  

Those need their own ambition grids; this pass owns the **daily Bitvavo residual/clip** family where most capital sits.

## Reproduce

```bash
.venv/bin/python -m bot.research.ambition_search --workers 5
```

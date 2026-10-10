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

## Last 3 months — daily PnL (simulated)

See **[`DAILY_3M.md`](./DAILY_3M.md)** (`2026-07-05` → `2026-10-03`, €20k wet next-open).

| Pack | Total | Avg day | Green days | Green weeks | Worst day |
|---|---:|---:|---:|---:|---:|
| **btc50 primary** | **+€9,921** | **+€109** | 56% (51/91) | 64% (9/14) | −€1,870 |
| Stack 70% btc50 + 15% residual_full | +€9,246 | +€102 | 54% | 50% (7/14) | −€1,923 |
| residual_full alone (skip0) | +€15,346 | +€169 | 25% | 36% | −€4,091 |

Note: an earlier draft of `DAILY_3M` accidentally coerced `skip_days=0`→`1` for residual_full (+€25k); corrected figures use true residual_full (`skip=0`).

## Cross-engine synthesis

Full capital stack (residual + ignition + 15m WR) with armed parameters:
**[`CROSS_ENGINE_BEST.md`](./CROSS_ENGINE_BEST.md)**.

Short version for “structural + green weeks + lots of capital”:

| Sleeve | Best pack | Notes |
|---|---|---|
| **Primary (most capital)** | **btc50** trail10 lb20 reb14 sma20 + AlphaI prefer/overlap | ~64% green / 90d; 3/3 on AlphaI window |
| Max-PnL satellite | residual_full floor 3.5% trail10 rq+cash | ~29% green weeks; huge red weeks |
| Ignition OKX | top_day trail8 + BTC SMA (or €10k/2-slot compound spikes) | Not every-week green; spike engine |
| 15m WR desk | hours 7/13/16, trail 5%, min_excess 2.5%, cap ≤€2k | Peak WR was 7+16 trail3%; shipped compromise is better ops |

Avoid-lists and intraday partials were not in this residual grid.

## Reproduce

```bash
.venv/bin/python -m bot.research.ambition_search --workers 5
```

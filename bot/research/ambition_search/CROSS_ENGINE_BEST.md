# Absolute best pack — all engines (structural / green weeks)

asof `2026-10-03`. Objective: **structural profit** and as many **green weeks/days** as the data allows — not max total PnL at any cost.

## Hard truth (every engine)

| Goal | Achieved in data? |
|---|---|
| Every calendar week green, multi-month | **No** — residual ambition `all_weeks_green_count=0`; ignition oracle ~27% of weeks even have a +50% forward signal |
| Every day green | **No** — not available as a residual/clip/ignition setting |
| Structural edge + majority green weeks | **Yes** — see primary book below |
| Max total PnL | **Yes** — residual_full, with ~29% green weeks and large red weeks |

“Elke dag/week netto plus” is **not** a tunable that this market + these engines can guarantee. Closest honest stack maximizes **green-week rate among profitable packs** and banks ignition spikes separately.

---

## Recommended capital stack (lots of capital)

| Sleeve | Role | Capital share (indicative) | Pack |
|---|---|---:|---|
| **A. Primary owner** | Structural PnL + green weeks | **60–75%** | **btc50** residual (below) |
| **B. Max-PnL satellite** | Accept red weeks for upside | **10–20%** | residual_full floor 3.5% / trail 10% / rq+cash |
| **C. Ignition OKX** | Spike / activity, not weekly paycheck | **10–15%** | top_day trail 8% + BTC SMA (or sniper coil for sparse) |
| **D. 15m WR desk** | Bitvavo leftover / satellite | **cap ≤ €2k** | hours 7/13/16, trail 5%, min_excess 2.5% |

Do **not** put the whole book in 100% alt if weekly green is the priority.

---

## A. Daily Bitvavo residual / clip (this branch — 1,354 packs)

Source: `ambition_search` wet next-open, €20k book, windows fair_2y / 12w / 90d + AlphaI real picks.

### Best for your stated goals (structural + green)

```
btc_frac          = 0.50
lookback_days     = 20
rebalance_days    = 14   # 7 also fine
trail_pct         = 0.10
floor             = 0.035–0.04
sma_n             = 20
require_alt_sma   = true / style with cash_when_no_alt
apply_alt_allow   = prefer or overlap_or_rs @ asof 07   # NOT hard gate
buy windows       = 7 / 13 / 16 UTC (minute < 8) when armed
```

| Window | Green weeks | PnL | maxDD |
|---|---:|---:|---:|
| Last 90d | **~64%** | **+€9.9k** | **~6.7%** |
| AlphaI 13d | **3/3** (prefer/overlap@07) | +€1.1k | 5.3% |

Pure `btc_hold` edges green% (~71% / 90d) but less alt upside.

### Best total PnL (accept many red weeks)

```
btc_frac=0  lookback=10  skip=0  trail=0.10  floor=0.035
flatten=all  require_alt_sma=1  cash_when_no_alt=1  sma focus 50–100
AlphaI: off, or intersect@16 if you want lower DD on pick history
```

| Window | Green weeks | PnL | maxDD |
|---|---:|---:|---:|
| Last 90d | 28.6% | +€30k | 10.8% |
| Fair ~2y | 29.1% | +€411k | 30.9% |
| AlphaI 13d off | 67% | +€6.9k | 10.7% |

Hard AlphaI gate@07 on residual_full: **−€1k** on the same sample — avoid.

Calmar / owner-tournament champion (different objective): **btc20 / alt80**, floor 4%, lb10, trail 10%, reb7, SMA50.

---

## B. Ignition / top_day (OKX) — sibling lab `ignition-engine-tune`

Source: `WEEKLY_AMBITION`, `DAILY_SLEEVE`, `EXPAND_GRID`, hybrid coil (not on this branch’s source tree; results from sibling).

### Activity / balanced daily sleeve (live-aligned)

```
entry_mode        = top_day
universe_mode     = ex_desk
trail_pct         = 0.08
time_max_days     = 2
require_btc_sma   = true
max_positions     = 1
book              = €2k default (compound optional)
buy hours         = 7 / 13 / 16 UTC
```

Wet €2k: ~+€10k full / +€9k 2026 / +€9k 180d; entry-days ~19%; maxDD ~54%.  
Without BTC SMA: higher PnL, **~88% DD** — skip for structural goals.

### Spike ambition (bank big weeks, not every week)

```
book_eur=10000  max_positions=2  compound_sizing=true
coil_hybrid + coil_trail=0.25  (or classic trail12 + ratchet 30→10)
universe ex_desk quiet_max=0.15
```

€2–3k **every** week is **not** in the data; maximize spike-week count instead.

---

## C. 15m WR / momentum desk

Source: `artifacts/wr_config_search.json` (135d, ~395 configs) + live Settings defaults.

### Peak WR (research)

```
decision_hours_utc = 7,16
min_excess         = 0.025
trail_pct          = 0.03
trail_tight_after  = 0.04 → trail_tight_pct 0.02
hard_stop_pct      = 0.03
time_exit_hours    = 36
green_deadline     = off
```

~**54% WR**, +€951, DD ~−€149 (live-scale clip).

### Shipped compromise (live defaults — prefer this)

```
decision_hours_utc = 7,13,16     # +midday EU, WR ~52.5% / +€888
trail_pct          = 0.05        # fixed; ratchet off (live research winner)
hard_stop_pct      = 0.03
time_exit_hours    = 36
Bitvavo satellite cap ≈ €2k
```

No separate green-week ambition grid on 15m; treat as capped satellite, not the owner book.

---

## AlphaI usage (all engines)

| Mode | When |
|---|---|
| **prefer / overlap_or_rs** @07 | Primary btc50 book — green-week winner on real picks |
| **intersect** @16 | residual_full if you want lower DD on pick history |
| **hard gate** @07 | Avoid on residual_full (missed NEAR moonshot → red) |
| **override_gate ≥20%** | Only if you insist on moonshot RS bypass; same PnL as off when excess is huge |

FET was never an AlphaI pick — hard gate would have blocked it; that does not make hard gate “best”.

---

## What to arm for “absoluut beste” under your goals

1. **Move primary capital off residual_full** → **btc50** trail10 / lb20 / reb14 / sma20 + AlphaI prefer/overlap.  
2. Keep residual_full only as a **sized satellite** if you still want max alt upside.  
3. Ignition: keep **top_day + trail8 + BTC SMA** on OKX; do not expect weekly paycheck.  
4. Desk: leave **7/13/16 + trail5%**, capped.  
5. Accept that **0 packs** hit 100% green weeks on 90d/2y — the edge is structural majority-green + spike banking, not a calendar guarantee.

Reproduce residual grid:

```bash
.venv/bin/python -m bot.research.ambition_search --workers 5
```

See also: `RESULTS.md`, `alphai_gate_lab/RESULTS.md`, `alphai_pack_compare/RESULTS.md`.

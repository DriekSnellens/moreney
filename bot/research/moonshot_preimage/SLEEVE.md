# €1.700 daily-green sleeve

Vaste small book naast de owner residual/clip. **Doel:** elke risk-on dag actief, zo structureel mogelijk groen — over **alle liquid coins**.

## Optimize verdict (walk-forward)

7680 packs · 80 names · fixed €1700 sizing · IS `2024-06→2025-12` / OOS `2026`.

Score = groene weken + activity − DD. Winner moet **IS én OOS** positief zijn (geen pure spike-fit).

| Pack | Rol | OOS €/wk | OOS greenW | OOS DD | IS €/wk | Full pnl | Full DD |
|---|---|---:|---:|---:|---:|---:|---:|
| **`brk20_day_t12_h5_hs5_btc1`** | **armed** | +317 | 48% | 40% | **+6** | **+€13.2k** | **55%** |
| `brk20_day_t15_h2_hs0_btc0` | blend-max (verworpen) | +333 | 65% | 26% | −10 | +€12.5k | 94% |
| old `coil_day_t12_h5` | baseline | — | — | — | — | +€10.6k | 172% |

Zie [`OPTIMIZE.md`](../daily_green_lab/OPTIMIZE.md) · [`LAST_6W.md`](LAST_6W.md) · [`LAST_FULL.md`](LAST_FULL.md).

## Armed pack (`daily_brk20_day`)

```
book_eur        = 1700
universe        = EXPAND_LIQUID_UNIVERSE (~80)
entry_mode      = brk20_day   # close ≥ prior 20d high
excess_floor    = 0.0
rebalance_days  = 1
alt_trail_pct   = 0.12
hard_stop_pct   = 0.05
time_max_days   = 5
btc_frac        = 0
cash_when_no_alt= true
size_to_book    = true
min_qvol_eur    = 50000
BTC filter      = SMA50 (risk-off → cash)
```

Laatste 6w (fixed book): **+€12.0k** · 86% groene weken · maxDD 20%.

## Arm

```bash
MOMENTUM_MOONSHOT_CLIP_ENABLED=true
MOMENTUM_MOONSHOT_CLIP_ALLOW_LIVE=false
MOMENTUM_MOONSHOT_CLIP_BOOK_EUR=1700
curl -X POST /live/momentum/moonshot-clip/start
```

Reproduce:

```bash
.venv/bin/python -m bot.research.daily_green_lab --optimize
```

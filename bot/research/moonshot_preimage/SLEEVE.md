# €1.700 daily-green sleeve

Vaste small book naast de owner residual/clip. **Doel:** elke risk-on dag actief, zo structureel mogelijk groen.

## Lab verdict (`daily_green_lab`, full universe)

| Claim | Resultaat |
|---|---|
| Alle liquid coins (niet enkel desk) | **Ja** — `data/ignition_expand_candles` · **80** names |
| Alle entry × exit packs | **Ja** — **8000** packs (RS / day / breakout / coil / vol / AlphaI × trail / time / stop / BTC-SMA) |
| Elke dag **+€100** op €1.7k (~5.9%/dag) | **Niet structureel** — winner avg ~**€319/dag** maar gedreven door enkele grote movers (LSK / USELESS / PUMP) |
| Elke dag in de markt + max groen | **coil_day** trail **12%**, hold ≤**5d**, day≥**2%**, BTC>SMA50 |
| AlphaI als oracle | **Nee** — `alphai1` ~vlak; structure/day-winners winnen |

Recent 45d winner: `coil_day_t12_h5_hs0_btc1_fl0.02` · avg **~€319/dag** · **~57%** groene dagen · **~74%** in-markt · 16/46 dagen ≥€100 · DD ~6%.

AlphaI-overlap (14d): zelfde coil familie ~**€87/dag**, **71%** groen; pure AlphaI-picks blijven zwak.

Runner-up families (full universe): `top_day` / `brk20_day` / `brk20_day6_vol2` — ook sterk; desk-only RS packs blijven lager.

## Armed pack (`daily_coil_day`)

```
book_eur        = 1700
universe        = EXPAND_LIQUID_UNIVERSE (~80)
entry_mode      = coil_day   # compressie + day thrust
excess_floor    = 0.02       # min day return na coil
rebalance_days  = 1
alt_trail_pct   = 0.12
time_max_days   = 5
btc_frac        = 0
cash_when_no_alt= true
size_to_book    = true
min_qvol_eur    = 50000
BTC filter      = SMA50 (risk-off → cash)
```

## Arm

```bash
MOMENTUM_MOONSHOT_CLIP_ENABLED=true
MOMENTUM_MOONSHOT_CLIP_ALLOW_LIVE=false
MOMENTUM_MOONSHOT_CLIP_BOOK_EUR=1700
curl -X POST /live/momentum/moonshot-clip/start
```

## Laatste 6 weken (wet)

Zie [`LAST_6W.md`](LAST_6W.md): `2026-08-21` → `2026-10-02` · PnL **+€13.5k** (~+791%) · 49% groen · maxDD 14.9%.  
Gedreven door USELESS (+€3.0k) en LSK (+€9.9k); zonder die twee trades is het pad veel vlakker.

Reproduce lab:

```bash
.venv/bin/python -m bot.research.daily_green_lab --candles data/ignition_expand_candles --broad --book 1700 --days 45
```

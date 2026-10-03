# €1.700 daily-green sleeve

Vaste small book naast de owner residual/clip. **Doel:** elke risk-on dag actief, zo structureel mogelijk groen.

## Lab verdict (`daily_green_lab`, 640 packs)

| Claim | Resultaat |
|---|---|
| Elke dag **+€100** op €1.7k (~5.9%/dag) | **Niet gehaald** — beste avg ~**€45/dag** |
| Elke dag in de markt + max groen | **top_day** trail **12%**, hold ≤**3d**, BTC>SMA50 |
| AlphaI als oracle | **Nee** — `alphai1` ~vlak; RS/top_day wint |

Recent 45d winner family: `top_day` · avg **~€32/dag** · **~50%** groene dagen · **~74%** in-markt · 9/46 dagen ≥€100.

AlphaI-overlap (13d): `top_rs10` iets sterker (~€40/dag, 60% groen); pure AlphaI-picks niet.

## Armed pack (`daily_top_day`)

```
book_eur        = 1700
entry_mode      = top_day      # sterkste liquid 1d mover
rebalance_days  = 1
alt_trail_pct   = 0.12
time_max_days   = 3
btc_frac        = 0
cash_when_no_alt= true
size_to_book    = true
BTC filter      = SMA50 (risk-off → cash)
```

## Arm

```bash
MOMENTUM_MOONSHOT_CLIP_ENABLED=true
MOMENTUM_MOONSHOT_CLIP_ALLOW_LIVE=false
MOMENTUM_MOONSHOT_CLIP_BOOK_EUR=1700
curl -X POST /live/momentum/moonshot-clip/start
```

Reproduce lab:

```bash
.venv/bin/python -m bot.research.daily_green_lab --book 1700 --days 45
```

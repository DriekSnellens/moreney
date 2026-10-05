# Continuous daily sleeve — green-day search

asof `2026-10-05T14:09:11.908234+00:00`  book €1,700  bases **81**  specs `2520`  robust `110`

Aparte sleeve naast residual: **continu kopen/verkopen** (dagelijkse rotate of exit→rebuy ≠ sold), fixed €1.7k, expand ~80 coins.

**Eerlijk:** structureel **elke dag groen is niet beschikbaar** op deze tape. Beste OOS green-day rates liggen rond **40–45%** (actieve dagen ~45%). Armed residual-achtige brk20-sleeve scoort OOS maar ~22% groene dagen.

## Aanbevolen (dual IS+OOS, continuous)

**`dai_top_day_t10_h3_hs3_btc0_fl0.00`** — mode `daily_rotate`

```
mode            = daily_rotate   # daily switch to top pick
entry           = top_day
trail_pct       = 0.1
time_max_days   = 3
hard_stop_pct   = 0.03
require_btc_sma = False
excess_floor    = 0.0
book_eur        = 1700
size_to_book    = true
```

| Window | Green days | In-mkt | avg €/day | PnL | maxDD |
|---|---:|---:|---:|---:|---:|
| IS | 35% | 57% | +0.6 | +322 | 56.4% |
| OOS | **42%** | 56% | +23.4 | +6438 | 51.4% |
| Full | 37% | 57% | +8.2 | +6926 | 61.2% |
| Last 6w | **56%** | 53% | +137.3 | +5899 | 12.6% |

## Max OOS green-day (fragieler IS)

**`exi_coil_day_t8_h3_hs0_btc0_fl0.00`** mode `exit_rebuy` — OOS green **45%** / DD 25.2% maar IS DD kan >100% (spike-pad). Niet armed.

## Modes

| Mode | Idee | Beste robust | OOS greenD |
|---|---|---|---:|
| `daily_rotate` | elke dag top pick | `dai_top_day_t10_h3_hs3_btc0_fl0.00` | 42% |
| `exit_rebuy` | hold tot exit, meteen andere coin | `exi_coil_day_t8_h3_hs0_btc0_fl0.00` | 45% |

## Top robust (green-day score)

| Pack | OOS greenD | IS greenD | OOS DD | OOS €/day |
|---|---:|---:|---:|---:|
| `exi_coil_day_t8_h3_hs0_btc0_fl0.00` | 45% | 34% | 25.2% | +15.5 |
| `exi_coil_day_t10_h3_hs0_btc0_fl0.00` | 43% | 34% | 28.6% | +20.7 |
| `dai_top_day_t10_h3_hs3_btc0_fl0.00` | 42% | 35% | 51.4% | +23.4 |
| `exi_coil_day_t8_h3_hs0_btc0_fl0.02` | 41% | 34% | 37.3% | +8.6 |
| `dai_top_day_t12_h3_hs3_btc0_fl0.00` | 41% | 35% | 47.3% | +40.6 |
| `dai_top_day_t10_h3_hs3_btc0_fl0.02` | 41% | 35% | 54.0% | +22.7 |
| `dai_top_day_t8_h3_hs3_btc0_fl0.00` | 41% | 35% | 51.7% | +23.4 |
| `exi_top_day_t5_h3_hs0_btc0_fl0.00` | 39% | 37% | 44.0% | +12.7 |
| `exi_top_day_above_sma20_t10_h2_hs5_btc0_fl0.00` | 42% | 34% | 54.1% | +10.2 |
| `exi_top_day_above_sma20_t12_h2_hs5_btc0_fl0.00` | 42% | 34% | 54.1% | +8.8 |
| `dai_top_day_t12_h3_hs3_btc0_fl0.02` | 41% | 34% | 50.5% | +39.9 |
| `exi_top_day_t8_h3_hs0_btc0_fl0.00` | 40% | 35% | 51.0% | +21.5 |
| `dai_top_day_t8_h3_hs3_btc0_fl0.02` | 41% | 34% | 54.4% | +22.7 |
| `exi_coil_day_t12_h3_hs5_btc0_fl0.00` | 42% | 34% | 29.8% | +15.0 |
| `dai_top_day_t5_h3_hs3_btc0_fl0.00` | 41% | 35% | 45.1% | +24.5 |

## vs huidige paper moonshot (`brk20_day` armed)

| Pack | OOS green days | OOS pnl | Rol |
|---|---:|---:|---|
| continuous `top_day` daily_rotate (aanbevolen) | ~42% | +€6.4k | continuous green-day sleeve |
| paper `brk20_day_t12_h5_hs5` | ~22% | +€12.6k | spike/PnL sleeve |

Voor **elke dag actief + zo groen mogelijk**: continuous `top_day` daily_rotate. Voor **max €**: blijf bij brk20 / residual — dat is een ander doel.

```bash
.venv/bin/python -m bot.research.daily_green_lab.continuous
```

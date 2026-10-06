# +8% precisie — zoektocht naar 70%

asof `2026-10-06T06:03:26.136411+00:00`  rijen `48668`  bases **80**

Label: hoogste koers binnen de horizon / volgende opening − 1 ≥ +8%.
Koop op de volgende opening. Features van de slotdag, plus de gap die bij de opening bekend is.
Fit ≤ 2024-12-31 · select = 2025 · test ≥ 2026-01-01.
De regel wordt gekozen op select (Wilson), en alleen als de fit óók ≥ 70% haalt.
2026 wordt daarna één keer gemeten.
Een tweede vraag is of +8% vóór een −5% stop wordt geraakt (`tp`), met dezelfde kosten als de TP8-sleeve.

Minimale steekproef per venster: **80**.

## high ≥ +8% vanaf de opening

| Horizon | Fit basiskans | Select basiskans | Test basiskans |
|---|---|---|---|
| 1d | 14.6% (n=10470) | 13.1% (n=21550) | 12.4% (n=16648) |
| 3d | 35.1% (n=10470) | 31.0% (n=21550) | 28.6% (n=16648) |
| 5d | 46.1% (n=10470) | 40.8% (n=21550) | 38.2% (n=16648) |

**70% niet gehouden.** horizon 5d · `score>=0.857`

| Venster | High ≥ +8% | Slot ≥ +8% | +8% vóór stop | Gem. trade |
|---|---|---|---|---|
| Fit | 79.0% (n=105, wilson≥70.3%) | 56.2% | 48.6% | +0.91% |
| Select | 73.3% (n=315, wilson≥68.2%) | 41.3% | 41.0% | -0.08% |
| Test 2026 | 69.2% (n=370, wilson≥64.3%) | 35.9% | 35.9% | -0.72% |

Hoogste select-precisie per horizon (test niet gebruikt om te kiezen):

| Horizon | Regel | Fit | Select |
|---|---|---|---|
| 1d | `score>=0.544` | 50.5% (n=105) | 45.7% (n=381, wilson≥40.7%) |
| 1d | `score>=0.435` | 43.3% (n=282) | 41.0% (n=691, wilson≥37.3%) |
| 1d | `score>=0.376` | 38.8% (n=459) | 37.3% (n=985, wilson≥34.3%) |
| 1d | `score>=0.340` | 36.5% (n=636) | 34.8% (n=1296, wilson≥32.3%) |
| 1d | `score>=0.312` | 35.2% (n=813) | 33.2% (n=1558, wilson≥30.9%) |
| 3d | `r1>=0.03 + up_vol>=0.10 + xs10>=0.15` | 72.6% (n=84) | 69.1% (n=97, wilson≥59.3%) |
| 3d | `up_vol>=0.12 + xs10>=0.25` | 70.4% (n=81) | 68.1% (n=94, wilson≥58.1%) |
| 3d | `r1<=0.08 + r3<=0.05 + up_vol>=0.08 + xs10>=0.25` | 72.2% (n=115) | 67.8% (n=118, wilson≥58.9%) |
| 3d | `r3<=0.05 + up_vol>=0.08 + xs10>=0.25` | 71.8% (n=124) | 67.4% (n=129, wilson≥59.0%) |
| 3d | `r1>=0.03 + up_vol>=0.10 + xs10>=0.05` | 70.0% (n=90) | 67.3% (n=113, wilson≥58.2%) |
| 5d | `btc_day_down + up_vol>=0.10 + xs10>=0.15` | 75.8% (n=91) | 74.8% (n=111, wilson≥66.0%) |
| 5d | `score>=0.857` | 79.0% (n=105) | 73.3% (n=315, wilson≥68.2%) |
| 5d | `up_vol>=0.12 + xs10>=0.15` | 71.8% (n=85) | 72.2% (n=126, wilson≥63.8%) |
| 5d | `up_vol>=0.10 + xs10>=0.15` | 72.6% (n=197) | 72.1% (n=226, wilson≥65.9%) |
| 5d | `btc_day_down + up_vol>=0.08 + xs10>=0.25` | 76.1% (n=176) | 72.1% (n=136, wilson≥64.0%) |

## +8% vóór een −5% stop

| Horizon | Fit basiskans | Select basiskans | Test basiskans |
|---|---|---|---|
| 1d | 13.5% (n=10470) | 12.1% (n=21550) | 11.4% (n=16648) |
| 3d | 28.0% (n=10470) | 25.3% (n=21550) | 24.2% (n=16648) |
| 5d | 33.1% (n=10470) | 30.1% (n=21550) | 30.0% (n=16648) |

**70% niet gehouden.** horizon 3d · `boom: up_vol<=0.027 en r5<=-0.176`

| Venster | +8% vóór stop | Slot ≥ +8% | +8% vóór stop | Gem. trade |
|---|---|---|---|---|
| Fit | 62.6% (n=91, wilson≥52.4%) | 58.2% | 62.6% | +3.23% |
| Select | 59.8% (n=102, wilson≥50.1%) | 64.7% | 59.8% | +3.05% |
| Test 2026 | 30.2% (n=126, wilson≥22.8%) | 23.0% | 30.2% | -0.42% |

Hoogste select-precisie per horizon (test niet gebruikt om te kiezen):

| Horizon | Regel | Fit | Select |
|---|---|---|---|
| 1d | `score>=0.476` | 41.0% (n=105) | 33.9% (n=387, wilson≥29.3%) |
| 1d | `score>=0.385` | 37.6% (n=282) | 31.7% (n=706, wilson≥28.4%) |
| 1d | `score>=0.333` | 33.6% (n=459) | 29.3% (n=1030, wilson≥26.6%) |
| 1d | `score>=0.303` | 31.6% (n=636) | 28.2% (n=1324, wilson≥25.9%) |
| 1d | `score>=0.280` | 31.0% (n=813) | 27.1% (n=1581, wilson≥25.0%) |
| 3d | `boom: up_vol<=0.027 en r5<=-0.176` | 62.6% (n=91) | 59.8% (n=102, wilson≥50.1%) |
| 3d | `score>=0.576` | 61.0% (n=105) | 39.7% (n=471, wilson≥35.4%) |
| 3d | `btc_day_down + btc_off + r3<=0.05 + volx>=3.0` | 70.6% (n=85) | 35.8% (n=151, wilson≥28.6%) |
| 3d | `btc_day_down + btc_off + r1<=0.02 + volx>=3.0` | 70.4% (n=81) | 34.9% (n=146, wilson≥27.7%) |
| 3d | `btc_day_down + btc_off + loc20<=0.50 + volx>=3.0` | 70.2% (n=84) | 34.8% (n=161, wilson≥27.9%) |
| 5d | `boom: up_vol<=0.026 en r5<=-0.159` | 58.5% (n=135) | 52.2% (n=134, wilson≥43.8%) |
| 5d | `btc_day_down + btc_off + r3<=0.05 + volx>=3.0` | 71.8% (n=85) | 41.1% (n=151, wilson≥33.5%) |
| 5d | `btc_day_down + btc_off + r1<=0.02 + volx>=3.0` | 71.6% (n=81) | 40.4% (n=146, wilson≥32.8%) |
| 5d | `btc_off + loc20<=0.50 + r1<=0.02 + volx>=3.0` | 72.0% (n=82) | 39.9% (n=163, wilson≥32.7%) |
| 5d | `btc_day_down + btc_off + loc20<=0.50 + volx>=3.0` | 71.4% (n=84) | 39.8% (n=161, wilson≥32.5%) |

Reproduce:

```bash
.venv/bin/python -m bot.research.daily_green_lab.hit8_precision
```

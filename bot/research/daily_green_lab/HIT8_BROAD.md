# Bredere zoektocht naar +8%

asof `2026-10-06T08:43:04.680581+00:00`

De score leert op 2024. De drempel kiest 2025, op de hoogste trefzekerheid met minstens 80 signalen in fit en in select, en een fit-trefzekerheid van minstens 45%.
2026 wordt per vraag één keer gemeten, op de gekozen drempel.
Dagfeatures zijn de eerdere set plus kaarslichaam, lonten, dagen sinds de 20d-high, en de rang binnen de dag.
De 4u-tape is elke 4 uur, entry op de volgende 4u-opening.
Paren gebruiken de fit-drivers. Hun drempels zijn fit-kwantielen. 2025 kiest het paar, los van de score.
Het ene slot vult een boek van €2000 per trade. De exit stond vast: take-profit +8%, harde stop 5%, anders de slotkoers aan het eind van de horizon. Kosten 0,15% en 0,1% slippage per kant.

## dag

### dag 1d

Basiskans 13.2% (n=48668). Kandidaten `113`.

`dag 1d score>=0.583`

| Venster | Trefzekerheid |
|---|---|
| Fit | 94.4% (n=213, wilson≥90.4%) |
| Select 2025 | 41.9% (n=191, wilson≥35.1%) |
| Test 2026 | niet gemeten; deze horizon haalde niet de hoogste select-trefzekerheid |

Sterkste score-drivers op de fit-steekproef: btc_r1 +0.070, up_vol +0.063, loc20 +0.022, r10 +0.019, dayloc +0.016, volx +0.015.

### dag 3d

Basiskans 31.1% (n=48668). Kandidaten `110`.

`dag 3d score>=0.904`

| Venster | Trefzekerheid |
|---|---|
| Fit | 100.0% (n=213, wilson≥98.2%) |
| Select 2025 | 62.0% (n=142, wilson≥53.8%) |
| Test 2026 | niet gemeten; deze horizon haalde niet de hoogste select-trefzekerheid |

Sterkste score-drivers op de fit-steekproef: btc_r1 +0.101, up_vol +0.039, volx +0.020, coil +0.015, lower_wick +0.014, dayloc +0.014.

### dag 5d

Basiskans 41.1% (n=48668). Kandidaten `111`.

`dag 5d score>=0.939`

| Venster | Trefzekerheid |
|---|---|
| Fit | 99.5% (n=213, wilson≥97.4%) |
| Select 2025 | 63.3% (n=98, wilson≥53.4%) |
| Test 2026 | 53.2% (n=47, wilson≥39.2%) |

Sterkste score-drivers op de fit-steekproef: btc_r1 +0.129, up_vol +0.050, xs10 +0.020, r3 +0.019, volx +0.018, xs3 +0.015.

### Eén slot op de gekozen score

| Venster | Trades | Trefzekerheid | TP-fill | Mediaan | PnL |
|---|---|---|---|---|---|
| Fit | 27 | 100.0% (wilson≥87.5%) | 77.8% | +7.59% | +€2,541 |
| Select 2025 | 31 | 71.0% (wilson≥53.4%) | 41.9% | -5.39% | +€259 |
| Test 2026 | 22 | 50.0% (wilson≥30.7%) | 31.8% | -5.40% | −€556 |

### Leesbare paren

#### dag 1d

Kandidaten `681`.

`up_vol>=0.074 + volx>=2.062`

| Venster | Trefzekerheid |
|---|---|
| Fit | 45.7% (n=116, wilson≥36.9%) |
| Select 2025 | 48.5% (n=130, wilson≥40.0%) |
| Test 2026 | niet gemeten; deze horizon haalde niet de hoogste select-trefzekerheid |

#### dag 3d

Kandidaten `681`.

`up_vol>=0.074 + volx>=2.062`

| Venster | Trefzekerheid |
|---|---|
| Fit | 59.5% (n=116, wilson≥50.4%) |
| Select 2025 | 69.2% (n=130, wilson≥60.8%) |
| Test 2026 | niet gemeten; deze horizon haalde niet de hoogste select-trefzekerheid |

#### dag 5d

Kandidaten `681`.

`btc_r1<=-0.030 + lower_wick>=0.048`

| Venster | Trefzekerheid |
|---|---|
| Fit | 77.2% (n=189, wilson≥70.8%) |
| Select 2025 | 78.0% (n=250, wilson≥72.5%) |
| Test 2026 | 36.3% (n=135, wilson≥28.7%) |

Eén slot op het gekozen paar, gerangschikt op relatieve kracht.

| Venster | Trades | Trefzekerheid | TP-fill | Mediaan | PnL |
|---|---|---|---|---|---|
| Fit | 16 | 56.2% (wilson≥33.2%) | 25.0% | -5.40% | −€687 |
| Select 2025 | 21 | 81.0% (wilson≥60.0%) | 47.6% | -5.39% | +€332 |
| Test 2026 | 15 | 53.3% (wilson≥30.1%) | 33.3% | -5.39% | −€320 |

## 4u

Rijen `255658`.

### 4u 24u

Basiskans 14.1% (n=255658). Kandidaten `90`.

`4u 24u score>=0.656`

| Venster | Trefzekerheid |
|---|---|
| Fit | 93.5% (n=1067, wilson≥91.9%) |
| Select 2025 | 46.4% (n=1061, wilson≥43.4%) |
| Test 2026 | niet gemeten; deze horizon haalde niet de hoogste select-trefzekerheid |

Sterkste score-drivers op de fit-steekproef: btc_r6 +0.062, r30 +0.058, xs6 +0.039, dist120 +0.039, xs18 +0.036, r1 +0.036.

### 4u 3d

Basiskans 31.9% (n=255658). Kandidaten `91`.

`4u 3d score>=0.977`

| Venster | Trefzekerheid |
|---|---|
| Fit | 99.2% (n=264, wilson≥97.3%) |
| Select 2025 | 87.7% (n=325, wilson≥83.7%) |
| Test 2026 | 79.3% (n=58, wilson≥67.2%) |

Sterkste score-drivers op de fit-steekproef: r30 +0.072, btc_r6 +0.062, dist120 +0.061, xs6 +0.047, r18 +0.043, xs18 +0.036.

### Eén slot op de gekozen score

| Venster | Trades | Trefzekerheid | TP-fill | Mediaan | PnL |
|---|---|---|---|---|---|
| Fit | 10 | 90.0% (wilson≥59.6%) | 60.0% | +7.59% | +€479 |
| Select 2025 | 11 | 81.8% (wilson≥52.3%) | 54.5% | +7.59% | +€372 |
| Test 2026 | 6 | 0.0% (wilson≥0.0%) | 0.0% | -5.40% | −€647 |

### Samenloop

Een episode is het eerste signaal van dezelfde munt. Een nieuwe episode begint pas na de horizon.

| Venster | Signalen | Unieke bars | Max op één bar | Episodes | Trefzekerheid eerste |
|---|---|---|---|---|---|
| Fit | 264 | 13 | 58 | 168 | 98.8% |
| Select 2025 | 325 | 23 | 55 | 161 | 91.3% |
| Test 2026 | 58 | 8 | 20 | 38 | 81.6% |

Eerste 2026-signalen van de score: 2026-01-31 ALICE raak, 2026-01-31 EIGEN mis, 2026-01-31 GRASS raak, 2026-01-31 NOM mis, 2026-01-31 PENGU raak, 2026-01-31 RAY mis, 2026-01-31 USELESS raak, 2026-01-31 VIRTUAL mis.

### Leesbare paren

#### 4u 24u

Kandidaten `211`.

`btc_r6<=-0.028 + r18>=0.148`

| Venster | Trefzekerheid |
|---|---|
| Fit | 46.3% (n=149, wilson≥38.5%) |
| Select 2025 | 55.7% (n=167, wilson≥48.1%) |
| Test 2026 | niet gemeten; deze horizon haalde niet de hoogste select-trefzekerheid |

#### 4u 3d

Kandidaten `211`.

`btc_r6<=-0.028 + r18>=0.148`

| Venster | Trefzekerheid |
|---|---|
| Fit | 61.7% (n=149, wilson≥53.7%) |
| Select 2025 | 69.5% (n=167, wilson≥62.1%) |
| Test 2026 | 62.4% (n=165, wilson≥54.8%) |

Eén slot op het gekozen paar, gerangschikt op relatieve kracht.

| Venster | Trades | Trefzekerheid | TP-fill | Mediaan | PnL |
|---|---|---|---|---|---|
| Fit | 24 | 45.8% (wilson≥27.9%) | 37.5% | -5.39% | −€255 |
| Select 2025 | 59 | 79.7% (wilson≥67.7%) | 42.4% | -5.39% | +€125 |
| Test 2026 | 66 | 71.2% (wilson≥59.4%) | 39.4% | -5.39% | −€368 |

Reproduce:

```bash
.venv/bin/python -m bot.research.daily_green_lab.hit8_broad
```

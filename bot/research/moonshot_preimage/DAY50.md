# 1-dag +50% sleeve

asof `2026-10-05T13:38:29.258891+00:00`  book €1,700  bases **80**  specs `11904`

Walk-forward accuracy = hoogste train-Wilson op packs met train-PnL>0, ≥3 hits en ≥12 trades. Walk-forward PnL = hoogste train-PnL met ≥12 trades en train-DD≤70%. Best test / best full zijn de maxima van de grid (zoekmaxima, geen schone keuze). Best full met train-PnL>0 is het hoogste full-pad dat in-sample ook geld verdiende.

Label: entry session high / open - 1 >= +50% (signal = prior close).
Kosten: 15 bp fee/side, 10 bp slip, vaste book-cap (winst schaalt het ticket niet boven €book).
Eén slot. Signaal op de slotkoers, koop de volgende open. Geen munt-specifieke regels.

## Kort

Basiskans dat de volgende sessie +50% vanaf de open handelt: **0.3%** in 2026 (51 hits op 16951 signalen). Geen gate haalt 40% precisie met n≥10.
Nauwkeurigste gate met test n≥20: `r1_35_vol1.5_loc0` — P50 16.3% (n=43, lift×54.27).
Walk-forward accuracy-sleeve: `r3_40_xs15_trend` / `tp50_hs12_d1` — full **€+2,804**, 2026 €+594, precisie 17.1% op de test.
Walk-forward PnL-sleeve: `r1_18_vol1.5_loc0` / `tp100_hs20_d3` — full **€+13,732**, 2026 €+7,847.
Hoogste full-PnL die t/m 2025 ook positief was: **€+17,080** (`r1_18_vol2.5_loc0__vol__btc0__gapNA__trail30_hs15_d4`).
Hoogste 2026-PnL in de grid: **€+19,894** (`r1_18_vol2.5_loc0__rs__btc1__gapNA__trail30_hs15_d4`), train €-934.
Hoogste full-PnL in de grid: **€+17,770** (`r1_18_vol2.5_loc0__rs__btc1__gapNA__trail30_hs15_d4`).
Plafond bij een perfecte +50%-limit elke event-dag, zonder verliezen: €+73,959 over het hele pad.

## Basiskans

Een willekeurige liquide naam, volgende sessie, high t.o.v. de open:

| Split | signalen | P(high ≥ +50%) | P(close ≥ +50%) | dagen met ≥1 hit |
|---|---:|---:|---:|---:|
| train ≤2025-12-31 | 31787 | 0.2% | 0.1% | 47 |
| test 2026+ | 16951 | 0.3% | 0.1% | 43 |
| full | 48738 | 0.2% | 0.1% | 90 |

## Plafond (perfecte picker, geen verliezen)

Als het ene slot elke dag precies de munt pakt die die sessie +50% vanaf de open handelt, en de limit op +50% vult: **€+822 per hit**.

| Window | event-dagen | plafond-PnL |
|---|---:|---:|
| train | 47 | €+38,623 |
| test 2026 | 43 | €+35,336 |
| full | 90 | €+73,959 |

Dat plafond telt geen stop-outs en geen dagen waarop de +50% alleen een wick in een rode candle was.

## Nauwkeurigheid van het signaal

Hier telt élk signaal, niet alleen het ene slot. Test-precisie, n≥20.

Beste gate (test n≥20): **`r1_35_vol1.5_loc0`** → P50 **16.3%** (n=43, lift×54.27, wilson≥8.1%). Train P50 4.1% (n=49).

Geen gate haalt 40% test-precisie met n≥10. +50% in één dag blijft een zeldzame staart.

| Gate | train n | train P50 | test n | test P50 | test P(close) | lift× |
|---|---:|---:|---:|---:|---:|---:|
| `r1_35_vol4.0_loc75` | 28 | 3.6% | 11 | 27.3% | 18.2% | 90.9 |
| `r1_35_vol2.5_loc75` | 36 | 2.8% | 15 | 20.0% | 13.3% | 66.67 |
| `r1_35_vol1.5_loc75` | 38 | 2.6% | 16 | 18.8% | 12.5% | 62.5 |
| `r1_35_vol1.5_loc0` | 49 | 4.1% | 43 | 16.3% | 9.3% | 54.27 |
| `climax_vol5_r1_15` | 46 | 2.2% | 26 | 15.4% | 11.5% | 51.27 |
| `r1_35_vol2.5_loc0` | 47 | 4.3% | 40 | 15.0% | 7.5% | 50.0 |
| `r1_35_vol4.0_loc0` | 39 | 5.1% | 34 | 14.7% | 5.9% | 49.03 |
| `r1_25_vol4.0_loc75` | 46 | 4.3% | 26 | 11.5% | 7.7% | 38.47 |
| `r3_40_xs40_trend` | 86 | 5.8% | 71 | 11.3% | 5.6% | 37.57 |
| `climax_vol5_r1_5` | 73 | 1.4% | 36 | 11.1% | 8.3% | 37.03 |
| `climax_vol8_r1_15` | 18 | 5.6% | 9 | 11.1% | 11.1% | 37.03 |
| `r1_18_vol4.0_loc0` | 128 | 2.3% | 95 | 10.5% | 6.3% | 35.1 |
| `r1_25_vol2.5_loc75` | 70 | 2.9% | 38 | 10.5% | 7.9% | 35.1 |
| `r1_25_vol2.5_loc0` | 104 | 2.9% | 77 | 10.4% | 5.2% | 34.63 |
| `r3_40_xs25_trend` | 103 | 4.9% | 78 | 10.3% | 5.1% | 34.2 |
| `r1_25_vol1.5_loc0` | 122 | 2.5% | 89 | 10.1% | 5.6% | 33.7 |
| `r1_18_vol4.0_loc75` | 83 | 2.4% | 40 | 10.0% | 7.5% | 33.33 |
| `climax_vol8_r1_5` | 20 | 5.0% | 10 | 10.0% | 10.0% | 33.33 |
| `r3_40_xs15_trend` | 109 | 5.5% | 81 | 9.9% | 4.9% | 32.93 |
| `r1_25_vol4.0_loc0` | 72 | 4.2% | 61 | 9.8% | 3.3% | 32.8 |
| `r1_18_vol2.5_loc0` | 203 | 1.5% | 145 | 9.0% | 5.5% | 29.9 |
| `r3_25_xs40_trend` | 182 | 3.3% | 128 | 8.6% | 5.5% | 28.63 |
| `r1_25_vol1.5_loc75` | 86 | 2.3% | 47 | 8.5% | 6.4% | 28.37 |
| `climax_vol3_r1_15` | 125 | 1.6% | 72 | 8.3% | 4.2% | 27.77 |
| `rng3_r1_15_vol2` | 93 | 2.1% | 112 | 8.0% | 4.5% | 26.8 |

## Walk-forward — meest accurate sleeve die in-sample geld verdiende

Gekozen op train (t/m 2025). 2026 is onaangeroerd.

- Train: `r3_40_xs15_trend__r1__btc1__gapNA__tp50_hs12_d1`  PnL **€+1,688**  precision **9.8%**  trades 41  hits 4  DD 31.1%
- Test 2026: `r3_40_xs15_trend__r1__btc1__gapNA__tp50_hs12_d1`  PnL **€+594**  precision **17.1%**  trades 35  hits 6  DD 65.7%
- Full: €+2,804  precision 13.2%  DD 41.8%  trades 76

Opstelling: gate `r3_40_xs15_trend`, rank `r1`, alleen als BTC > SMA50, gap-filter: geen, exit `tp50_hs12_d1`.

### Full path

Top-naam aandeel in bruto winst: 15.1%.

| Naam | PnL |
|---|---:|
| PHA | €+1,645 |
| ENJ | €+1,436 |
| QNT | €+891 |
| HBAR | €+704 |
| LSK | €+627 |
| DOGE | €+533 |
| XLM | €+415 |
| RAY | €+296 |

| Datum | Naam | Reden | hit50 | PnL |
|---|---|---|---|---:|
| 2024-11-18 | HBAR | take_profit | True | €+839 |
| 2024-11-23 | XLM | take_profit | True | €+839 |
| 2024-12-27 | PHA | take_profit | True | €+839 |
| 2026-04-14 | ENJ | take_profit | True | €+839 |
| 2026-04-15 | ENJ | take_profit | True | €+839 |
| 2026-09-13 | LSK | take_profit | True | €+839 |
| 2026-09-27 | QNT | take_profit | True | €+839 |
| 2026-10-01 | MOVR | take_profit | True | €+839 |
| 2026-10-02 | GTC | hard_stop | False | €-212 |
| 2026-09-28 | QNT | hard_stop | False | €-212 |
| 2026-09-26 | PHA | hard_stop | False | €-212 |
| 2026-09-18 | COTI | hard_stop | False | €-212 |
| 2026-09-14 | LSK | hard_stop | False | €-212 |

| Maand | PnL |
|---|---:|
| 2024-06 | €+610 |
| 2024-09 | €-212 |
| 2024-11 | €+1,700 |
| 2024-12 | €+1,003 |
| 2025-04 | €-272 |
| 2025-05 | €+16 |
| 2025-06 | €-333 |
| 2025-07 | €-190 |
| 2025-08 | €-635 |
| 2026-04 | €+613 |
| 2026-05 | €-561 |
| 2026-08 | €-299 |
| 2026-09 | €+736 |
| 2026-10 | €+627 |

## Walk-forward — hoogste train-PnL

- Train: `r1_18_vol1.5_loc0__rs__btc0__gapNA__tp100_hs20_d3`  PnL **€+4,808**  precision **2.6%**  trades 77  hits 2  DD 34.9%
- Test 2026: `r1_18_vol1.5_loc0__rs__btc0__gapNA__tp100_hs20_d3`  PnL **€+7,847**  precision **7.0%**  trades 57  hits 4  DD 76.0%
- Full: €+13,732  precision 4.5%  DD 36.9%  trades 134

Opstelling: gate `r1_18_vol1.5_loc0`, rank `rs`, geen BTC-filter, gap-filter: geen, exit `tp100_hs20_d3`.

### Full path

Top-naam aandeel in bruto winst: 9.7%.

| Naam | PnL |
|---|---:|
| LSK | €+2,966 |
| SYN | €+2,837 |
| NOM | €+2,613 |
| MAGIC | €+2,578 |
| USELESS | €+1,401 |
| QNT | €+1,338 |
| ENJ | €+1,217 |
| NPC | €+1,210 |

| Datum | Naam | Reden | hit50 | PnL |
|---|---|---|---|---:|
| 2024-12-27 | PHA | take_profit | False | €+1,686 |
| 2025-04-21 | MAGIC | take_profit | True | €+1,686 |
| 2026-04-01 | NOM | take_profit | False | €+1,686 |
| 2026-04-02 | NOM | take_profit | True | €+1,686 |
| 2026-04-15 | ENJ | take_profit | False | €+1,686 |
| 2026-06-18 | SYN | take_profit | False | €+1,686 |
| 2026-06-25 | SYN | take_profit | False | €+1,686 |
| 2026-09-03 | USELESS | take_profit | False | €+1,686 |
| 2026-09-28 | QNT | hard_stop | False | €-347 |
| 2026-09-14 | LSK | hard_stop | False | €-347 |
| 2026-09-09 | USELESS | hard_stop | False | €-347 |
| 2026-08-28 | MOVR | hard_stop | False | €-347 |
| 2026-08-15 | ALICE | hard_stop | False | €-347 |

| Maand | PnL |
|---|---:|
| 2024-06 | €+379 |
| 2024-07 | €-67 |
| 2024-08 | €+114 |
| 2024-09 | €-376 |
| 2024-10 | €+353 |
| 2024-11 | €+314 |
| 2024-12 | €+1,367 |
| 2025-01 | €-2 |
| 2025-02 | €-640 |
| 2025-03 | €-273 |
| 2025-04 | €+3,207 |
| 2025-05 | €-455 |
| 2025-06 | €-655 |
| 2025-07 | €+246 |
| 2025-08 | €+646 |
| 2025-09 | €+16 |
| 2025-10 | €+622 |
| 2025-11 | €+809 |
| 2025-12 | €-797 |
| 2026-01 | €+51 |
| 2026-02 | €-736 |
| 2026-03 | €-518 |
| 2026-04 | €+3,176 |
| 2026-05 | €+96 |
| 2026-06 | €+2,086 |
| 2026-07 | €-215 |
| 2026-08 | €-420 |
| 2026-09 | €+5,428 |
| 2026-10 | €-24 |

## Hoogste PnL in de zoektocht (2026, vers boek)

Dit is het maximum over de hele grid op de testperiode. Het is het best behaalde test-cijfer, niet de walk-forward keuze.

- Test 2026: `r1_18_vol2.5_loc0__rs__btc1__gapNA__trail30_hs15_d4`  PnL **€+19,894**  precision **19.2%**  trades 26  hits 5  DD 30.8%
- Train: `r1_18_vol2.5_loc0__rs__btc1__gapNA__trail30_hs15_d4`  PnL **€-934**  precision **2.5%**  trades 40  hits 1  DD 70.2%
- Full: €+17,770  precision 9.1%  DD 73.6%  trades 66

Opstelling: gate `r1_18_vol2.5_loc0`, rank `rs`, alleen als BTC > SMA50, gap-filter: geen, exit `trail30_hs15_d4`.

### Test 2026

Top-naam aandeel in bruto winst: 35.6%.

| Naam | PnL |
|---|---:|
| LSK | €+8,032 |
| USELESS | €+3,266 |
| SYN | €+1,804 |
| NPC | €+1,653 |
| ENJ | €+1,625 |
| PHA | €+1,264 |
| NOM | €+1,029 |
| QNT | €+876 |

| Datum | Naam | Reden | hit50 | PnL |
|---|---|---|---|---:|
| 2026-09-14 | LSK | trail | True | €+8,032 |
| 2026-09-05 | USELESS | time | False | €+3,266 |
| 2026-09-20 | SYN | time | True | €+2,067 |
| 2026-08-24 | NPC | time | False | €+1,653 |
| 2026-04-17 | ENJ | time | False | €+1,625 |
| 2026-04-03 | NOM | trail | True | €+1,291 |
| 2026-09-26 | PHA | time | False | €+1,264 |
| 2026-10-02 | MOVR | eow | True | €+1,192 |
| 2026-09-15 | ARK | hard_stop | False | €-263 |
| 2026-09-09 | NOM | hard_stop | False | €-263 |
| 2026-08-06 | SYN | hard_stop | False | €-263 |
| 2026-07-24 | SWEAT | hard_stop | False | €-263 |
| 2026-05-12 | GTC | hard_stop | False | €-263 |

| Maand | PnL |
|---|---:|
| 2026-01 | €+83 |
| 2026-03 | €-263 |
| 2026-04 | €+2,201 |
| 2026-05 | €+556 |
| 2026-07 | €+57 |
| 2026-08 | €+1,087 |
| 2026-09 | €+16,235 |
| 2026-10 | €-63 |

### Full path van dat pack

Top-naam aandeel in bruto winst: 33.0%.

| Naam | PnL |
|---|---:|
| LSK | €+8,032 |
| USELESS | €+3,266 |
| PHA | €+2,449 |
| NPC | €+1,653 |
| SYN | €+1,643 |
| ENJ | €+1,145 |
| QNT | €+876 |
| MOVR | €+667 |

| Datum | Naam | Reden | hit50 | PnL |
|---|---|---|---|---:|
| 2026-09-14 | LSK | trail | True | €+8,032 |
| 2026-09-05 | USELESS | time | False | €+3,266 |
| 2026-09-20 | SYN | time | True | €+2,067 |
| 2026-08-24 | NPC | time | False | €+1,653 |
| 2026-09-26 | PHA | time | False | €+1,264 |
| 2026-10-02 | MOVR | eow | True | €+1,192 |
| 2024-12-29 | PHA | time | False | €+1,186 |
| 2026-04-17 | ENJ | time | False | €+1,145 |
| 2026-09-15 | ARK | hard_stop | False | €-263 |
| 2026-09-09 | NOM | hard_stop | False | €-263 |
| 2026-08-06 | SYN | hard_stop | False | €-263 |
| 2026-07-24 | SWEAT | hard_stop | False | €-263 |
| 2026-05-12 | GTC | hard_stop | False | €-263 |

| Maand | PnL |
|---|---:|
| 2024-06 | €-257 |
| 2024-07 | €-253 |
| 2024-08 | €-180 |
| 2024-09 | €-192 |
| 2024-10 | €+231 |
| 2024-11 | €+198 |
| 2024-12 | €+987 |
| 2025-01 | €-424 |
| 2025-02 | €-288 |
| 2025-04 | €+352 |
| 2025-05 | €-489 |
| 2025-06 | €-294 |
| 2025-07 | €-81 |
| 2025-08 | €+274 |
| 2025-09 | €-114 |
| 2025-10 | €-403 |
| 2026-01 | €+33 |
| 2026-03 | €-121 |
| 2026-04 | €+1,018 |
| 2026-05 | €+458 |
| 2026-07 | €+57 |
| 2026-08 | €+1,087 |
| 2026-09 | €+16,235 |
| 2026-10 | €-63 |

## Hoogste PnL op het hele pad

Vers boek vanaf 2024-06-15, zelfde kosten. Het absolute maximum mag in 2025 verlies hebben gedraaid. Daarnaast het maximum onder packs die t/m 2025 wél positief waren.

- Absoluut maximum: `r1_18_vol2.5_loc0__rs__btc1__gapNA__trail30_hs15_d4`  PnL **€+17,770**  precision **9.1%**  trades 66  hits 6  DD 73.6% op full, test €+19,894, train €-934.
Opstelling: gate `r1_18_vol2.5_loc0`, rank `rs`, alleen als BTC > SMA50, gap-filter: geen, exit `trail30_hs15_d4`.

- Hoogste full-PnL met train-PnL>0: `r1_18_vol2.5_loc0__vol__btc0__gapNA__trail30_hs15_d4`  PnL **€+17,080**  precision **7.5%**  trades 106  hits 8  DD 60.2% op full, test €+16,780, train €+286.
Opstelling: gate `r1_18_vol2.5_loc0`, rank `vol`, geen BTC-filter, gap-filter: geen, exit `trail30_hs15_d4`.


### Top 10 test-PnL

| Pack | test PnL | test P50 | test DD | train PnL | train P50 |
|---|---:|---:|---:|---:|---:|
| `r1_18_vol2.5_loc0__rs__btc1__gapNA__trail30_hs15_d4` | €+19,894 | 19.2% | 30.8% | €-934 | 2.5% |
| `r1_18_vol2.5_loc0__rs__btc1__gap10__trail30_hs15_d4` | €+19,894 | 19.2% | 30.8% | €-934 | 2.5% |
| `r1_18_vol2.5_loc0__rs__btc1__gap25__trail30_hs15_d4` | €+19,894 | 19.2% | 30.8% | €-934 | 2.5% |
| `r1_18_vol2.5_loc0__rs__btc0__gapNA__trail30_hs15_d4` | €+18,956 | 11.6% | 61.7% | €-589 | 3.0% |
| `r1_18_vol2.5_loc0__rs__btc0__gap10__trail30_hs15_d4` | €+18,956 | 11.6% | 61.7% | €-589 | 3.0% |
| `r1_18_vol2.5_loc0__rs__btc0__gap25__trail30_hs15_d4` | €+18,956 | 11.6% | 61.7% | €-589 | 3.0% |
| `r1_18_vol2.5_loc0__r1__btc1__gapNA__trail30_hs15_d4` | €+18,943 | 12.0% | 28.9% | €-895 | 2.5% |
| `r1_18_vol2.5_loc0__r1__btc1__gap10__trail30_hs15_d4` | €+18,943 | 12.0% | 28.9% | €-895 | 2.5% |
| `r1_18_vol2.5_loc0__r1__btc1__gap25__trail30_hs15_d4` | €+18,943 | 12.0% | 28.9% | €-895 | 2.5% |
| `r1_18_vol1.5_loc0__r1__btc1__gapNA__trail30_hs15_d4` | €+18,766 | 10.7% | 30.4% | €-1,390 | 2.4% |

### Top 10 test-precisie (sleeve, ≥8 trades)

| Pack | test P50 | test trades | test PnL | train P50 | train PnL |
|---|---:|---:|---:|---:|---:|
| `r1_35_vol4.0_loc0__r1__btc1__gapNA__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |
| `r1_35_vol4.0_loc0__r1__btc1__gap10__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |
| `r1_35_vol4.0_loc0__r1__btc1__gap25__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |
| `r1_35_vol4.0_loc0__r1vol__btc1__gapNA__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |
| `r1_35_vol4.0_loc0__r1vol__btc1__gap10__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |
| `r1_35_vol4.0_loc0__r1vol__btc1__gap25__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |
| `r1_35_vol4.0_loc0__rs__btc1__gapNA__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |
| `r1_35_vol4.0_loc0__rs__btc1__gap10__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |
| `r1_35_vol4.0_loc0__rs__btc1__gap25__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |
| `r1_35_vol4.0_loc0__vol__btc1__gapNA__trail30_hs15_d4` | 35.7% | 14 | €-26 | 5.9% | €-287 |

## Hoe te lezen

1. De basiskans van een +50% sessie (high t.o.v. de open die je nog kunt kopen) ligt ver onder de 7-daagse +50% uit `RESULTS.md`.
2. Een gate kan de kans een paar keer verhogen en toch ver van 'meestal goed' blijven. Precisie van het signaal en PnL van het slot zijn verschillende dingen: een zeldzame +50% betaalt de stops alleen als de win-rate én de exit kloppen.
3. Het plafond laat zien hoeveel er op tafel ligt als elke event-dag perfect geraakt wordt. De sleeve-PnL daaronder is wat de tape met deze features echt afgeeft.
4. Deel van de winst in één naam betekent dat het pad door een paar spikes loopt. De regel zelf blijft generiek (drempels op return, volume, range, trend).

Reproduce:

```bash
.venv/bin/python -m bot.research.moonshot_preimage.day50
```

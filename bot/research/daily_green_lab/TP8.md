# Continuous +8% take-profit

asof `2026-10-05T15:39:25.231249+00:00`  book €2,000  alts **80**  fee 0.0015  slip 0.001

Signaal op de slotkoers, koop de volgende opening. Take-profit vult op de dagbar als de high +8% boven de entry komt (up-bar: target vóór de stop). Daarna pas de volgende munt op de opening erna. Vast boek, elke vlakke dag weer €book.

Een pakket dat alleen in 2026 groen is en over 2024–2026 rood, telt niet als de PnL. Zonder stop of tijdslimiet blijft de sleeve in één munt hangen; de 2026-rij apart starten telt die hangende trade niet en oogt te groen.

Dual-venster groen in deze grid: `brk20_day_tp8_hs0_d3` — full **+€1,346**, IS +€637, OOS +€709, maxDD 77%. Ongeveer de helft van de exits is de +8%.

`top_day` met TP 8% en hard-stop 5% draait wel continu (honderden trades) en verliest: full ongeveer **−€5.6k**.

| Pack | IS € | OOS € | Full € | 6w € | OOS TP-rate | OOS DD | OOS green |
|---|---:|---:|---:|---:|---:|---:|---:|
| `coil_day_tp8_hs0_d0` | -1132 | +3219 | -1157 | +1139 | 96% | 24.7% | 52% |
| `coil_day_tp8_hs0_d3` | -5569 | +2923 | -2471 | +460 | 52% | 24.2% | 49% |
| `brk20_day_tp8_hs5_d0` | -773 | +2845 | +2072 | +1345 | 54% | 31.0% | 41% |
| `brk20_day_tp8_hs5_d5` | -1326 | +2374 | +1048 | +1345 | 48% | 35.6% | 34% |
| `coil_day_tp8_hs0_d5` | -822 | +2282 | +1527 | +673 | 62% | 30.2% | 46% |
| `brk20_day_tp8_hs5_d3` | -1873 | +1782 | -91 | +1345 | 43% | 40.8% | 31% |
| `top_day_tp8_hs0_d3` | -1218 | +1628 | +585 | +1503 | 61% | 90.0% | 48% |
| `top_day_tp8_hs0_d5` | -2087 | +1579 | -665 | +1226 | 66% | 108.5% | 47% |
| `coil_day_tp8_hs5_d5` | -3641 | +1272 | -2220 | -11 | 43% | 27.3% | 45% |
| `coil_day_tp8_hs5_d0` | -3505 | +1242 | -2114 | -42 | 45% | 29.9% | 47% |
| `top_day_tp8_hs0_d0` | -74 | +1096 | +191 | +932 | 90% | 46.7% | 51% |
| `coil_day_tp8_hs5_d3` | -3555 | +944 | -2462 | +69 | 38% | 44.0% | 43% |
| `brk20_day_tp8_hs0_d3` | +637 | +709 | +1346 | +884 | 53% | 83.4% | 36% |
| `top_day_tp8_hs5_d3_btc` | -999 | +184 | -815 | +1008 | 40% | 62.4% | 23% |
| `brk20_day_tp8_hs0_d5` | -470 | -308 | -778 | +483 | 58% | 62.7% | 35% |
| `brk20_day_tp8_hs0_d0` | +223 | -717 | +489 | +40 | 86% | 61.5% | 43% |
| `top_day_tp8_hs5_d0` | -3371 | -1317 | -5081 | +1008 | 39% | 140.2% | 42% |
| `top_day_tp8_hs5_d3` | -3920 | -1862 | -5633 | +1008 | 36% | 155.1% | 39% |
| `top_day_tp8_hs5_d5` | -3618 | -1888 | -5357 | +1008 | 37% | 165.0% | 38% |
| `top_day_tp8_hs5_d3_rotate` | -4201 | -2018 | -6071 | +1032 | 31% | 160.5% | 40% |

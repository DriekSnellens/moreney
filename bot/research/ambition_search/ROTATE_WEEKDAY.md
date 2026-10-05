# residual_full — rotate dag maakt uit?

asof `2026-10-04T19:43:45.934013+00:00`  `2024-03-16` → `2026-10-03`  book €20,000  wet compound  skip=1 trail 10%

## Live nu

- **Geen weekday-pin** (`rebalance_weekday=null`) = any-day.
- Laatste clock: **Tuesday 2026-09-29 08:41 UTC** → volgende age-due **Tuesday 2026-10-06 08:41 UTC**.
- Daardoor voelt het als “elke dinsdag”, maar dat is de **7d-fase**, geen pin. Een **alt-trail** reset de klok → fase kan verschuiven.

## Sim: vaste pin vs any-day

| Setup | PnL | Return | maxDD | Calmar | trades |
|---|---:|---:|---:|---:|---:|
| `any_day` **LIVE** | +410,891 | 2054.5% | 30.9% | 7.53 | 92 |
| `pin_Thu` | +77,848 | 389.2% | 35.7% | 2.42 | 70 |
| `pin_Tue` | +17,119 | 85.6% | 64.0% | 0.43 | 74 |
| `pin_Fri` | +9,030 | 45.1% | 57.3% | 0.27 | 76 |
| `pin_Sat` | +8,893 | 44.5% | 50.6% | 0.31 | 76 |
| `pin_Wed` | +2,502 | 12.5% | 60.1% | 0.08 | 68 |
| `pin_Mon` | -12,867 | -64.3% | 70.3% | -0.47 | 72 |
| `pin_Sun` | -14,350 | -71.8% | 75.7% | -0.52 | 80 |

**Beste overall:** `any_day` (+€410,891).
**Beste vaste pin:** `pin_Thu` (+€77,848).
**Maandag / zondag:** slecht op dit pad (-12,867 / -14,350).

Pin ≠ any-day-fase: pin wacht tot die weekday ná 7d; any-day vuurt exact na 7d (of eerder via trail-reset).

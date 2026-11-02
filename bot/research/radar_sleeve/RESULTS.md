# Radar sleeve — archived news proxy

AlphaI's radar is a live snapshot (`point_in_time` false). This replay does not call it. The signal is a Poisson tail on archived crypto stories, relevance ≥ 6, prior 30 calendar days, stories with more than 5 tickers dropped.

Window `2026-08-05` → `2026-11-01`, 4360 stories, 90 publication days. Book €20,000. Sleeve 15% (€3,000), one name, held while it stays selected, wet next open. The other 85% is the live residual book. Idle sleeve cash earns nothing. The sleeve does not read the residual rank or the BTC average.

Locked cells: `spike` tail < 0.001 and sentiment delta ≥ 0; `hot` z ≥ 1 and sentiment delta ≥ 0; `hot_any` z ≥ 1. Nothing is armed. The archive does not reach before 2026.

## Residual on the same window

- full €20,000: pnl +18089  dd  31.7%  trades  14 hold cash

## Sleeve plus residual core

| Cell | Sleeve PnL | Sleeve DD | Trips | Days picked | Combined PnL | Combined DD | vs full |
|---|---:|---:|---:|---:|---:|---:|---:|
| spike | -193 | 7.6% | 2 | 2 | +15259 | 29.8% | -2830 |
| hot | -1132 | 41.4% | 25 | 29 | +14320 | 31.9% | -3769 |
| hot_any | -1134 | 41.5% | 37 | 49 | +14319 | 31.4% | -3770 |

## What each cell traded

- `spike`: DOGE 1, NEAR 1. Largest trip NEAR €+35 (sleeve finished down).
- `hot`: NEAR 7, ADA 6, UNI 5, XRP 4, DOGE 2. Largest trip NEAR €+173 (sleeve finished down).
- `hot_any`: NEAR 10, ADA 8, UNI 8, ETH 6, DOGE 4. Largest trip NEAR €+166 (sleeve finished down).

## Reading

The highest combined cell is `spike` at €+15259, against the full book at €+18089. No locked cell beats the residual book on this overlap. Nothing is armed.

```bash
.venv/bin/python -m bot.research.radar_sleeve
```

# €1.700 sleeve — fixed book vs compound

asof `2026-10-04T09:58:00.873972+00:00`  pack **`brk20_day_t12_h5_hs5_btc1_fl0.00`**  universe **81**  start book €1,700

Wet next-open, fee 15 bp/side, slip 10 bp, BTC>SMA50.

| Mode | Size rule |
|---|---|
| `fixed_book_cap` | `notion = min(cash×0.98, €1700)` — live sleeve |
| `compound` | `notion = cash×0.98` — volle equity herbeleggen |

## Compare

| Window | Mode | PnL | Return | end eq | avg€/dag | greenW | maxDD | trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `last_6w` | `fixed_book_cap` | +11973 | +704% | €13,673 | +278.4 | 86% | 19.9% | 28 |
| `last_6w` | `compound` | +30112 | +1771% | €31,812 | +700.3 | 71% | 22.7% | 28 |
| `last_3m` | `fixed_book_cap` | +12350 | +726% | €14,050 | +132.8 | 71% | 25.5% | 44 |
| `last_3m` | `compound` | +37115 | +2183% | €38,815 | +399.1 | 64% | 25.5% | 44 |
| `last_1y` | `fixed_book_cap` | +12739 | +749% | €14,439 | +34.8 | 41% | 38.9% | 100 |
| `last_1y` | `compound` | +38597 | +2270% | €40,297 | +105.5 | 39% | 46.5% | 100 |
| `full` | `fixed_book_cap` | +11971 | +704% | €13,671 | +14.2 | 30% | 56.4% | 230 |
| `full` | `compound` | +29341 | +1726% | €31,041 | +34.9 | 29% | 57.1% | 230 |

## Compound weeks (last 6w)

| Week | Fixed € | Compound € |
|---|---:|---:|
| 2026-W33 | -9.55 | -9.55 |
| 2026-W34 | +36.17 | +30.44 |
| 2026-W35 | +440.15 | +458.39 |
| 2026-W36 | +7473.69 | +7190.27 |
| 2026-W37 | +72.21 | -225.96 |
| 2026-W38 | +3692.91 | +19473.24 |
| 2026-W39 | +267.51 | +3195.07 |

## Note

Live paper sleeve blijft **fixed_book_cap** (`size_to_book=true`). Compound is alleen research — na een spike schaalt ticket-size mee en DD in euro's groeit mee.

```bash
.venv/bin/python -m bot.research.daily_green_lab --compound
```

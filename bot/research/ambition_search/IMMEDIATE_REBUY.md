# residual_full — immediate rebuy na verkoop (excl. sold coin)

asof `2026-10-05T12:47:29.119137+00:00`  `2024-03-16` → `2026-10-03`  book €20,000  wet compound

Live-achtige knobs: skip1 lb10 trail10 floor3.5% any-day reb7 `require_alt_sma` + cash.

| Mode | Regel |
|---|---|
| `wait_7d` (live) | Na trail/sell: 7d cash-wacht vóór nieuwe buy |
| `immediate_rebuy` | Zelfde next-open: buy beste alt **≠ net verkocht** |

## Results

| Window | Mode | PnL | maxDD | Calmar | trades | imm rebuys | greenW | worst week |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `full` | `wait_7d` | +410,891 | 30.9% | 7.53 | 92 | 0 | 39/134 | -53,389 |
| `full` | `immediate_rebuy` | +132,799 | 58.3% | 2.09 | 159 | 41 | 42/134 | -29,142 |
| `pre_near` | `wait_7d` | +189,150 | 30.9% | 5.03 | 90 | 0 | 37/132 | -21,775 |
| `pre_near` | `immediate_rebuy` | +61,908 | 58.3% | 1.30 | 155 | 40 | 41/132 | -29,142 |

**Full verdict:** wait `+410,891` / 30.9% DD  vs  immediate `+132,799` / 58.3% DD (-278,093 Δ PnL).

Weekly rotate blijft; verschil = wat er gebeurt **na een trail** (geen week in cash tenzij geen andere alt kwalificeert).

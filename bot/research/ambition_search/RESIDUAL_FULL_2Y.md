# residual_full — huidige RS engine (~2y, compound)

asof `2026-10-04T19:35:10.021877+00:00`  `2024-03-16` → `2026-10-03`  book €20,000  wet Bitvavo next-open  **sizing = equity (winst herbelegd)**

Live params: `btc=0` lookback**10** skip**1** trail**10%** floor**3.5%** sma**50** flatten=all `require_alt_sma` + `cash_when_no_alt` reb**7** weekday**Tue** · desk 16 · **geen AlphaI** in deze fair replay.

## Summary (live = Tue + skip=1)

| Metric | Value |
|---|---:|
| Total PnL | **+17,119** |
| Return | **85.6%** |
| End equity | €37,119 |
| maxDD | **64.0%** |
| Ann | 27.4% |
| Calmar | 0.43 |
| Trades | 74 |
| Green weeks | 23/134 (17.2%) |
| Best / worst week | +14,042 / -4,472 |
| End hold | `cash` |

## Variants

| Variant | PnL | Return | End eq | maxDD | trades |
|---|---:|---:|---:|---:|---:|
| **live Tue + skip=1** | +17,119 | 85.6% | €37,119 | 64.0% | 74 |
| **any-day + skip=1 (pre-Tue pin)** | +410,891 | 2054.5% | €430,891 | 30.9% | 92 |
| **any-day + skip=0** | +109,206 | 546.0% | €129,206 | 33.1% | 94 |

## Yearly (live Tue skip=1)

| Year | PnL |
|---|---:|
| 2024 | +10,533 |
| 2025 | -12,112 |
| 2026 | +18,698 |

## Monthly (live Tue skip=1)

| Month | PnL |
|---|---:|
| 2024-03 | +876 |
| 2024-04 | +0 |
| 2024-05 | +1,907 |
| 2024-06 | +0 |
| 2024-07 | -2,805 |
| 2024-08 | -2,409 |
| 2024-09 | +9,025 |
| 2024-10 | +5,031 |
| 2024-11 | +1,705 |
| 2024-12 | -2,796 |
| 2025-01 | -2,028 |
| 2025-02 | -2,327 |
| 2025-03 | +0 |
| 2025-04 | +2,757 |
| 2025-05 | -2,633 |
| 2025-06 | -3,261 |
| 2025-07 | +985 |
| 2025-08 | -3,388 |
| 2025-09 | -2,218 |
| 2025-10 | +0 |
| 2025-11 | +0 |
| 2025-12 | +0 |
| 2026-01 | -1,037 |
| 2026-02 | +0 |
| 2026-03 | -2,194 |
| 2026-04 | +2,458 |
| 2026-05 | -2,773 |
| 2026-06 | +0 |
| 2026-07 | -1,141 |
| 2026-08 | +1,965 |
| 2026-09 | +21,420 |
| 2026-10 | +0 |

## Realized by base

| Base | Realized PnL | n exits |
|---|---:|---:|
| NEAR | +18,070 | 3 |
| UNI | +6,522 | 4 |
| DOT | +6,365 | 1 |
| SUI | +4,961 | 6 |
| ARB | +1,015 | 2 |
| FET | +827 | 4 |
| ADA | -185 | 1 |
| DOGE | -465 | 5 |
| ETH | -1,947 | 1 |
| AVAX | -2,356 | 2 |
| LINK | -3,219 | 4 |
| XRP | -3,425 | 2 |
| SOL | -4,783 | 2 |

## Worst weeks

| Week | Hold | PnL |
|---|---|---:|
| 2026-W40 | cash | -4,472 |
| 2024-W42 | cash | -3,889 |
| 2024-W51 | cash | -3,483 |
| 2024-W31 | cash | -3,361 |
| 2025-W31 | cash | -3,290 |
| 2025-W23 | cash | -3,261 |
| 2026-W20 | cash | -2,239 |
| 2026-W12 | cash | -2,194 |
| 2024-W30 | cash | -2,066 |
| 2025-W20 | alt:ETH | -2,061 |

## Best weeks

| Week | Hold | PnL |
|---|---|---:|
| 2026-W38 | alt:NEAR | +14,042 |
| 2026-W39 | alt:NEAR | +9,663 |
| 2024-W38 | alt:SUI | +6,086 |
| 2024-W41 | alt:SUI | +4,377 |
| 2024-W48 | alt:DOT | +4,332 |
| 2026-W36 | alt:UNI | +3,764 |
| 2024-W21 | alt:UNI | +3,205 |
| 2024-W44 | cash | +3,054 |
| 2025-W17 | alt:FET | +2,947 |
| 2024-W39 | alt:SUI | +2,635 |

## Note

Ticketsizen op gemarkeerde equity bij elke rebalance → compound. Live AlphaI-gate staat aan maar zit **niet** in deze fair replay. De Tuesday week-clock mist t.o.v. any-day een groot deel van het compound-pad (any-day skip=1 ≈ +€411k); pin is live gezet na de weekday-studie.

# residual_full (100% alt) — fair ~2y wet replay (no AlphaI)

asof `2026-10-03T09:53:22.707657+00:00`  `2024-03-16` → `2026-10-03`  book €20,000  wet Bitvavo next-open

Params (true residual_full): `btc=0` lookback**10** skip**0** trail**10%** floor**3.5%** sma**50** flatten=all `require_alt_sma` + `cash_when_no_alt` reb**7**.

## Summary

| Metric | skip=0 (true) | skip=1 (sensitivity) |
|---|---:|---:|
| Total PnL | **+109,206** | +410,891 |
| Return | **546.0%** | 2054.5% |
| End equity | €129,206 | €430,891 |
| maxDD | **33.1%** | 30.9% |
| Green weeks | 36/134 (26.9%) | 39/134 |
| Best week | +48,837 | +162,618 |
| Worst week | -15,725 | -53,389 |

> Note: an older ambition grid coerced `skip_days=0`→`1` in the evaluator, so its “sk0” rows were actually skip=1 (~+€411k). Figures below use **true skip=0**.

## Yearly (skip=0)

| Year | PnL |
|---|---:|
| 2024 | +34,275 |
| 2025 | -2,537 |
| 2026 | +77,468 |

## Monthly (skip=0)

| Month | PnL |
|---|---:|
| 2024-03 | +269 |
| 2024-04 | -3,805 |
| 2024-05 | +1,461 |
| 2024-06 | +782 |
| 2024-07 | -547 |
| 2024-08 | +1,070 |
| 2024-09 | +13,706 |
| 2024-10 | -1,400 |
| 2024-11 | +18,626 |
| 2024-12 | +4,113 |
| 2025-01 | +350 |
| 2025-02 | -4,467 |
| 2025-03 | +0 |
| 2025-04 | +5,215 |
| 2025-05 | -5,403 |
| 2025-06 | -4,329 |
| 2025-07 | +6,900 |
| 2025-08 | -5,270 |
| 2025-09 | +4,467 |
| 2025-10 | +0 |
| 2025-11 | +0 |
| 2025-12 | +0 |
| 2026-01 | -4,219 |
| 2026-02 | +0 |
| 2026-03 | +4,008 |
| 2026-04 | +3,583 |
| 2026-05 | +19,941 |
| 2026-06 | +0 |
| 2026-07 | -315 |
| 2026-08 | -16,727 |
| 2026-09 | +71,198 |
| 2026-10 | +0 |

## Realized by base (skip=0)

| Base | Realized PnL | n exits |
|---|---:|---:|
| ARB | -8,725 | 3 |
| ADA | -8,161 | 1 |
| SOL | -7,899 | 3 |
| XRP | -6,033 | 4 |
| LINK | -1,345 | 2 |
| ETH | -217 | 1 |
| LTC | +1,565 | 2 |
| UNI | +3,038 | 9 |
| AVAX | +4,714 | 1 |
| DOGE | +5,567 | 5 |
| DOT | +9,178 | 2 |
| FET | +9,898 | 5 |
| SUI | +27,250 | 5 |
| NEAR | +91,278 | 4 |

## Worst weeks

| Week | Hold | PnL |
|---|---|---:|
| 2026-W40 (2026-09-28) | cash | -15,725 |
| 2025-W33 (2025-08-11) | cash | -6,973 |
| 2026-W13 (2026-03-23) | cash | -6,809 |
| 2026-W33 (2026-08-10) | cash | -6,515 |
| 2025-W05 (2025-01-27) | cash | -6,172 |
| 2026-W20 (2026-05-11) | cash | -5,962 |
| 2025-W24 (2025-06-09) | cash | -5,726 |
| 2026-W37 (2026-09-07) | cash | -5,612 |
| 2026-W35 (2026-08-24) | cash | -5,413 |
| 2025-W30 (2025-07-21) | cash | -5,303 |
| 2024-W12 (2024-03-18) | cash | -3,537 |
| 2025-W21 (2025-05-19) | cash | -3,422 |
| 2026-W29 (2026-07-13) | alt:ARB | -3,233 |
| 2025-W02 (2025-01-06) | cash | -2,914 |
| 2024-W25 (2024-06-17) | cash | -2,708 |

## Best weeks

| Week | Hold | PnL |
|---|---|---:|
| 2026-W38 (2026-09-14) | alt:NEAR | +48,837 |
| 2026-W39 (2026-09-21) | alt:NEAR | +33,672 |
| 2026-W21 (2026-05-18) | alt:NEAR | +20,031 |
| 2026-W36 (2026-08-31) | alt:UNI | +10,027 |
| 2024-W45 (2024-11-04) | alt:SUI | +9,551 |
| 2024-W38 (2024-09-16) | alt:SUI | +9,157 |
| 2025-W29 (2025-07-14) | alt:XRP | +7,050 |
| 2024-W49 (2024-12-02) | cash | +6,780 |
| 2026-W12 (2026-03-16) | cash | +6,191 |
| 2025-W28 (2025-07-07) | alt:SUI | +6,016 |
| 2025-W38 (2025-09-15) | alt:AVAX | +5,768 |
| 2025-W17 (2025-04-21) | alt:FET | +5,615 |
| 2025-W03 (2025-01-13) | alt:XRP | +5,519 |
| 2024-W46 (2024-11-11) | alt:DOGE | +5,411 |
| 2026-W11 (2026-03-09) | alt:FET | +4,626 |

## Where the money came from

- **NEAR** dominates realized PnL (~+€91k across exits), especially Sep 2026 and a long 2024→2026 hold.
- **SUI** second (~+€27k). Losers: ARB/ADA/SOL/XRP clusters.
- **2025** is roughly flat/slightly red (−€2.5k): long underwater stretch (peak DD ~33% into Jul 2025).
- Green-week rate stays ~**27%** — same structural message as the short window: big spikes, many red/flat weeks.

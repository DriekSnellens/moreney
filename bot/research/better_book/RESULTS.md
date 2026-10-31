# Better than the live residual pack

asof `2026-10-30`. Wet Bitvavo next-open, book €20,000, desk 16, 2024-03-16 → 2026-10-30.
Baseline is the armed pack (10d skip-1, floor 3.5%, own SMA50, cash when none
qualify, 7-day clock, near-high 0.25, trail 10%, lock +12%/+6%). IS runs that
book through 2025-12-31. OOS is a fresh €20k book from 2026-01-01.

The bar was locked before this run:

- fresh 2026 PnL at least 1.50× the baseline, and positive
- full-path PnL at least 1.25× the baseline
- IS PnL at least the baseline
- full-path max drawdown at most 5% worse
- at least half the cells in that family, and at least two, beat the
  baseline on both IS and fresh 2026
- less than 60% of the full-path euro gain comes from one
  round trip the baseline did not also book

A family that misses any line is not a find. Nothing here is wired into the live clip.

Reproduce: `.venv/bin/python -m bot.research.better_book`
(`--no-fresh` skips the Bitvavo merge and replays the local cache only.)

## Baseline

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| baseline | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |

## Scale in after the starter proves itself

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| scale_s40_a8 | +137,587 | 19.7% | +40,986 | +24,247 | 19.3% | 118 |
| scale_s40_a12 | +108,070 | 20.7% | +38,465 | +17,374 | 19.3% | 118 |
| scale_s40_a20 | +80,646 | 19.6% | +33,459 | +16,227 | 19.3% | 111 |
| scale_s50_a8 | +169,374 | 22.0% | +54,437 | +33,289 | 21.5% | 118 |
| scale_s50_a12 | +140,028 | 21.9% | +51,916 | +26,434 | 21.5% | 118 |
| scale_s50_a20 | +112,295 | 21.9% | +47,061 | +20,818 | 21.4% | 111 |
| scale_s60_a8 | +203,611 | 24.4% | +69,892 | +33,064 | 23.6% | 118 |
| scale_s60_a12 | +176,159 | 24.3% | +67,502 | +27,636 | 23.6% | 118 |
| scale_s60_a20 | +149,839 | 24.2% | +63,045 | +23,099 | 23.6% | 111 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Widen the trail only after a large peak

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| runner_a30_t15 | +186,704 | 51.4% | +65,617 | +33,827 | 27.0% | 104 |
| runner_a30_t20 | +213,584 | 51.4% | +72,210 | +36,590 | 27.0% | 102 |
| runner_a30_t25 | +240,585 | 51.4% | +72,210 | +43,458 | 27.1% | 102 |
| runner_a40_t15 | +211,917 | 44.9% | +77,372 | +33,827 | 27.0% | 102 |
| runner_a40_t20 | +284,773 | 34.3% | +104,246 | +36,590 | 27.0% | 98 |
| runner_a40_t25 | +278,391 | 43.6% | +87,354 | +43,458 | 27.1% | 100 |
| runner_a60_t15 | +309,531 | 30.7% | +127,119 | +33,146 | 27.0% | 102 |
| runner_a60_t20 | +331,212 | 31.0% | +138,325 | +33,146 | 27.0% | 102 |
| runner_a60_t25 | +331,212 | 31.0% | +138,325 | +33,146 | 27.0% | 102 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Price trail on one weekday, lock every day

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| trail_mon | +2,974 | 52.7% | +12,496 | -5,569 | 52.0% | 96 |
| trail_fri | +77,813 | 52.0% | +33,785 | +18,384 | 26.8% | 100 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Same 10% trail, but one close is not enough

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| confirm_2 | +51,386 | 35.2% | +22,076 | +15,232 | 22.7% | 100 |
| confirm_3 | +13,763 | 50.4% | +4,029 | +8,306 | 27.7% | 98 |
| half_pierce | +57,718 | 47.3% | +31,038 | +11,619 | 33.1% | 120 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Sell at the level when the low trades it

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| touch_trail | +56,543 | 37.4% | +32,068 | +11,211 | 25.2% | 104 |
| touch_lock | +293,492 | 41.2% | +125,563 | +30,791 | 31.9% | 102 |
| touch_both | +54,184 | 42.0% | +30,331 | +11,211 | 25.2% | 104 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Rank by excess after the alt's own BTC beta

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| beta_20 | +300,127 | 33.9% | +129,096 | +30,791 | 31.9% | 102 |
| beta_40 | +119,804 | 40.1% | +39,127 | +30,791 | 31.9% | 102 |
| beta_60 | +119,804 | 40.1% | +39,127 | +30,791 | 31.9% | 102 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Rank that excess per unit of residual volatility

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| ir_10 | +139,052 | 40.9% | +86,462 | +13,629 | 28.0% | 102 |
| ir_20 | +167,179 | 30.1% | +107,722 | +13,629 | 28.0% | 102 |
| ir_40 | +131,583 | 32.9% | +43,700 | +31,421 | 31.9% | 104 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Reading

Full size on the leader is the book. A starter of 40–60% that adds only after the close is through an arm keeps about a quarter to three-fifths of the full path, with a lower drawdown that does not pay for the lost euros.

The 10% trail has to fire on the first close through the level, and it has to sell the whole lot. Widening it after a large peak never reaches the size bar; the closest cells are the ones that almost never widen. Checking that trail on only one weekday, waiting for a second or third close, or banking half on the first pierce, all give the compounded path back. Selling when the low trades the prior-peak level is earlier than the close and does the same: the wick is not a fill you want.

The bar was not loosened after these numbers. A later family has to leave the entry size and this exit alone.

Changing only who is ranked first does not clear it either. The live excess treats every alt as if its BTC beta were 1. Estimating that beta on 20, 40 or 60 daily log returns leaves the fresh 2026 book on the same euros and cuts the compounded path. Dividing the live score by the volatility of the alt-minus-BTC residual cuts the compounded path as well. The 40-day window is a few hundred euros ahead on a fresh 2026 book and about €210k behind over the full path.

## Verdict

Nothing cleared the pre-registered bar. The live residual pack stays the book. Nothing is armed.

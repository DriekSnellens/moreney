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

## After a green exit, open the next leader before the week is up

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| rearm_lock | +300,814 | 42.8% | +129,462 | +30,791 | 31.9% | 104 |
| rearm_green | +130,939 | 44.3% | +61,590 | +21,057 | 33.4% | 122 |
| rearm_green_next | +17,266 | 51.1% | +14,668 | +2,188 | 28.4% | 124 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Open a new ride only while BTC's own window is up

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| tide_0 | +61,930 | 42.7% | +68,604 | -189 | 40.6% | 84 |
| tide_2 | +80,861 | 42.0% | +64,728 | +5,449 | 30.7% | 76 |
| tide_5 | +57,517 | 37.5% | +68,439 | -1,640 | 20.3% | 64 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## After a green exit, wait longer than a week

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| pause_14 | +87,817 | 37.2% | +33,820 | +22,361 | 16.2% | 82 |
| pause_21 | +22,863 | 39.2% | +8,263 | +10,849 | 25.8% | 80 |
| pause_28 | +28,664 | 27.7% | +24,996 | +2,325 | 27.3% | 68 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Cash on a BTC SMA break only after a deeper gap

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| veto_5 | +114,322 | 55.8% | +62,628 | +12,682 | 31.6% | 116 |
| veto_10 | -157 | 68.5% | -5,787 | +6,824 | 34.0% | 122 |
| veto_off | +48,407 | 74.5% | +35,465 | +13,255 | 33.7% | 126 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Buy a cooler qualifier than the residual leader

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| slot_second | -3,715 | 65.2% | +2,261 | -5,301 | 47.5% | 92 |
| slot_third | -6,065 | 42.1% | +1,278 | -6,881 | 38.1% | 76 |
| slot_floor | -9,489 | 67.7% | -11,653 | +4,160 | 34.2% | 110 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Sell a position that is still red after a few days

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| red_2 | +227,070 | 33.5% | +56,817 | +48,308 | 32.1% | 98 |
| red_4 | +333,913 | 34.1% | +160,358 | +25,402 | 31.8% | 94 |
| red_6 | +298,190 | 34.5% | +122,451 | +30,791 | 31.9% | 96 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Sell a red position only after it loses the lead

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| red_lead_2 | +417,544 | 34.4% | +148,310 | +40,807 | 32.0% | 98 |
| red_lead_4 | +333,913 | 34.1% | +160,358 | +25,402 | 31.8% | 94 |
| red_lead_6 | +298,190 | 34.5% | +122,451 | +30,791 | 31.9% | 96 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Cancel a buy when the next open gaps up

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| gap_3 | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |
| gap_5 | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |
| gap_8 | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Sell when the held name loses the residual lead

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| lead_rank | +66,224 | 44.9% | +39,365 | +11,053 | 36.0% | 108 |
| lead_rank_red | +258,057 | 39.7% | +144,236 | +18,839 | 33.3% | 102 |
| lead_floor | +245,715 | 39.1% | +105,885 | +28,619 | 23.0% | 102 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Keep a held name when BTC closes under its average

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| keep_green | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |
| keep_leader | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |
| keep_both | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Require the longer residual to be positive too

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| agree_20 | +290,255 | 36.0% | +123,845 | +30,791 | 31.9% | 100 |
| agree_40 | +350,425 | 34.1% | +153,446 | +31,078 | 31.9% | 96 |
| agree_60 | +115,801 | 50.5% | +78,807 | +9,593 | 31.6% | 92 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Skip a name after a sell that closed under the entry

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| ban_14 | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |
| ban_28 | +340,848 | 34.1% | +151,125 | +30,791 | 31.9% | 100 |
| ban_56 | +340,848 | 34.1% | +151,125 | +30,791 | 31.9% | 100 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Buy only a name whose own volume has expanded

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| vol_100 | +186,857 | 32.3% | +75,236 | +28,182 | 30.4% | 92 |
| vol_150 | +29,917 | 66.1% | +10,993 | +12,565 | 28.4% | 74 |
| vol_200 | +66,910 | 64.2% | +32,755 | +13,622 | 24.1% | 58 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Buy only a residual that just cleared the floor

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| cross_1 | -6,633 | 50.6% | -9,479 | +5,153 | 16.7% | 52 |
| cross_2 | -204 | 46.5% | +5,387 | -4,308 | 40.6% | 66 |
| cross_3 | -10,641 | 62.1% | -4,798 | -7,783 | 53.8% | 74 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Reading

Full size on the leader is the book. A starter of 40–60% that adds only after the close is through an arm keeps about a quarter to three-fifths of the full path, with a lower drawdown that does not pay for the lost euros.

The 10% trail has to fire on the first close through the level, and it has to sell the whole lot. Widening it after a large peak never reaches the size bar; the closest cells are the ones that almost never widen. Checking that trail on only one weekday, waiting for a second or third close, or banking half on the first pierce, all give the compounded path back. Selling when the low trades the prior-peak level is earlier than the close and does the same: the wick is not a fill you want.

The bar was not loosened after these numbers. A later family has to leave the entry size and this exit alone.

Changing only who is ranked first does not clear it either. The live excess treats every alt as if its BTC beta were 1. Estimating that beta on 20, 40 or 60 daily log returns leaves the fresh 2026 book on the same euros and cuts the compounded path. Dividing the live score by the volatility of the alt-minus-BTC residual cuts the compounded path as well. The 40-day window is a few hundred euros ahead on a fresh 2026 book and about €210k behind over the full path.

Opening the next leader sooner after a green exit loses. Waiting 14, 21 or 28 days after that same green exit also loses. A losing exit already keeps the week; lengthening the wait only after a winner gives the next ride away. Refusing a new ride unless BTC's own 10-day return is positive takes the fresh 2026 book to about zero.

Re-picking the sleeve each day from the most-traded names on the whole venue, 12, 16 or 24 of them, finishes between a small gain and a loss. The fixed desk on that same cache is the book above.

Letting a qualifying alt stay on when BTC is 5% or 10% under its SMA50, or ignoring that average, raises the full-path drawdown to 56–75% and gives the euros back. Buying the second score, the third, or the qualifier closest to the floor loses the stake or finishes a few thousand euros down. The leader, and the cash rule on a BTC close under its average, stay in the book.

Selling any position that is still under its entry after two days is ahead of the fresh-2026 size bar and misses the rest of it. The compounded path and the book through 2025 both give way. Four days is a little ahead through 2025 and a little behind on the full path, and it gives back fresh 2026. Six days leaves fresh 2026 unchanged. The day count stays where it was locked.

Keeping that two-day cut only when the name has also lost the lead is the closest cell: full path +€417,544 versus +€342,074, fresh 2026 +€40,807 versus +€30,791, and the book through 2025 a few thousand euros behind. That is short of 1.25× and of 1.5×, it is one cell, and four and six days do not come with it. Cancelling a buy when the next open gaps 3, 5 or 8 percent does not change a fill on this tape.

Selling as soon as the held name is no longer the top qualifier, with or without a red close, or as soon as its excess falls through the floor, gives the compounded path back. The two-day wait is what kept that closer cell. Dropping the wait is not a find.

Keeping a name already held when BTC closes under its average does not change a fill on this tape. Requiring the 40-day excess to be positive as well is a little ahead on both windows, about €8k on the full path and a few hundred euros on a fresh 2026 book, and it misses the size bar. Twenty and sixty days do not come with it. Skipping a name for 14 days after a losing sell changes nothing. Twenty-eight and fifty-six days cost about €1,200.

Requiring the name's own quote volume to have expanded cuts the path. At one times the prior twenty days the full path is +€186,857 and a fresh 2026 book is +€28,182. At 1.5× and 2× the book is a small gain with a much deeper drawdown. Buying only a residual that cleared the floor in the last one, two or three days loses the stake. The name that has already been above the floor is the book.

## Verdict

Nothing cleared the pre-registered bar. The live residual pack stays the book. Nothing is armed.

## Liquid names, re-picked every day

Same rules, on every Bitvavo EUR name in `data/research/bitvavo_1d_all` through 2026-10-30.
Each day the book may only buy one of the most-traded names, and a
name needs 60 completed days. The baseline row is the fixed desk on
this same cache, not the desk cache used above.

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| baseline | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |
| vol_top_12 | +2,479 | 59.8% | +7,875 | -3,724 | 52.6% | 114 |
| vol_top_16 | -7,875 | 80.4% | +15,752 | -13,094 | 65.5% | 124 |
| vol_top_24 | -17,287 | 87.1% | -15,673 | -8,068 | 46.4% | 130 |

Does not clear the bar. region=False margin=[].


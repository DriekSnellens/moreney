# Better than the live residual pack

asof `2026-10-31`. Wet Bitvavo next-open, book €20,000, desk 16, 2024-03-16 → 2026-10-31.
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

## A leader needs a deeper giveback before the trail sells

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| wide_15 | +83,515 | 39.9% | +21,756 | +31,701 | 32.5% | 96 |
| wide_20 | +234,265 | 35.9% | +126,363 | +19,660 | 32.3% | 92 |
| wide_25 | +257,067 | 37.7% | +122,498 | +24,526 | 32.4% | 92 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Open a new ride only when the qualifier count is in range

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| crowd_max_2 | +18,916 | 53.1% | -7,540 | +41,383 | 25.3% | 72 |
| crowd_max_3 | +21,863 | 55.8% | +8,921 | +9,448 | 30.7% | 82 |
| crowd_max_4 | +4,617 | 58.5% | -41 | +4,666 | 34.0% | 88 |
| crowd_min_2 | +195,977 | 38.4% | +118,513 | +15,533 | 34.0% | 92 |
| crowd_min_3 | +488,027 | 34.7% | +174,489 | +15,239 | 31.7% | 74 |
| crowd_min_4 | +66,496 | 40.8% | +47,382 | +4,502 | 36.5% | 62 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Cancel a buy when the next open gaps down

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| gap_down_2 | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |
| gap_down_4 | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |
| gap_down_6 | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Rotate only when the challenger leads by a set margin

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| rotate_2 | +253,984 | 33.6% | +139,693 | +18,877 | 31.7% | 102 |
| rotate_5 | +250,554 | 33.6% | +139,693 | +17,829 | 31.7% | 100 |
| rotate_8 | +275,634 | 33.8% | +139,693 | +21,024 | 31.8% | 98 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Open a new ride only when BTC's distance above its average is in range

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| ext_max_5 | +28,642 | 32.6% | +30,571 | +100 | 31.8% | 68 |
| ext_max_10 | +252 | 58.9% | +14,479 | -7,965 | 53.1% | 84 |
| ext_max_20 | +87,575 | 51.3% | +39,466 | +18,877 | 31.7% | 100 |
| ext_min_2 | +169,881 | 34.9% | +54,360 | +35,324 | 32.0% | 86 |
| ext_min_5 | +50,210 | 60.2% | +35,070 | +6,219 | 39.2% | 76 |
| ext_min_10 | +19,169 | 33.9% | +13,221 | +3,694 | 24.0% | 36 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Drop a name whose own lookback return is below a floor

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| own_0 | +331,233 | 34.0% | +151,798 | +28,516 | 31.9% | 98 |
| own_3 | +331,233 | 34.0% | +151,798 | +28,516 | 31.9% | 98 |
| own_6 | +250,063 | 38.5% | +147,164 | +16,599 | 33.5% | 92 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Rank qualifiers by whether the excess is rising or fading

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| accel_3 | +41,584 | 35.0% | +16,787 | +14,738 | 34.2% | 106 |
| accel_5 | +2,643 | 51.4% | -3,844 | +7,736 | 30.9% | 110 |
| accel_10 | +35,456 | 57.4% | +4,944 | +25,080 | 28.6% | 104 |
| decel_3 | +6,918 | 63.1% | +10,331 | -1,932 | 31.0% | 112 |
| decel_5 | -6,552 | 63.7% | -4,446 | -2,894 | 35.4% | 110 |
| decel_10 | -4,532 | 72.4% | -10,293 | +10,695 | 26.1% | 114 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Rank the same score on a one- to four-week excess window

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| form_7 | +71,098 | 44.9% | +39,218 | +13,235 | 24.1% | 104 |
| form_14 | +129,498 | 50.0% | +59,038 | +20,770 | 33.4% | 98 |
| form_21 | +16,896 | 51.2% | +15,845 | +1,121 | 41.4% | 104 |
| form_28 | +27,634 | 50.3% | +27,309 | +805 | 40.1% | 102 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Rank qualifiers by their largest recent day

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| max_high_5 | +72,436 | 38.4% | +12,236 | +39,148 | 26.7% | 104 |
| max_high_10 | +44,698 | 27.2% | +11,913 | +21,926 | 26.7% | 104 |
| max_high_20 | +36,525 | 47.1% | +8,704 | +20,249 | 26.7% | 100 |
| max_low_5 | -16,592 | 83.8% | -16,183 | -4,436 | 39.6% | 110 |
| max_low_10 | -15,574 | 86.4% | -16,055 | -388 | 38.1% | 112 |
| max_low_20 | -11,343 | 70.8% | -11,504 | -388 | 38.1% | 112 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Rank qualifiers by how much they trade

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| liq_high_10 | -5,556 | 53.9% | -8,341 | +4,421 | 24.6% | 110 |
| liq_high_20 | -3,484 | 50.0% | -6,624 | +4,421 | 24.6% | 108 |
| liq_high_40 | -5,299 | 57.2% | -8,557 | +5,310 | 23.5% | 110 |
| liq_low_10 | -12,054 | 62.3% | -11,613 | -2,113 | 36.1% | 108 |
| liq_low_20 | -12,054 | 62.3% | -11,613 | -2,113 | 36.1% | 108 |
| liq_low_40 | -5,203 | 57.0% | -10,643 | +10,006 | 26.8% | 106 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Skip a new ride while the book's equity is under its own average

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| eq_20 | +264,362 | 29.1% | +115,812 | +24,550 | 27.7% | 70 |
| eq_40 | +22,551 | 30.3% | +12,858 | -3,748 | 36.7% | 62 |
| eq_60 | -420 | 34.7% | -1,750 | -1,872 | 29.2% | 44 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Buy a qualifier only if it did or did not just print a high

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| break_above_1 | -402 | 43.8% | -7,116 | +10,030 | 33.6% | 74 |
| break_above_2 | +8,162 | 42.6% | -2,755 | +12,566 | 30.8% | 58 |
| break_above_3 | +8,162 | 42.6% | -2,755 | +12,566 | 30.8% | 58 |
| break_inside_1 | +39,421 | 52.7% | +31,867 | +3,806 | 37.3% | 98 |
| break_inside_2 | +54,687 | 47.0% | +31,867 | +10,042 | 31.6% | 100 |
| break_inside_3 | +50,897 | 49.7% | +29,088 | +10,042 | 31.6% | 98 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Sell the same day a close is through a loss from the entry

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| stop_5 | +151,714 | 37.7% | +30,172 | +53,018 | 26.7% | 104 |
| stop_8 | +320,299 | 34.0% | +139,934 | +30,791 | 31.9% | 100 |
| stop_12 | +342,074 | 34.1% | +151,798 | +30,791 | 31.9% | 100 |

Does not clear the bar: the cells do not form a region above the baseline on both windows; no cell reaches the size bar.

## Open a new ride only while BTC's time above its average is in range

| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |
|---|---:|---:|---:|---:|---:|---:|
| reclaim_wait_2 | +110,380 | 47.1% | +53,622 | +18,586 | 31.7% | 98 |
| reclaim_wait_4 | +25,267 | 80.0% | +12,855 | +8,122 | 41.4% | 88 |
| reclaim_wait_8 | +32,323 | 76.8% | +10,653 | +14,700 | 31.7% | 78 |
| reclaim_fresh_2 | +52,652 | 18.2% | +26,550 | +11,755 | 12.3% | 32 |
| reclaim_fresh_4 | +40,294 | 21.6% | +26,550 | +6,445 | 21.0% | 34 |
| reclaim_fresh_8 | +40,695 | 19.6% | +28,232 | +5,992 | 19.2% | 48 |

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

A wider trail while the held name is still the leader gives the path back. Fifteen percent off the peak finishes at +€83,515. Twenty percent is +€234,265 and twenty-five is +€257,067, against +€342,074. Fresh 2026 is behind on the two wider cells. Opening a new ride only when few names clear the floor destroys the compounded path. The two-name cap is +€18,916 over the full path. Opening only when at least three names clear the floor is the first cell over the full-path size bar: +€488,027 against +€342,074, +€174,489 through 2025 against +€151,798, drawdown 34.7%. A fresh 2026 book is +€15,239 against +€30,791. Two and four names do not come with it. The count stays where it was locked. Cancelling a buy on a 2, 4 or 6 percent gap down changes no fill.

Refusing a rotation unless the challenger leads by 2, 5 or 8 points of excess gives the path back. The three cells finish between +€250,554 and +€275,634 against +€342,074, and a fresh 2026 book is between +€17,829 and +€21,024. Opening a new ride only when BTC is at most 5, 10 or 20 percent above its SMA50 finishes between a few hundred euros and +€87,575. Requiring BTC to be at least 2 percent above that average is ahead on a fresh 2026 book, +€35,324 against +€30,791, and the full path is +€169,881 with the book through 2025 at +€54,360. Five and ten percent do not come with it. Dropping a name whose own 10-day return is below zero, or below 3 percent, is the same cell: +€331,233 against +€342,074, and a fresh 2026 book is +€28,516. The book through 2025 is unchanged. Six percent is further behind.

Ranking the names that clear the floor by whether their excess is rising or fading gives the path back. A rising excess over 3, 5 or 10 days finishes between +€2,643 and +€41,584 against +€342,074. A fading excess finishes between −€6,552 and +€6,918. Both directions were locked before the run. The live excess rank stays.

Moving the same score onto a 7, 14, 21 or 28 day window gives the path back. The best of them is 14 days, +€129,498 against +€342,074, with a fresh 2026 book at +€20,770. Three and four weeks, the published peak of that band, finish at +€16,896 and +€27,634. Ranking qualifiers by their largest day prefers the spike at 5, 10 or 20 days and finishes between +€36,525 and +€72,436. The 5-day spike is ahead on a fresh 2026 book, +€39,148 against +€30,791, and the book through 2025 is +€12,236. Avoiding that spike loses the stake on every window. Ranking by quote volume, louder or quieter, finishes within a few thousand euros of flat. The 10-day residual leader stays.

Sitting out a new ride while the book's own equity is under its 20-day average finishes at +€264,362 against +€342,074, and a fresh 2026 book is +€24,550. Forty and sixty days give the stake back. Buying only a qualifier that just closed above its prior 1, 2 or 3 day high finishes between a small loss and +€8,162. Buying only a qualifier that stayed inside that high finishes between +€39,421 and +€54,687. Neither side reaches the size bar.

A close 5 percent under the entry sells the same day and finishes at +€151,714 against +€342,074. A fresh 2026 book is +€53,018, ahead of the fresh size bar, and the book through 2025 is +€30,172. Eight percent leaves fresh 2026 unchanged and costs about €22k on the full path. Twelve percent changes no fill. Opening a new ride only after BTC has spent 2, 4 or 8 days above its average, or only during those first days, finishes between +€25,267 and +€110,380. The day count and the loss level stay where they were locked.

## Verdict

Nothing cleared the pre-registered bar. The live residual pack stays the book. Nothing is armed.

## Liquid names, re-picked every day

Same rules, on every Bitvavo EUR name in `data/research/bitvavo_1d_all` through 2026-10-31.
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


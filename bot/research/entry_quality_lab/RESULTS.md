# Entry-quality and peak-cashing lab

Question (owner, 18 Oct 2026): the bot "buys high", ignores news and
buy/sell order flow, and does not cash momentum peaks. Does any of that,
turned into a generic rule, improve the armed `residual_full` pack?

Setup: the live pack (`LIVE_KNOBS` + `trail10_lock12_6`), EUR 20k start,
compounding, WET fill model, 16-name universe, Bitvavo daily bars merged
fresh through 2026-10-18. Windows: full 2024-03-16 → 2026-10-18, IS →
2025-12-31, OOS 2026. Every overlay is a generic gate or size scale evaluated
on the signal close; buys fill next open. Order flow comes from Binance spot
`<BASE>USDT` 1d klines (taker-buy base volume and trade count), cached in
`data/entry_quality_flow/`.

Run: `.venv/bin/python -m bot.research.entry_quality_lab --forensic OP:2026-10-14`

## Baseline

| variant | full PnL | max DD | trades | IS | OOS | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline (live pack) | 348,788 | 32.9% | 99 | 151,798 | 31,121 | 132,986 | 18,811 | 196,990 |

## 1. "Never buy high" (location)

| variant | full PnL | DD | n | IS | OOS |
|---|---:|---:|---:|---:|---:|
| yday_retrace_max8 (skip if yesterday < -8%) | 324,752 | 32.8% | 97 | 139,064 | 31,121 |
| nearhigh0 (drop the near-high weight) | 305,577 | 32.7% | 95 | 145,105 | 26,663 |
| offhigh60_within10 (only within 10% of 60d high) | 159,419 | 39.8% | 54 | 31,277 | 52,221 |
| ext20_scale8_25 (size down when >8% above SMA20) | 147,743 | 27.4% | 99 | 46,532 | 22,587 |
| yday_retrace_max5 | 120,703 | 39.3% | 89 | 98,666 | 6,890 |
| ext20_max30 (skip >30% above SMA20) | 86,293 | 42.8% | 85 | 15,623 | 41,649 |
| skip0 (rank includes yesterday) | 69,834 | 33.0% | 97 | 23,183 | 23,176 |
| ext20_max20 | 34,965 | 35.6% | 75 | 10,661 | 16,452 |
| skip2 | 24,566 | 45.0% | 101 | 16,388 | 5,200 |
| rsi_max80 | 20,933 | 48.0% | 85 | 6,576 | 11,171 |
| ext20_max16 | 17,182 | 28.5% | 60 | -119 | 17,399 |
| offhigh60_atleast10 (buy only >=10% below 60d high) | 7,730 | 28.9% | 59 | 7,867 | 79 |
| offhigh60_atleast20 | 3,612 | 26.7% | 18 | 290 | 3,278 |
| ext20_max8 | -3,305 | 35.0% | 26 | -2,690 | -725 |
| range20_max80 (not in top 20% of 20d range) | -2,710 | 44.0% | 55 | -1,562 | -1,283 |
| rsi_max70 | -7,177 | 49.2% | 59 | -2,768 | -5,152 |
| rsi_max60 | -5,090 | 35.6% | 16 | -5,050 | -65 |

Every rule that refuses extended names removes the profit. The pack earns
its money on exactly the entries that look "too high" (RSI 70–88, 10–35%
above SMA20, top of the 20d range). Buying pulled-back names
(`offhigh60_atleast*`) is close to zero over 2.5 years. The one gate that
would have blocked OP on 2026-10-14 (yesterday -11%) costs 7% at -8% and
65% at -5%.

## 2. "News" (impulse concentration, abnormal volume)

| variant | full PnL | DD | n | IS | OOS |
|---|---:|---:|---:|---:|---:|
| volsurge3_min2 (require a volume event) | 119,902 | 44.0% | 76 | 17,774 | 55,659 |
| impulse_max90 (one candle < 90% of window return) | 103,815 | 29.4% | 72 | 38,838 | 24,271 |
| volsurge3_max5 (skip if 3d max vol > 5x median) | 97,274 | 27.0% | 76 | 39,841 | 20,780 |
| impulse_max50 | 59,589 | 41.6% | 54 | 55,969 | 1,878 |
| volsurge3_max3 | 3,400 | 34.5% | 56 | 1,858 | 1,456 |
| volsurge3_max2 | -7,686 | 45.5% | 36 | -4,723 | -3,947 |

OP on 2026-10-14 had `impulse_share 1.24` (the 13-Oct candle was more than
the whole 10-day return) and `volume_surge3 6.2x`; every news proxy would
have skipped it. Applied to all entries the same proxies cut PnL by 65–100%:
most winners were also news candles. There is no historical headline feed to
test sentiment directly; volume and single-candle concentration are the
measurable stand-ins and they do not separate winners from losers.

## 3. "Buy vs sell orders" (Binance taker flow and trade count)

| variant | full PnL | DD | n | IS | OOS |
|---|---:|---:|---:|---:|---:|
| taker10_min48 (10d taker-buy ratio >= 0.48) | 382,596 | 33.0% | 95 | 162,503 | 32,630 |
| taker3_min48 | 337,296 | 32.8% | 95 | 139,288 | 32,630 |
| taker1_min48 | 180,456 | 36.8% | 79 | 98,111 | 17,452 |
| taker3_scale46_54 (size by 3d ratio) | 150,364 | 22.5% | 99 | 76,407 | 17,544 |
| trades3_min1.2 (trade count surge >= 1.2x) | 113,525 | 24.4% | 68 | 52,169 | 18,714 |
| taker1_min50 | 85,372 | 35.5% | 44 | 30,817 | 22,713 |
| taker3_min50 | 72,866 | 32.3% | 67 | 42,080 | 11,757 |
| taker10_min50 / delta10_pos | 59,581 | 44.3% | 66 | 15,334 | 26,185 |
| takerz3_min1 (buyers z >= 1) | 12,140 | 14.0% | 12 | 11,479 | 466 |
| taker3_min52 | 12,041 | 18.1% | 10 | 5,730 | 4,963 |
| taker3_max50 (prefer sellers) | -5,490 | 54.7% | 61 | 5,669 | -8,630 |

Daily taker-buy ratios on these names live in 0.47–0.53. Requiring buyers
to dominate (>= 0.50 or z >= 1) removes 30–90% of the trades and the PnL
with them. `taker10_min48` is +10% but only drops 4 of 99 trades; it is the
kind of difference one trade explains, not a signal. OP on 2026-10-14:
taker1 0.485, taker3 0.505, taker10 0.500 — flat, no warning in either
direction.

## 4. "Peaks are not cashed" (exit policies on top of trail10)

| variant | full PnL | DD | n | IS | OOS |
|---|---:|---:|---:|---:|---:|
| atr3.5 (extra 3.5x ATR14 trail) | 444,112 | 33.1% | 99 | 151,798 | 45,226 |
| trail10_nolock | 348,990 | 32.9% | 99 | 151,906 | 31,121 |
| donch10 | 348,788 | 32.9% | 99 | 151,798 | 31,121 |
| lock15_10 | 335,619 | 32.8% | 99 | 151,906 | 31,121 |
| half_at15 (sell 50% at +15%) | 333,688 | 31.6% | 115 | 156,101 | 27,722 |
| half_at20 | 310,713 | 31.5% | 110 | 146,843 | 26,778 |
| time10 | 310,026 | 40.2% | 104 | 110,585 | 37,919 |
| third_at25 | 269,110 | 32.2% | 106 | 128,407 | 25,476 |
| lock8_3 | 257,265 | 32.6% | 101 | 98,530 | 31,961 |
| half_at30 | 242,505 | 29.4% | 106 | 122,712 | 22,742 |
| tp50 (full take-profit at +50%) | 198,151 | 32.4% | 103 | 101,200 | 21,368 |
| spike10_40_gb5_sameday | 171,750 | 39.5% | 105 | 92,429 | 18,493 |
| spike8_30_gb4 | 91,033 | 32.6% | 107 | 38,946 | 19,920 |
| trail8 | 80,404 | 30.8% | 106 | 76,790 | 3,309 |
| tp30 | 70,986 | 51.5% | 105 | 27,611 | 20,415 |
| tp20 | 68,580 | 42.7% | 105 | 30,662 | 17,121 |
| trail15 | 53,322 | 60.9% | 107 | 19,796 | 16,942 |
| tp15 | 50,635 | 42.9% | 107 | 16,377 | 20,369 |
| time5 | 49,772 | 34.0% | 101 | 45,317 | 2,811 |
| trail6 | 47,464 | 28.7% | 108 | 31,237 | 7,887 |
| trail12 | 44,622 | 47.5% | 97 | 18,803 | 14,431 |

Cashing peaks earlier (full take-profits, tighter trails, spike give-backs,
time stops) costs 45–85% of the PnL: the pack's return is a few runners that
a +20/+30% target would have sold. Selling half at +15% is roughly neutral
(-4%, DD -1.3pt). `atr3.5` is IS-identical to baseline and differs in one
trade (ARB sold 2026-05-01 instead of 05-04), after which the path happens
to land in a better week; that is one event, not an exit rule.

## Path fragility (context, not in the grid)

| trail | full PnL | DD |
|---|---:|---:|
| 9% | 261,986 | 31.0% |
| 9.5% | 277,529 | 31.2% |
| 10% (live) | 348,788 | 32.9% |
| 10.5% | 182,050 | 41.2% |
| 11% | 64,704 | 48.5% |
| n_alts=2 | 27,766 | 41.7% |
| n_alts=3 | 18,162 | 34.4% |

One position, one weekly pick, ~40 roundtrips in 2.5 years, compounding:
half a percentage point on the trail moves the result by a factor of 2–5.
This is the real weakness of the pack. None of the four owner hypotheses
addresses it, and none of their generic forms improves the replay.

## This week in the live ledger

The clip's own trade was OP: bought 2026-10-15 00:00 UTC at 0.12596, sold on
the 2026-10-18 daily close by the 10% trail (peak 0.12767 → close 0.11488),
-1,055.83 EUR (-9.0%). The other exits booked under `manual_external` /
`untracked_roundtrip` this week (ZEUS -3,333.89, XDP -302.98, GTC +414.93,
SUI -580.19) are Bitvavo-UI roundtrips the clip reconciled into its realized
total; the engine did not place those buys.

## Verdict

- "Never buy high": rejected. The edge is buying extended names.
- "News": no feed to test; the measurable proxies (single-candle share,
  volume surge) do not separate winners from losers and cost 65–100%.
- "Buy vs sell orders": flat ratios, no information at daily resolution;
  every meaningful threshold loses money.
- "Cash the peaks": rejected for full take-profits and tighter trails;
  partial 50% at +15% is the only near-neutral option (slightly lower DD).

Nothing here is a change to arm. The next test that could actually move
robustness is structural (clock staggering / ensemble of trail levels and
pick dates), not a new entry filter.

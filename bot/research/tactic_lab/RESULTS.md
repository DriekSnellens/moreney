# Tactic lab

Question: the published low-turnover momentum designs, and a real
AlphaI allow-list, on wet Bitvavo daily fills.

Time-series momentum is long only while the lookback return is
positive, ranked by excess versus BTC. The multi-horizon trend is
the equal-weight 7/14/28/56 day return, skip one day, long the
highest score that is still positive. No model is fit on this tape.
Clock 7 or 28 days. Trail 10% sells the next open; `hold` has no
trail. BTC below its SMA50 blocks a new buy and flattens on the
clock. €20k compounding, taker 0.25% plus impact.

AlphaI rows use the stored sessions only (as-of 16:00 UTC, the
hour the gate lab found least costly). There is no AlphaI history
before the first stored session, so that gate is not applied to
the long sample.

Daily tape through 2026-10-03.
Reproduce: `.venv/bin/python -m bot.research.tactic_lab --no-fresh`


## Daily full

| variant | PnL | DD | green W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline_residual | 468,677 | 31.2% | 32% | 96 | 132,986 | 18,812 | 316,879 |
| tsmom_7_reb7 | 50,449 | 61.0% | 24% | 44 | 62,237 | -35,518 | 23,730 |
| tsmom_28_reb7 | 23,976 | 64.1% | 22% | 47 | 45,740 | -15,810 | -5,954 |
| tsmom_28_reb28 | -7,875 | 56.3% | 7% | 17 | -3,106 | -1,206 | -3,563 |
| tsmom_28_reb28_hold | -3,411 | 73.1% | 25% | 13 | 12,698 | 10,776 | -26,885 |
| ctrend_reb7 | 21,587 | 64.9% | 22% | 47 | 22,573 | -11,705 | 10,719 |
| ctrend_reb28 | -4,332 | 47.2% | 9% | 16 | -3,106 | 1,122 | -2,348 |
| ctrend_reb28_vol | -1,114 | 63.5% | 6% | 15 | -3,106 | -5,176 | 7,169 |
| ctrend_reb28_hold | -2,883 | 78.0% | 23% | 12 | 21,179 | 9,582 | -33,644 |

## Daily is

| variant | PnL | DD | green W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline_residual | 151,798 | 24.8% | 32% | 64 | 132,986 | 18,812 | 0 |
| tsmom_7_reb7 | 26,719 | 49.5% | 22% | 26 | 62,237 | -35,518 | 0 |
| tsmom_28_reb7 | 29,930 | 49.2% | 23% | 31 | 45,740 | -15,810 | 0 |
| tsmom_28_reb28 | -4,312 | 48.0% | 5% | 10 | -3,106 | -1,206 | 0 |
| tsmom_28_reb28_hold | 23,474 | 53.5% | 25% | 8 | 12,698 | 10,776 | 0 |
| ctrend_reb7 | 10,868 | 42.9% | 22% | 32 | 22,573 | -11,705 | 0 |
| ctrend_reb28 | -1,984 | 47.2% | 6% | 10 | -3,106 | 1,122 | 0 |
| ctrend_reb28_vol | -8,282 | 49.2% | 3% | 9 | -3,106 | -5,176 | 0 |
| ctrend_reb28_hold | 30,762 | 53.5% | 23% | 7 | 21,179 | 9,582 | 0 |

## Daily oos

| variant | PnL | DD | green W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline_residual | 46,533 | 26.7% | 32% | 32 | 0 | 0 | 46,533 |
| tsmom_7_reb7 | 13,224 | 27.8% | 30% | 17 | 0 | 0 | 13,224 |
| tsmom_28_reb7 | -213 | 37.0% | 18% | 16 | 0 | 0 | -213 |
| tsmom_28_reb28 | -871 | 20.4% | 10% | 5 | 0 | 0 | -871 |
| tsmom_28_reb28_hold | 4,154 | 33.2% | 25% | 4 | 0 | 0 | 4,154 |
| ctrend_reb7 | 8,162 | 38.8% | 22% | 15 | 0 | 0 | 8,162 |
| ctrend_reb28 | -871 | 20.4% | 10% | 5 | 0 | 0 | -871 |
| ctrend_reb28_vol | -2,816 | 18.4% | 2% | 3 | 0 | 0 | -2,816 |
| ctrend_reb28_hold | 4,154 | 33.2% | 25% | 4 | 0 | 0 | 4,154 |

## AlphaI sessions

| variant | PnL | DD | green W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline_residual | 6,948 | 10.7% | 67% | 2 | 0 | 0 | 6,948 |
| tsmom_28_reb7 | -1,073 | 17.1% | 33% | 2 | 0 | 0 | -1,073 |
| tsmom_28_reb7_alphai | 1,900 | 2.8% | 33% | 0 | 0 | 0 | 1,900 |
| ctrend_reb7 | -1,073 | 17.1% | 33% | 2 | 0 | 0 | -1,073 |
| ctrend_reb7_alphai | 1,900 | 2.8% | 33% | 0 | 0 | 0 | 1,900 |


## Reading

The armed residual pack is the profitable book on this tape: +€468,677
over the full path, +€151,798 in-sample, +€46,533 on a fresh 2026 book,
drawdown 31.2%. Everything below is a published rule on the same wet fills.

1. **A weekly time-series momentum book nets after taker fees, and it is
   the smaller book.** `tsmom_7_reb7` is long only while the 7-day return
   is positive, ranked by excess versus BTC, trailed 10%. +€26,719
   in-sample, +€13,224 in 2026, full path +€50,449 at 61% drawdown.
   2025 is −€35,518. Green weeks are 24% against 32% for the residual pack.
2. **The multi-horizon trend, equal-weight 7/14/28/56 with no model fit,
   also nets and also lags the pack.** `ctrend_reb7` is +€10,868 in-sample
   and +€8,162 in 2026, full path +€21,587 at 65% drawdown. Adding a
   volume-expansion filter on the 28-day clock (`ctrend_reb28_vol`) is
   negative on the full path.
3. **The four-week hold the papers use to survive costs does not survive
   this tape.** `tsmom_28_reb28` and `ctrend_reb28` are negative in-sample
   and on the fresh 2026 book. Dropping the trail (`*_hold`) is positive
   in-sample (+€23k / +€31k) and a thin +€4,154 in 2026, and the full path
   is negative because 2026 on the compounded book gives the earlier gain
   back.
4. **AlphaI has 13 stored sessions, 2026-09-19 through 2026-10-03.** On
   those days the residual pack is +€6,948. The same window with the
   published signals is −€1,073. Restricting those signals to that day's
   AlphaI picks leaves one open name, marked +€1,900, with no closed round
   trip. The gate lab on this same store already measured the residual
   pack itself: a hard AlphaI gate went to −€1,004 or to cash, and the
   least costly intersect (as-of 16:00) cut +€6,948 to +€1,944.

## Verdict

Nothing new armed. The profitable way after Bitvavo taker fees is the
book already live: 10-day skip-1 excess, 7-day clock, 10% trail. The
online designs that claim to survive costs do net when the clock stays
at a week and the name's own return is still positive, and they make
roughly a tenth of that pack at about twice the drawdown. Stretching the
hold to four weeks, which is how those papers keep turnover down, gives
the gain back. AlphaI cannot be a two-year filter: the stored sessions
cover thirteen days, and on those days it does not improve the residual
pack.

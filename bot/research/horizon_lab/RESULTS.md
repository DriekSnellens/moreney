# Day and week horizon lab

Question: the residual pack holds one name for weeks, and the minute
desk loses to fees. Is there a week clock that recycles dead names
without selling runners, and a day book that is flat by the next
UTC open, that survive costs on this tape?

Week rows use the live residual signal (10d skip-1, floor 3.5%,
SMA50, near-high 0.25, trail 10%, lock +12%/+6%) on Bitvavo daily
bars, wet next-open fills, €20k compounding. `keep` does not sell a
lot whose close has reached the runner arm; `cut` sells it on the
clock like a time box. The day session is a 3-day residual, one
UTC session, round-tripped even when the leader does not change.
`day_graduate` leaves that lot on the trail once the session closes
through +8%. The 4h book enters once per UTC day and is sold on the
first open of the next date.

Daily tape through 2026-10-03.
Reproduce: `.venv/bin/python -m bot.research.horizon_lab --no-fresh`


## Daily full

| variant | PnL | DD | green W | red W | worst W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline_residual | 468,677 | 31.2% | 32% | 28% | -15.3% | 96 | 132,986 | 18,812 | 316,879 |
| week_age_cut | 468,677 | 31.2% | 32% | 28% | -15.3% | 96 | 132,986 | 18,812 | 316,879 |
| week_age_keep | 519,464 | 33.7% | 31% | 29% | -15.3% | 88 | 132,986 | 20,130 | 366,347 |
| week_age_keep_arm8 | 553,993 | 33.7% | 31% | 29% | -15.3% | 84 | 132,986 | 20,130 | 400,876 |
| week_age_keep_arm18 | 519,464 | 33.7% | 31% | 29% | -15.3% | 88 | 132,986 | 20,130 | 366,347 |
| week_sun_cut | -17,087 | 87.6% | 13% | 37% | -17.3% | 116 | -13,152 | -2,337 | -1,599 |
| week_sun_keep | -16,741 | 86.7% | 13% | 37% | -17.3% | 110 | -13,152 | -1,999 | -1,590 |
| week_daily_cut | 33,640 | 79.9% | 31% | 34% | -27.2% | 329 | 32,169 | -35,371 | 36,841 |
| week_daily_keep | 45,096 | 77.3% | 33% | 33% | -22.4% | 295 | 33,329 | -34,576 | 46,343 |
| day_session | -13,370 | 81.8% | 24% | 39% | -30.6% | 398 | -5,487 | -8,413 | 530 |
| day_graduate | -9,289 | 78.1% | 23% | 40% | -28.1% | 332 | -3,551 | -5,701 | -37 |
| week_keep_plus_idle_day | 86,716 | 68.6% | 31% | 34% | -30.1% | 254 | 63,765 | -40,362 | 63,313 |

## Daily is

| variant | PnL | DD | green W | red W | worst W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline_residual | 151,798 | 24.8% | 32% | 26% | -15.3% | 64 | 132,986 | 18,812 | 0 |
| week_age_cut | 151,798 | 24.8% | 32% | 26% | -15.3% | 64 | 132,986 | 18,812 | 0 |
| week_age_keep | 153,116 | 33.7% | 30% | 27% | -15.3% | 58 | 132,986 | 20,130 | 0 |
| week_age_keep_arm8 | 153,116 | 33.7% | 30% | 27% | -15.3% | 58 | 132,986 | 20,130 | 0 |
| week_age_keep_arm18 | 153,116 | 33.7% | 30% | 27% | -15.3% | 58 | 132,986 | 20,130 | 0 |
| week_sun_cut | -15,488 | 77.4% | 13% | 35% | -17.3% | 80 | -13,152 | -2,337 | 0 |
| week_sun_keep | -15,151 | 75.8% | 13% | 35% | -17.3% | 76 | -13,152 | -1,999 | 0 |
| week_daily_cut | -3,201 | 77.0% | 25% | 38% | -22.4% | 240 | 32,169 | -35,371 | 0 |
| week_daily_keep | -1,247 | 74.0% | 27% | 36% | -22.4% | 210 | 33,329 | -34,576 | 0 |
| day_session | -13,901 | 72.5% | 24% | 37% | -16.5% | 272 | -5,487 | -8,413 | 0 |
| day_graduate | -9,251 | 63.2% | 23% | 39% | -20.7% | 222 | -3,551 | -5,701 | 0 |
| week_keep_plus_idle_day | 23,403 | 56.6% | 27% | 37% | -28.9% | 186 | 63,765 | -40,362 | 0 |

## Daily oos

| variant | PnL | DD | green W | red W | worst W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline_residual | 46,533 | 26.7% | 32% | 32% | -11.5% | 32 | 0 | 0 | 46,533 |
| week_age_cut | 46,533 | 26.7% | 32% | 32% | -11.5% | 32 | 0 | 0 | 46,533 |
| week_age_keep | 52,909 | 26.7% | 32% | 32% | -11.5% | 30 | 0 | 0 | 52,909 |
| week_age_keep_arm8 | 54,850 | 28.7% | 32% | 32% | -11.5% | 26 | 0 | 0 | 54,850 |
| week_age_keep_arm18 | 52,909 | 26.7% | 32% | 32% | -11.5% | 30 | 0 | 0 | 52,909 |
| week_sun_cut | -7,633 | 50.2% | 15% | 42% | -14.0% | 36 | 0 | 0 | -7,633 |
| week_sun_keep | -7,090 | 50.2% | 15% | 42% | -14.0% | 34 | 0 | 0 | -7,090 |
| week_daily_cut | 42,579 | 27.3% | 45% | 25% | -27.3% | 89 | 0 | 0 | 42,579 |
| week_daily_keep | 48,911 | 21.2% | 45% | 25% | -20.9% | 85 | 0 | 0 | 48,911 |
| day_session | -957 | 40.3% | 22% | 42% | -31.3% | 126 | 0 | 0 | -957 |
| day_graduate | -1,387 | 44.1% | 22% | 42% | -28.5% | 110 | 0 | 0 | -1,387 |
| week_keep_plus_idle_day | 33,590 | 39.1% | 38% | 28% | -29.2% | 68 | 0 | 0 | 33,590 |

## 4h full

| variant | PnL | DD | green W | red W | worst W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| day4h_utc_f2 | -17,301 | 86.5% | 18% | 41% | -24.9% | 173 | -12,108 | 246 | -5,439 |
| day4h_utc_f35 | -18,237 | 91.6% | 16% | 41% | -24.6% | 148 | -9,789 | -4,202 | -4,245 |
| day4h_utc_f6 | -11,094 | 59.5% | 21% | 30% | -23.1% | 106 | -1,734 | -4,562 | -4,798 |
| day4h_utc_f35_trail | -15,983 | 80.9% | 18% | 38% | -23.3% | 178 | -10,534 | -3,454 | -1,995 |
| day4h_open_f35 | -15,155 | 76.6% | 21% | 36% | -28.2% | 233 | -10,826 | -2,058 | -2,271 |
| day4h_utc_f35_n2 | -18,024 | 90.5% | 17% | 40% | -22.5% | 230 | -9,812 | -4,629 | -3,583 |

## 4h is

| variant | PnL | DD | green W | red W | worst W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| day4h_utc_f2 | -11,862 | 70.1% | 20% | 36% | -21.5% | 111 | -12,108 | 246 | 0 |
| day4h_utc_f35 | -13,992 | 74.2% | 16% | 37% | -18.6% | 96 | -9,789 | -4,202 | 0 |
| day4h_utc_f6 | -6,297 | 42.1% | 19% | 27% | -18.5% | 67 | -1,734 | -4,562 | 0 |
| day4h_utc_f35_trail | -13,989 | 72.0% | 18% | 34% | -23.3% | 114 | -10,534 | -3,454 | 0 |
| day4h_open_f35 | -12,884 | 67.1% | 21% | 31% | -28.2% | 148 | -10,826 | -2,058 | 0 |
| day4h_utc_f35_n2 | -14,441 | 75.1% | 16% | 37% | -16.6% | 149 | -9,812 | -4,629 | 0 |

## 4h oos

| variant | PnL | DD | green W | red W | worst W | trades | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| day4h_utc_f2 | -14,643 | 75.1% | 14% | 50% | -25.5% | 62 | 0 | 0 | -14,643 |
| day4h_utc_f35 | -15,237 | 76.2% | 17% | 48% | -25.4% | 52 | 0 | 0 | -15,237 |
| day4h_utc_f6 | -7,780 | 53.0% | 24% | 33% | -23.4% | 39 | 0 | 0 | -7,780 |
| day4h_utc_f35_trail | -9,667 | 50.5% | 19% | 45% | -20.1% | 64 | 0 | 0 | -9,667 |
| day4h_open_f35 | -9,962 | 54.5% | 19% | 45% | -24.9% | 85 | 0 | 0 | -9,962 |
| day4h_utc_f35_n2 | -14,115 | 71.2% | 17% | 48% | -23.0% | 81 | 0 | 0 | -14,115 |

## Reading

`week_age_cut` matches the armed residual pack to the euro (same PnL, same
drawdown, same 96 trades). The new engine is the live book when the runner
exemption is off. Everything below is a difference from that book, not a
different simulator.

1. **A day book gives the money back to fees.** On daily bars the one-session
   residual (`day_session`) is −€13.9k in-sample and −€1.0k out of sample.
   Letting a session that closed through +8% graduate onto the trail
   (`day_graduate`) is still negative on both sides. On 4h bars, buying the
   leader once per UTC day and selling the next midnight open loses at every
   excess floor (2%, 3.5%, 6%), with and without a 6% trail, for one name
   and for two. Round-trip taker cost is about 0.5% plus impact; the
   forward day does not clear it. Adding that sleeve on the weeks the
   residual book is in cash (`week_keep_plus_idle_day`) cuts the keep book
   from +€519k to +€87k.
2. **A shorter week is the same result the consistency lab already had.**
   Recycling every day (`week_daily_cut` / `week_daily_keep`) finishes
   in-sample flat to negative and carries a 74–80% drawdown. Pinning the
   clock to Sunday (`week_sun_*`) loses in 2024, 2025 and 2026.
3. **Not selling a runner on the 7-day clock is the only positive delta,
   and it is two episodes.** `week_age_keep` is +€153k IS versus +€152k
   for the live pack, and +€53k versus +€47k on a fresh 2026 book. The
   trade diff is a clock sell the keep book refused: SUI on 2025-05-12
   (held to the trail four days later) and LINK on 2026-08-25 (held one
   more day). The other sells in the diff are the path that followed from
   still being in that name. Arms at +8%, +12% and +18% are the same book
   through 2025; only 2026 moves, and by one further hold. Drawdown on the
   full path is worse (33.7% versus 31.2%). Green weeks do not improve
   (31% versus 32%).

## Verdict

Nothing armed. Day trading on this tape, flat by the next UTC open, does
not survive Bitvavo taker fees. Forcing the residual book to act like a
week time-box gives back the trends that pay it. Leaving a name that has
already closed through the runner arm on the trail, instead of rotating
it on the clock, is a generic rule and it does not lose — but the extra
euros are two holds, and the share of green weeks does not rise. The live
pack stays the 7-day residual with the 10% trail.


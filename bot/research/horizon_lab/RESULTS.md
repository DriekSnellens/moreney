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

Bank rows are a 4h momentum book on the same desk universe.
Entry is the next open after a close whose own lookback return
is positive and whose excess versus BTC clears the floor. A
target fills at the target when the high trades through it. A
stop fills at the stop when the low trades through it; if both
print in one bar the stop is booked. `hold` has no target and
no stop and sells the next open once the lookback return is no
longer positive. `bank_w_follow` is that 7-day hold plus a 5%
stop. A trail sells inside the bar when the low trades through
a retrace from the prior peak. A lock raises that floor once
the prior peak has reached the arm. Fills are wet Bitvavo
taker, €20k compounding.
`baseline_from_4h_start` is the armed residual pack on daily
bars from the same start date (printed in the run log).

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

## Bank full

| variant | PnL | DD | green W | red W | worst W | trades | tp | stop | fail | trail | lock | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bank_d_tp2_s2 | -19,999 | 100.0% | 5% | 58% | -40.4% | 1118 | 452 | 595 | 71 | 0 | 0 | -19,766 | -227 | -7 |
| bank_d_tp3_s2 | -19,996 | 100.0% | 8% | 58% | -36.8% | 1053 | 351 | 609 | 93 | 0 | 0 | -19,467 | -485 | -43 |
| bank_d_tp3_s3 | -19,985 | 99.9% | 13% | 54% | -33.5% | 903 | 381 | 381 | 141 | 0 | 0 | -19,171 | -738 | -76 |
| bank_d_tp4_s3 | -19,961 | 99.8% | 14% | 53% | -38.7% | 788 | 267 | 352 | 169 | 0 | 0 | -19,113 | -760 | -87 |
| bank_d_tp6_s4 | -19,925 | 99.6% | 14% | 52% | -37.2% | 600 | 158 | 221 | 221 | 0 | 0 | -18,073 | -1,671 | -180 |
| bank_d_follow | -18,944 | 95.3% | 18% | 48% | -27.1% | 363 | 0 | 121 | 242 | 0 | 0 | -15,418 | -2,692 | -834 |
| bank_d_tp3_part | -18,866 | 94.8% | 20% | 46% | -23.7% | 664 | 203 | 287 | 174 | 0 | 0 | -14,280 | -3,875 | -710 |
| bank_d_tp3_n2 | -19,978 | 99.9% | 8% | 58% | -27.4% | 1536 | 598 | 635 | 303 | 0 | 0 | -19,047 | -837 | -94 |
| bank_w_tp4_s3 | -19,941 | 99.7% | 15% | 49% | -29.4% | 730 | 287 | 415 | 28 | 0 | 0 | -18,422 | -1,281 | -238 |
| bank_w_tp6_s4 | -19,829 | 99.2% | 17% | 48% | -26.4% | 447 | 150 | 246 | 51 | 0 | 0 | -17,322 | -2,243 | -263 |
| bank_w_follow | -5,071 | 76.4% | 28% | 37% | -32.0% | 124 | 0 | 55 | 69 | 0 | 0 | 3,919 | -8,382 | -608 |
| bank_d_tp3_norisk | -19,999 | 100.0% | 8% | 57% | -32.8% | 1046 | 404 | 452 | 190 | 0 | 0 | -19,859 | -140 | 0 |
| bank_w_f3_hold | 15,440 | 81.7% | 31% | 35% | -35.2% | 91 | 0 | 0 | 91 | 0 | 0 | 42,182 | -31,539 | 4,797 |
| bank_w_f4_hold | 19,655 | 79.9% | 31% | 35% | -35.3% | 84 | 0 | 0 | 84 | 0 | 0 | 49,129 | -26,916 | -2,559 |
| bank_w_f6_hold | 22,766 | 78.8% | 30% | 34% | -35.4% | 74 | 0 | 0 | 74 | 0 | 0 | 58,259 | -30,369 | -5,125 |
| bank_w_f8_hold | 29,428 | 75.5% | 30% | 34% | -35.5% | 64 | 0 | 0 | 64 | 0 | 0 | 69,482 | -32,497 | -7,557 |
| bank_d_f4_hold | -15,664 | 84.9% | 18% | 46% | -22.6% | 229 | 0 | 0 | 229 | 0 | 0 | -5,056 | -9,379 | -1,229 |
| bank_w_f4_tp4 | -16,273 | 83.5% | 28% | 38% | -22.7% | 237 | 153 | 0 | 84 | 0 | 0 | -8,187 | -5,919 | -2,167 |
| bank_w_f4_s8 | -11,103 | 82.4% | 27% | 38% | -38.5% | 98 | 0 | 31 | 67 | 0 | 0 | -6,779 | -2,259 | -2,065 |
| bank_w_f4_tp4s8 | -18,906 | 95.0% | 21% | 44% | -20.8% | 370 | 228 | 96 | 46 | 0 | 0 | -12,870 | -5,007 | -1,029 |
| bank_d_f8_hold | -13,019 | 78.0% | 18% | 39% | -21.8% | 144 | 0 | 0 | 144 | 0 | 0 | -2,046 | -5,445 | -5,528 |
| bank_w_f8_tp4 | -12,535 | 77.7% | 26% | 37% | -20.0% | 203 | 144 | 0 | 59 | 0 | 0 | 5,518 | -13,085 | -4,969 |
| bank_w_f8_s8 | -8,445 | 75.4% | 28% | 34% | -38.9% | 78 | 0 | 28 | 50 | 0 | 0 | -3,291 | -4,698 | -457 |
| bank_w_f8_tp4s8 | -18,084 | 93.0% | 23% | 38% | -22.6% | 323 | 208 | 88 | 27 | 0 | 0 | -8,784 | -7,005 | -2,294 |
| bank_w_f4_trail10 | -18,022 | 93.1% | 20% | 45% | -32.9% | 167 | 0 | 0 | 62 | 105 | 0 | -10,464 | -4,870 | -2,689 |
| bank_w_f4_lock | -18,060 | 93.5% | 20% | 45% | -32.9% | 171 | 0 | 0 | 60 | 95 | 16 | -9,845 | -6,071 | -2,143 |
| bank_w_f4_ratchet | -7,564 | 87.2% | 25% | 40% | -25.8% | 168 | 0 | 0 | 70 | 0 | 98 | 11,636 | -15,282 | -3,918 |
| bank_w_f8_trail10 | -16,622 | 89.6% | 22% | 40% | -34.4% | 142 | 0 | 0 | 40 | 102 | 0 | -6,987 | -6,676 | -2,959 |
| bank_w_f8_lock | -16,995 | 91.3% | 22% | 39% | -34.1% | 149 | 0 | 0 | 42 | 89 | 18 | -8,031 | -7,450 | -1,514 |
| bank_w_f8_ratchet | -5,487 | 72.5% | 26% | 36% | -26.4% | 147 | 0 | 0 | 48 | 0 | 99 | 7,007 | -7,590 | -4,904 |
| bank_d_f4_trail10 | -17,249 | 87.0% | 19% | 45% | -22.7% | 257 | 0 | 0 | 200 | 57 | 0 | -11,336 | -5,642 | -272 |
| bank_d_f4_lock | -17,691 | 88.6% | 18% | 46% | -22.7% | 265 | 0 | 0 | 197 | 53 | 15 | -11,792 | -5,425 | -474 |
| bank_d_f4_ratchet | -16,777 | 84.0% | 22% | 42% | -31.8% | 324 | 0 | 0 | 162 | 0 | 162 | -11,163 | -4,560 | -1,054 |

## Bank is

| variant | PnL | DD | green W | red W | worst W | trades | tp | stop | fail | trail | lock | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bank_d_tp2_s2 | -19,992 | 100.0% | 4% | 60% | -40.4% | 804 | 315 | 450 | 39 | 0 | 0 | -19,766 | -227 | 0 |
| bank_d_tp3_s2 | -19,952 | 99.8% | 7% | 56% | -36.8% | 696 | 233 | 415 | 48 | 0 | 0 | -19,467 | -485 | 0 |
| bank_d_tp3_s3 | -19,909 | 99.5% | 12% | 51% | -33.5% | 596 | 250 | 268 | 78 | 0 | 0 | -19,171 | -738 | 0 |
| bank_d_tp4_s3 | -19,874 | 99.4% | 10% | 54% | -38.7% | 511 | 164 | 247 | 100 | 0 | 0 | -19,113 | -760 | 0 |
| bank_d_tp6_s4 | -19,744 | 98.7% | 13% | 50% | -37.2% | 386 | 100 | 154 | 132 | 0 | 0 | -18,073 | -1,671 | 0 |
| bank_d_follow | -18,110 | 90.5% | 18% | 45% | -27.1% | 231 | 0 | 87 | 144 | 0 | 0 | -15,418 | -2,692 | 0 |
| bank_d_tp3_part | -18,155 | 90.8% | 18% | 45% | -23.7% | 428 | 129 | 199 | 100 | 0 | 0 | -14,280 | -3,875 | 0 |
| bank_d_tp3_n2 | -19,884 | 99.4% | 7% | 56% | -27.4% | 1007 | 386 | 451 | 170 | 0 | 0 | -19,047 | -837 | 0 |
| bank_w_tp4_s3 | -19,703 | 98.5% | 12% | 50% | -29.4% | 469 | 179 | 271 | 19 | 0 | 0 | -18,422 | -1,281 | 0 |
| bank_w_tp6_s4 | -19,565 | 97.8% | 13% | 49% | -24.5% | 301 | 96 | 174 | 31 | 0 | 0 | -17,322 | -2,243 | 0 |
| bank_w_follow | -4,463 | 65.6% | 27% | 34% | -25.8% | 81 | 0 | 39 | 42 | 0 | 0 | 3,919 | -8,382 | 0 |
| bank_d_tp3_norisk | -19,999 | 100.0% | 12% | 84% | -32.8% | 1046 | 404 | 452 | 190 | 0 | 0 | -19,859 | -140 | 0 |
| bank_w_f3_hold | 10,643 | 73.3% | 31% | 32% | -23.5% | 60 | 0 | 0 | 60 | 0 | 0 | 42,182 | -31,539 | 0 |
| bank_w_f4_hold | 22,213 | 65.9% | 31% | 32% | -23.6% | 53 | 0 | 0 | 53 | 0 | 0 | 49,129 | -26,916 | 0 |
| bank_w_f6_hold | 27,891 | 62.4% | 30% | 31% | -22.0% | 46 | 0 | 0 | 46 | 0 | 0 | 58,259 | -30,369 | 0 |
| bank_w_f8_hold | 36,985 | 56.9% | 29% | 31% | -22.0% | 39 | 0 | 0 | 39 | 0 | 0 | 69,482 | -32,497 | 0 |
| bank_d_f4_hold | -14,436 | 80.6% | 16% | 46% | -22.3% | 153 | 0 | 0 | 153 | 0 | 0 | -5,056 | -9,379 | 0 |
| bank_w_f4_tp4 | -14,106 | 71.9% | 27% | 34% | -22.7% | 148 | 93 | 0 | 55 | 0 | 0 | -8,187 | -5,919 | 0 |
| bank_w_f4_s8 | -9,038 | 65.4% | 26% | 36% | -20.9% | 59 | 0 | 20 | 39 | 0 | 0 | -6,779 | -2,259 | 0 |
| bank_w_f4_tp4s8 | -17,877 | 89.4% | 19% | 43% | -20.8% | 245 | 151 | 69 | 25 | 0 | 0 | -12,870 | -5,007 | 0 |
| bank_d_f8_hold | -7,491 | 60.5% | 18% | 39% | -17.8% | 94 | 0 | 0 | 94 | 0 | 0 | -2,046 | -5,445 | 0 |
| bank_w_f8_tp4 | -7,566 | 62.8% | 26% | 32% | -17.8% | 130 | 92 | 0 | 38 | 0 | 0 | 5,518 | -13,085 | 0 |
| bank_w_f8_s8 | -7,989 | 63.9% | 26% | 32% | -22.1% | 48 | 0 | 19 | 29 | 0 | 0 | -3,291 | -4,698 | 0 |
| bank_w_f8_tp4s8 | -15,789 | 84.6% | 20% | 37% | -22.6% | 215 | 140 | 61 | 14 | 0 | 0 | -8,784 | -7,005 | 0 |
| bank_w_f4_trail10 | -15,334 | 82.4% | 18% | 44% | -29.8% | 106 | 0 | 0 | 36 | 70 | 0 | -10,464 | -4,870 | 0 |
| bank_w_f4_lock | -15,916 | 85.2% | 18% | 44% | -29.8% | 109 | 0 | 0 | 35 | 63 | 11 | -9,845 | -6,071 | 0 |
| bank_w_f4_ratchet | -3,646 | 72.0% | 26% | 36% | -25.8% | 103 | 0 | 0 | 43 | 0 | 60 | 11,636 | -15,282 | 0 |
| bank_w_f8_trail10 | -13,663 | 79.9% | 18% | 40% | -22.5% | 89 | 0 | 0 | 23 | 66 | 0 | -6,987 | -6,676 | 0 |
| bank_w_f8_lock | -15,481 | 85.9% | 18% | 40% | -31.8% | 94 | 0 | 0 | 24 | 59 | 11 | -8,031 | -7,450 | 0 |
| bank_w_f8_ratchet | -583 | 59.8% | 24% | 33% | -25.7% | 88 | 0 | 0 | 28 | 0 | 60 | 7,007 | -7,590 | 0 |
| bank_d_f4_trail10 | -16,978 | 84.9% | 16% | 46% | -22.7% | 172 | 0 | 0 | 135 | 37 | 0 | -11,336 | -5,642 | 0 |
| bank_d_f4_lock | -17,217 | 86.1% | 14% | 48% | -22.7% | 176 | 0 | 0 | 133 | 34 | 9 | -11,792 | -5,425 | 0 |
| bank_d_f4_ratchet | -15,723 | 78.6% | 19% | 43% | -23.0% | 212 | 0 | 0 | 107 | 0 | 105 | -11,163 | -4,560 | 0 |

## Bank oos

| variant | PnL | DD | green W | red W | worst W | trades | tp | stop | fail | trail | lock | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bank_d_tp2_s2 | -19,846 | 99.2% | 2% | 69% | -38.1% | 426 | 159 | 232 | 35 | 0 | 0 | 0 | 0 | -19,846 |
| bank_d_tp3_s2 | -19,658 | 98.4% | 5% | 67% | -34.4% | 365 | 109 | 215 | 41 | 0 | 0 | 0 | 0 | -19,658 |
| bank_d_tp3_s3 | -18,990 | 95.2% | 7% | 64% | -26.4% | 308 | 127 | 121 | 60 | 0 | 0 | 0 | 0 | -18,990 |
| bank_d_tp4_s3 | -18,365 | 92.5% | 12% | 60% | -26.0% | 281 | 102 | 117 | 62 | 0 | 0 | 0 | 0 | -18,365 |
| bank_d_tp6_s4 | -18,446 | 93.0% | 14% | 57% | -29.8% | 220 | 59 | 78 | 83 | 0 | 0 | 0 | 0 | -18,446 |
| bank_d_follow | -14,836 | 77.9% | 19% | 52% | -25.9% | 133 | 0 | 36 | 97 | 0 | 0 | 0 | 0 | -14,836 |
| bank_d_tp3_part | -14,838 | 76.4% | 17% | 55% | -17.1% | 239 | 73 | 99 | 67 | 0 | 0 | 0 | 0 | -14,838 |
| bank_d_tp3_n2 | -18,511 | 92.8% | 2% | 69% | -20.7% | 528 | 203 | 200 | 125 | 0 | 0 | 0 | 0 | -18,511 |
| bank_w_tp4_s3 | -19,134 | 95.8% | 17% | 50% | -29.6% | 259 | 93 | 155 | 11 | 0 | 0 | 0 | 0 | -19,134 |
| bank_w_tp6_s4 | -16,927 | 85.3% | 14% | 52% | -28.7% | 147 | 50 | 79 | 18 | 0 | 0 | 0 | 0 | -16,927 |
| bank_w_follow | -1,183 | 38.9% | 29% | 40% | -32.2% | 43 | 0 | 16 | 27 | 0 | 0 | 0 | 0 | -1,183 |
| bank_d_tp3_norisk | -19,753 | 98.8% | 12% | 88% | -28.6% | 497 | 198 | 179 | 120 | 0 | 0 | 0 | 0 | -19,753 |
| bank_w_f3_hold | 5,307 | 40.0% | 31% | 40% | -35.1% | 31 | 0 | 0 | 31 | 0 | 0 | 0 | 0 | 5,307 |
| bank_w_f4_hold | 2,174 | 39.9% | 33% | 38% | -35.0% | 31 | 0 | 0 | 31 | 0 | 0 | 0 | 0 | 2,174 |
| bank_w_f6_hold | 1,150 | 40.0% | 31% | 40% | -35.0% | 28 | 0 | 0 | 28 | 0 | 0 | 0 | 0 | 1,150 |
| bank_w_f8_hold | 788 | 40.0% | 31% | 40% | -35.0% | 25 | 0 | 0 | 25 | 0 | 0 | 0 | 0 | 788 |
| bank_d_f4_hold | -9,162 | 52.3% | 24% | 43% | -24.5% | 76 | 0 | 0 | 76 | 0 | 0 | 0 | 0 | -9,162 |
| bank_w_f4_tp4 | -9,964 | 52.2% | 21% | 50% | -20.1% | 89 | 59 | 0 | 30 | 0 | 0 | 0 | 0 | -9,964 |
| bank_w_f4_s8 | -6,758 | 52.8% | 29% | 40% | -39.0% | 40 | 0 | 12 | 28 | 0 | 0 | 0 | 0 | -6,758 |
| bank_w_f4_tp4s8 | -14,334 | 72.9% | 17% | 50% | -19.0% | 129 | 78 | 29 | 22 | 0 | 0 | 0 | 0 | -14,334 |
| bank_d_f8_hold | -9,762 | 54.0% | 19% | 38% | -22.6% | 50 | 0 | 0 | 50 | 0 | 0 | 0 | 0 | -9,762 |
| bank_w_f8_tp4 | -8,961 | 46.0% | 21% | 50% | -20.2% | 73 | 52 | 0 | 21 | 0 | 0 | 0 | 0 | -8,961 |
| bank_w_f8_s8 | -1,974 | 44.9% | 31% | 36% | -39.5% | 30 | 0 | 9 | 21 | 0 | 0 | 0 | 0 | -1,974 |
| bank_w_f8_tp4s8 | -13,855 | 69.9% | 19% | 48% | -21.0% | 116 | 73 | 31 | 12 | 0 | 0 | 0 | 0 | -13,855 |
| bank_w_f4_trail10 | -13,412 | 70.2% | 24% | 45% | -33.9% | 61 | 0 | 0 | 26 | 35 | 0 | 0 | 0 | -13,412 |
| bank_w_f4_lock | -12,722 | 67.1% | 24% | 45% | -33.9% | 62 | 0 | 0 | 25 | 32 | 5 | 0 | 0 | -12,722 |
| bank_w_f4_ratchet | -3,679 | 49.2% | 24% | 45% | -26.0% | 61 | 0 | 0 | 27 | 0 | 34 | 0 | 0 | -3,679 |
| bank_w_f8_trail10 | -11,195 | 61.5% | 26% | 40% | -35.4% | 53 | 0 | 0 | 17 | 36 | 0 | 0 | 0 | -11,195 |
| bank_w_f8_lock | -10,897 | 60.9% | 26% | 40% | -35.3% | 53 | 0 | 0 | 17 | 32 | 4 | 0 | 0 | -10,897 |
| bank_w_f8_ratchet | -5,141 | 42.6% | 29% | 40% | -26.5% | 59 | 0 | 0 | 20 | 0 | 39 | 0 | 0 | -5,141 |
| bank_d_f4_trail10 | -9,155 | 58.3% | 24% | 43% | -24.4% | 85 | 0 | 0 | 65 | 20 | 0 | 0 | 0 | -9,155 |
| bank_d_f4_lock | -10,414 | 60.3% | 24% | 43% | -24.1% | 89 | 0 | 0 | 64 | 19 | 6 | 0 | 0 | -10,414 |
| bank_d_f4_ratchet | -12,504 | 68.0% | 26% | 40% | -37.1% | 117 | 0 | 0 | 57 | 0 | 60 | 0 | 0 | -12,504 |


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
4. **Taking the level while the bar is printing gives the rise back to
   fees.** A round trip is about 0.5% plus impact. A +3% target against a
   −3% stop needs about 58% of the decisive trades to hit the target
   before it breaks even. `bank_d_tp3_s3` prints 381 targets and 381
   stops and finishes at −€19,985. Every tight target on a 24h rise —
   partial, two names, or the BTC filter off — compounds €20k toward zero,
   in-sample and in 2026. A 24h hold with no target and no stop is still
   negative (`bank_d_f4_hold` −€14.4k IS / −€9.2k OOS, `bank_d_f8_hold`
   −€7.5k / −€9.8k).
5. **A 7-day hold until the rise dies is the only line above zero, and it
   is a 2024 windfall with a deep giveback.** No target, no stop, sell the
   next open once the lookback return is no longer positive. Floors 3%,
   4%, 6% and 8% are positive on the full path (+€15k to +€29k) and on a
   fresh 2026 book (+€5.3k, +€2.2k, +€1.2k, +€0.8k). The in-sample gain
   grows as the floor rises (+€11k to +€37k) while the fresh 2026 book
   shrinks, and 2025 loses €27k–€32k at every floor. Drawdown on the full
   path is 76–82%, worst week about −35%, green weeks about 30%. From the
   same 2024-06-01 start the armed residual pack is +€389,590 at 30.6%
   drawdown (2024 +€104k, 2025 +€17k, 2026 +€269k). A +4% target or an 8%
   stop on the 4% and 8% entries is negative in-sample and in 2026. The
   3% floor plus a 5% stop (`bank_w_follow`) is negative too (−€4.5k IS,
   −€1.2k OOS).
6. **A trail or a lock on that same 7-day rise gives the trend back.**
   The live 10% trail on the 4% entry (`bank_w_f4_trail10`) is −€15.3k
   in-sample and −€13.4k on a fresh 2026 book. 70 of 106 in-sample sells
   are the trail. The 8% entry is the same shape. The live lock, arm
   +12% and keep +6%, does not repair it. A ratchet that lifts the floor
   to +0.5% once the prior peak is +2% — one taker round trip — is the
   least bad bank and still loses: `bank_w_f8_ratchet` is −€0.6k
   in-sample and −€5.1k in 2026, and the sells are the lock. The 2024
   rise the plain hold kept (+€49k at the 4% floor) is −€10k once the
   trail is on.

## Verdict

Nothing armed. The book already ranks what is rising, every 4h bar.
Banking that rise loses after Bitvavo taker fees. A fixed target sits
under the fee-adjusted breakeven and a €20k book goes to zero. A 10%
trail, the live +12%/+6% lock, and a ratchet that protects one round
trip once the peak is +2% are negative on the same 7-day entries, in
sample and in 2026. The trail is what cuts the 2024 rise the hold kept.
Holding a 7-day riser until the rise dies stays above zero only as a
2024 profit that 2025 largely gives back, with a fresh 2026 result of
a few thousand euros and a drawdown past 70%. That is a smaller, rougher
book than the armed residual trail over the same dates. Day trading flat
by the next UTC open loses the same fees. A shorter residual clock gives
back the trends that pay the pack. Leaving a name that has closed through
the runner arm on the trail is two holds, and the share of green weeks
stays put. The live pack stays the 7-day residual with the 10% trail.

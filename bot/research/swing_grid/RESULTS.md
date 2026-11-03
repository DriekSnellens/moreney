# Swing grid

Rules are the module docstring in `__init__.py`. They were locked before
this replay. Fills are 15-minute Bitvavo EUR bars. Maker 0.15% at the
limit. A close-out is taker 0.25% plus impact
`0.02 * sqrt(notional / median quote volume)`, capped at 3%.
Each window restarts at €20,000. The in-progress UTC day is dropped.

Last complete session: 2026-11-02.

| cell | window | pnl | max DD | days selected | days filled | maker exits | flattens | maker pnl | flatten pnl | largest day |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| strict | full | -€459 | 66.9% | 1032 | 534 | 105 | 645 | +€109,729 | -€110,188 | +€7,067 |
| strict | before 2026 | +€5,373 | 57.3% | 726 | 383 | 74 | 470 | +€91,283 | -€85,910 | +€7,067 |
| strict | 2026 | -€4,266 | 37.8% | 306 | 151 | 31 | 175 | +€14,738 | -€19,003 | -€2,831 |
| path | full | -€785 | 67.5% | 1032 | 534 | 104 | 645 | +€107,826 | -€108,611 | +€7,067 |
| path | before 2026 | +€4,905 | 57.3% | 726 | 383 | 73 | 470 | +€89,700 | -€84,794 | +€7,067 |
| path | 2026 | -€4,266 | 37.8% | 306 | 151 | 31 | 175 | +€14,738 | -€19,003 | -€2,831 |
| retest | full | -€19,635 | 98.9% | 1032 | 834 | 514 | 558 | +€93,367 | -€113,002 | +€3,619 |
| retest | before 2026 | -€19,046 | 97.1% | 726 | 583 | 352 | 393 | +€91,448 | -€110,494 | +€3,619 |
| retest | 2026 | -€13,857 | 71.7% | 306 | 251 | 162 | 165 | +€36,127 | -€49,984 | -€2,682 |
| bounce5 | full | -€19,468 | 98.8% | 1032 | 949 | 306 | 643 | +€147,624 | -€167,091 | -€3,190 |
| bounce5 | before 2026 | -€17,551 | 93.1% | 726 | 671 | 226 | 445 | +€144,320 | -€161,871 | -€3,190 |
| bounce5 | 2026 | -€16,378 | 86.9% | 306 | 278 | 80 | 198 | +€24,487 | -€40,865 | -€2,092 |
| bounce10 | full | -€19,832 | 99.7% | 1032 | 949 | 98 | 851 | +€79,237 | -€99,070 | -€4,150 |
| bounce10 | before 2026 | -€19,307 | 98.4% | 726 | 671 | 67 | 604 | +€78,501 | -€97,808 | -€4,150 |
| bounce10 | 2026 | -€16,347 | 86.0% | 306 | 278 | 31 | 247 | +€17,927 | -€34,273 | -€1,805 |
| confirm5 | full | -€19,321 | 98.4% | 1032 | 920 | 286 | 634 | +€160,727 | -€180,048 | -€3,412 |
| confirm5 | before 2026 | -€16,857 | 90.9% | 726 | 649 | 210 | 439 | +€156,717 | -€173,575 | -€3,412 |
| confirm5 | 2026 | -€16,340 | 86.2% | 306 | 271 | 76 | 195 | +€23,346 | -€39,686 | -€2,110 |
| stop5 | full | -€19,937 | 99.7% | 1032 | 944 | 233 | 711 | +€35,902 | -€55,839 | +€858 |
| stop5 | before 2026 | -€19,672 | 98.4% | 726 | 668 | 169 | 499 | +€35,504 | -€55,176 | +€858 |
| stop5 | 2026 | -€17,077 | 86.6% | 306 | 276 | 64 | 212 | +€21,153 | -€38,229 | +€944 |
| shallow5 | full | -€19,477 | 98.7% | 1032 | 884 | 281 | 603 | +€125,610 | -€145,088 | -€2,982 |
| shallow5 | before 2026 | -€17,355 | 92.0% | 726 | 615 | 206 | 409 | +€122,200 | -€139,554 | -€2,982 |
| shallow5 | 2026 | -€16,677 | 86.8% | 306 | 269 | 75 | 194 | +€23,562 | -€40,239 | -€2,381 |
| hold3 | full | -€14,593 | 94.7% | 509 | 472 | 278 | 194 | +€391,113 | -€405,705 | -€11,828 |
| hold3 | before 2026 | -€5,789 | 80.4% | 363 | 341 | 207 | 134 | +€370,152 | -€375,941 | -€11,828 |
| hold3 | 2026 | -€12,506 | 75.9% | 146 | 131 | 71 | 60 | +€29,243 | -€41,749 | -€1,765 |
| liquid5 | full | -€19,606 | 98.6% | 1032 | 966 | 166 | 800 | +€67,842 | -€87,448 | -€2,732 |
| liquid5 | before 2026 | -€18,338 | 93.8% | 726 | 680 | 129 | 551 | +€66,624 | -€84,961 | -€2,732 |
| liquid5 | 2026 | -€15,688 | 80.2% | 306 | 286 | 37 | 249 | +€13,935 | -€29,623 | -€2,034 |
| early5 | full | -€14,721 | 87.8% | 1032 | 661 | 251 | 410 | +€251,842 | -€266,562 | -€6,111 |
| early5 | before 2026 | -€863 | 59.1% | 726 | 482 | 189 | 293 | +€222,046 | -€222,909 | -€6,111 |
| early5 | 2026 | -€14,504 | 75.7% | 306 | 179 | 62 | 117 | +€31,080 | -€45,584 | -€2,845 |
| reclaim | full | -€18,284 | 92.8% | 1032 | 741 | 494 | 247 | +€57,379 | -€75,663 | -€2,444 |
| reclaim | before 2026 | -€15,930 | 82.9% | 726 | 532 | 353 | 179 | +€54,573 | -€70,504 | -€2,444 |
| reclaim | 2026 | -€12,102 | 63.4% | 306 | 209 | 141 | 68 | +€13,301 | -€25,402 | -€2,504 |
| prior | full | -€8,264 | 69.9% | 1032 | 346 | 77 | 269 | +€69,359 | -€77,623 | -€3,401 |
| prior | before 2026 | -€6,827 | 69.9% | 726 | 254 | 55 | 199 | +€58,393 | -€65,220 | -€3,401 |
| prior | 2026 | -€2,755 | 29.2% | 306 | 92 | 22 | 70 | +€16,352 | -€19,106 | -€2,304 |
| sma_bounce | full | -€19,190 | 98.3% | 840 | 780 | 246 | 534 | +€111,810 | -€131,001 | -€4,006 |
| sma_bounce | before 2026 | -€17,864 | 95.2% | 593 | 557 | 182 | 375 | +€107,989 | -€125,853 | -€4,006 |
| sma_bounce | 2026 | -€13,627 | 72.2% | 247 | 223 | 64 | 159 | +€32,620 | -€46,247 | -€2,534 |
| sma_reclaim | full | -€16,097 | 88.5% | 840 | 597 | 406 | 191 | +€42,662 | -€58,759 | -€1,920 |
| sma_reclaim | before 2026 | -€14,053 | 79.6% | 593 | 429 | 291 | 138 | +€38,979 | -€53,032 | -€1,920 |
| sma_reclaim | 2026 | -€7,421 | 54.8% | 247 | 168 | 115 | 53 | +€12,038 | -€19,458 | -€1,896 |
| sma_prior | full | -€7,731 | 78.0% | 840 | 273 | 52 | 221 | +€41,765 | -€49,496 | +€2,869 |
| sma_prior | before 2026 | -€10,859 | 78.0% | 593 | 199 | 35 | 164 | +€34,635 | -€45,495 | +€2,869 |
| sma_prior | 2026 | +€5,677 | 13.7% | 247 | 74 | 17 | 57 | +€15,174 | -€9,497 | +€1,615 |
| quiet5 | full | -€19,329 | 98.5% | 1032 | 937 | 304 | 633 | +€146,282 | -€165,610 | -€3,190 |
| quiet5 | before 2026 | -€17,057 | 91.7% | 726 | 662 | 224 | 438 | +€142,144 | -€159,201 | -€3,190 |
| quiet5 | 2026 | -€16,159 | 86.1% | 306 | 275 | 80 | 195 | +€25,659 | -€41,818 | -€2,207 |
| clip | full | -€19,424 | 97.1% | 1032 | 903 | 782 | 121 | +€7,790 | -€27,214 | -€2,297 |
| clip | before 2026 | -€18,642 | 93.3% | 726 | 643 | 562 | 81 | +€7,443 | -€26,085 | -€2,297 |
| clip | 2026 | -€11,666 | 59.0% | 306 | 260 | 220 | 40 | +€5,066 | -€16,732 | -€1,194 |
| double5 | full | -€19,554 | 98.9% | 1032 | 917 | 279 | 638 | +€162,921 | -€182,475 | -€3,359 |
| double5 | before 2026 | -€17,908 | 94.4% | 726 | 643 | 206 | 437 | +€160,251 | -€178,159 | -€3,359 |
| double5 | 2026 | -€16,500 | 84.9% | 306 | 274 | 73 | 201 | +€22,875 | -€39,374 | -€2,153 |
| scale | full | -€19,556 | 98.6% | 1032 | 949 | 886 | 643 | +€105,018 | -€124,574 | -€2,691 |
| scale | before 2026 | -€17,640 | 91.2% | 726 | 671 | 656 | 445 | +€102,378 | -€120,018 | -€2,691 |
| scale | 2026 | -€16,743 | 87.5% | 306 | 278 | 230 | 198 | +€20,820 | -€37,563 | -€2,257 |
| shallow_rung | full | +€5,312 | 44.8% | 1032 | 533 | 99 | 454 | +€91,023 | -€85,712 | -€2,549 |
| shallow_rung | before 2026 | +€8,124 | 44.8% | 726 | 382 | 70 | 328 | +€71,784 | -€63,659 | -€2,549 |
| shallow_rung | 2026 | -€1,674 | 29.4% | 306 | 151 | 29 | 126 | +€13,846 | -€15,520 | -€1,640 |
| scratch | full | -€19,960 | 99.9% | 1032 | 949 | 656 | 293 | +€50,962 | -€70,922 | -€2,508 |
| scratch | before 2026 | -€19,715 | 98.9% | 726 | 671 | 467 | 204 | +€50,750 | -€70,465 | -€2,508 |
| scratch | 2026 | -€17,494 | 88.0% | 306 | 278 | 189 | 89 | +€13,942 | -€31,436 | -€2,224 |
| vwap | full | -€19,140 | 95.9% | 1032 | 959 | 764 | 195 | +€31,952 | -€51,091 | -€2,214 |
| vwap | before 2026 | -€18,183 | 91.0% | 726 | 681 | 551 | 130 | +€30,095 | -€48,278 | -€2,214 |
| vwap | 2026 | -€11,365 | 62.0% | 306 | 278 | 213 | 65 | +€19,573 | -€30,938 | -€1,973 |
| carry | full | -€7,114 | 49.2% | 3 | 3 | 2 | 1 | +€4,062 | -€11,177 | +€3,379 |
| carry | before 2026 | -€6,992 | 47.0% | 3 | 3 | 2 | 1 | +€4,062 | -€11,055 | +€3,379 |
| carry | 2026 | +€4,649 | 59.9% | 19 | 15 | 10 | 1 | +€16,099 | -€11,450 | +€4,193 |
| mid_exit | full | -€830 | 44.4% | 1032 | 523 | 206 | 317 | +€73,989 | -€74,818 | -€1,833 |
| mid_exit | before 2026 | -€2,265 | 43.2% | 726 | 379 | 146 | 233 | +€57,623 | -€59,888 | -€1,833 |
| mid_exit | 2026 | +€1,529 | 11.8% | 306 | 144 | 60 | 84 | +€18,412 | -€16,882 | -€1,452 |
| second_day | full | -€19,822 | 99.3% | 847 | 759 | 186 | 573 | +€45,027 | -€64,849 | -€2,443 |
| second_day | before 2026 | -€19,407 | 97.4% | 590 | 530 | 136 | 394 | +€44,379 | -€63,786 | -€2,443 |
| second_day | 2026 | -€15,433 | 80.9% | 257 | 229 | 50 | 179 | +€18,715 | -€34,148 | -€2,163 |
| hold | full | -€19,980 | 100.0% | 1032 | 1032 | 0 | 1032 | €0 | -€19,980 | +€7,814 |
| hold | before 2026 | -€19,911 | 99.7% | 726 | 726 | 0 | 726 | €0 | -€19,911 | +€7,814 |
| hold | 2026 | -€17,677 | 91.5% | 306 | 306 | 0 | 306 | €0 | -€17,677 | -€1,735 |
| oracle | full | +€41,963,155 | 0.0% | 71 | 71 | 71 | 0 | +€41,963,155 | €0 | +€7,648,837 |
| oracle | before 2026 | +€41,963,155 | 0.0% | 71 | 71 | 71 | 0 | +€41,963,155 | €0 | +€7,648,837 |
| oracle | 2026 | +€15,038,801 | 0.0% | 105 | 105 | 105 | 0 | +€15,038,801 | €0 | +€1,386,298 |

## Reading

The headline is `strict`: limits from the prior five sessions, and no
round trip inside a single 15-minute bar. On a €20,000 book restarted
for each window it finishes -€459 on the full path,
+€5,373 before 2026, and -€4,266
in 2026.

Completed maker swings on the full path contribute +€109,729.
Inventory sold at the close contributes -€110,188.
The largest single day is +€7,067.
`path` allows a same-bar round trip when the bar closes up and
finishes -€785.
`retest` buys a retest of extremes that have already printed and
finishes -€19,635.
`bounce5` bids 1% above the low already printed today and sells
5% above that fill. Full path -€19,468, maker
+€147,624, close-outs -€167,091.
`bounce10` uses the same bid and sells 10% above the fill.
Full path -€19,832, maker +€79,237,
close-outs -€99,070.
`confirm5` waits until a later bar holds the low. It finishes
-€19,321 on the full path,
-€16,857 before 2026, and
-€16,340 in 2026.
`stop5` sells a break 2% through that low. It finishes
-€19,937,
-€19,672 before 2026, and
-€17,077 in 2026.
`shallow5` skips a low more than 4% under the open. It finishes
-€19,477,
-€17,355 before 2026, and
-€16,677 in 2026.
`hold3` keeps the fill for up to three sessions. It finishes
-€14,593,
-€5,789 before 2026, and
-€12,506 in 2026.
`liquid5` takes the busiest name that still clears the chop
floor. -€19,606 full,
-€18,338 before 2026,
-€15,688 in 2026.
`early5` pulls the bid after four hours.
-€14,721 full,
-€863 before 2026,
-€14,504 in 2026.
`reclaim` sells the fill back at the session open.
-€18,284 full,
-€15,930 before 2026,
-€12,102 in 2026.
`prior` buys halfway down yesterday's range and sells the
prior close.
-€8,264 full,
-€6,827 before 2026,
-€2,755 in 2026.
`sma_bounce` bounce5 only above the 20-session average. -€19,190 full, -€17,864 before 2026, -€13,627 in 2026.
`sma_reclaim` reclaim only above that average. -€16,097 full, -€14,053 before 2026, -€7,421 in 2026.
`sma_prior` prior-close sell only above that average. -€7,731 full, -€10,859 before 2026, +€5,677 in 2026.
`quiet5` no bid once today already exceeds a normal range. -€19,329 full, -€17,057 before 2026, -€16,159 in 2026.
`clip` 0.5% under the open on the busiest name, sold 0.5% higher. -€19,424 full, -€18,642 before 2026, -€11,666 in 2026.
`double5` buys the second test of the same low, sold 5% higher. -€19,554 full, -€17,908 before 2026, -€16,500 in 2026.
`scale` sells half at 2% and the rest at 5%. -€19,556 full, -€17,640 before 2026, -€16,743 in 2026.
`shallow_rung` rests only the 0.35 rung, still at half the book. +€5,312 full, +€8,124 before 2026, -€1,674 in 2026.
`scratch` replaces the 5% sell with the fill after 64 bars. -€19,960 full, -€19,715 before 2026, -€17,494 in 2026.
`vwap` buys 1% under the completed-bar VWAP and sells it. -€19,140 full, -€18,183 before 2026, -€11,365 in 2026.
`carry` keeps the strict grid's paired sell working past the close. -€7,114 full, -€6,992 before 2026, +€4,649 in 2026.
`mid_exit` buys the 0.35 rung and sells the prior close. -€830 full, -€2,265 before 2026, +€1,529 in 2026.
`second_day` bounce5 only after a lower low than the day before. -€19,822 full, -€19,407 before 2026, -€15,433 in 2026.
No cell clears the locked bar for a convincing profit.
`hold` buys that same name at the open and sells the close and
finishes -€19,980.
The oracle ceiling buys the day's low and sells the day's high and
finishes +€41,963,155.
That ceiling uses the session extremes and is not a strategy.

Nothing is armed. The first cells were locked before their replay.
The 1% cushion and the 5% and 10% sells were locked before the
second replay. None of those numbers were moved after the result.

Strict full-path selections: FET 273, SUI 179, NEAR 112, UNI 103, ARB 74, DOGE 58, OP 51, ADA 45.

## Coverage

| base | sessions | first | last |
| --- | ---: | --- | --- |
| ETH | 1038 | 2024-01-01 | 2026-11-03 |
| SOL | 1038 | 2024-01-01 | 2026-11-03 |
| XRP | 1038 | 2024-01-01 | 2026-11-03 |
| ADA | 1038 | 2024-01-01 | 2026-11-03 |
| DOGE | 1038 | 2024-01-01 | 2026-11-03 |
| LINK | 1038 | 2024-01-01 | 2026-11-03 |
| DOT | 1038 | 2024-01-01 | 2026-11-03 |
| AVAX | 1038 | 2024-01-01 | 2026-11-03 |
| LTC | 1038 | 2024-01-01 | 2026-11-03 |
| NEAR | 1038 | 2024-01-01 | 2026-11-03 |
| ATOM | 1038 | 2024-01-01 | 2026-11-03 |
| OP | 1038 | 2024-01-01 | 2026-11-03 |
| ARB | 1038 | 2024-01-01 | 2026-11-03 |
| SUI | 1038 | 2024-01-01 | 2026-11-03 |
| FET | 1034 | 2024-01-01 | 2026-11-03 |
| UNI | 1038 | 2024-01-01 | 2026-11-03 |

Reproduce: `python -m bot.research.swing_grid`

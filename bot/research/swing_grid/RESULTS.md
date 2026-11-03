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

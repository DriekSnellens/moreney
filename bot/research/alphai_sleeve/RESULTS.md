# AlphaI sleeve

Rules are the module docstring in `__init__.py`. They were locked
before this replay. Sleeve €10,000. At most four names in `four`
and `chop`, equal weight, taker both sides. The news count is the
seven publication days before the session. Insider rows are excluded.
Archive 2026-08-05 → 2026-11-02. Overlap 2026-08-12 → 2026-11-02.
Tape for the controls 2024-01-01 → 2026-11-02.
There is no news path before 2026.

## News overlap

| cell | pnl | max DD | name-days | buys | sells | largest day | largest name | one-window |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| four | -€182 | 30.4% | 313 | 174 | 184 | -€1,217 | ETH +€1,255 | — |
| all | +€1,587 | 28.5% | 600 | 325 | 304 | -€1,437 | ETH +€1,105 | — |
| chop | +€3,417 | 33.6% | 370 | 200 | 197 | +€1,987 | NEAR +€2,490 | — |
| book | +€3,617 | 25.1% | 1265 | 684 | 553 | +€1,457 | NEAR +€896 | pass |

An overlap pass is not a two-period result.

## Controls on the full tape

| cell | pnl | max DD | name-days | buys | sells | largest day | largest name | one-window |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| chop full | -€8,015 | 94.4% | 4144 | 2423 | 2355 | +€3,732 | XRP +€4,037 | — |
| chop before 2026 | -€7,722 | 90.1% | 2929 | 1739 | 1706 | +€3,732 | XRP +€3,999 | — |
| chop 2026 | -€1,823 | 54.4% | 1244 | 726 | 694 | +€1,204 | NEAR +€2,328 | — |
| book full | -€4,561 | 84.1% | 15686 | 7796 | 6579 | -€3,411 | XRP +€1,338 | — |
| book before 2026 | -€3,435 | 73.4% | 11065 | 5710 | 4809 | -€3,411 | XRP +€1,463 | — |
| book 2026 | -€1,779 | 52.7% | 4621 | 2221 | 1905 | -€1,219 | NEAR +€662 | — |

No control clears both periods.
Nothing is armed.

## Live rollup

Live sentiment-summary fetched 2026-11-03 14:58 Europe/Amsterdam.
The product's window includes today's partial bucket.
These counts are the published rollup, not the replay.

| name | bullish | bearish | neutral |
| --- | ---: | ---: | ---: |
| ETH | 35 | 190 | 43 |
| SOL | 47 | 115 | 30 |
| XRP | 65 | 103 | 74 |
| ADA | 0 | 2 | 2 |
| DOGE | 7 | 10 | 5 |
| LINK | 11 | 2 | 4 |
| DOT | 0 | 0 | 0 |
| AVAX | 1 | 1 | 1 |
| LTC | 0 | 1 | 0 |
| NEAR | 11 | 10 | 9 |
| ATOM | 0 | 0 | 1 |
| OP | 0 | 0 | 0 |
| ARB | 4 | 1 | 2 |
| SUI | 2 | 0 | 1 |
| FET | 0 | 0 | 0 |
| UNI | 3 | 6 | 4 |

The published rollup's top four, today included: LINK, ARB, SUI, NEAR.

Reproduce: `python -m bot.research.alphai_sleeve`

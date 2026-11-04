# AlphaI sentiment as a strategy chooser

One majority vote per coin per UTC day. The entry is the next midnight
open and the exit is the open 24 hours later, with the Bitvavo taker
on both sides. The chooser then picks one item from a fixed menu:
follow the label, fade the label, always long, always short, or flat.

The lookback is 14 calendar days and only votes that have
already exited. The market chooser needs
20 of those votes and a strictly positive mean. The per-coin
chooser needs 5 votes on that coin. An exact tie keeps the
earlier menu item. Flat names earn zero inside that day's average.
The book starts at €20,000 and reinvests the day's average.
Fixed rules are scored on the same days. These widths were set before
the run. Nothing is armed.

Scored daily votes 505. Labels with no 15m file are omitted
from the vote build (2470 directional rows had no file).
Vote midnights run 2026-08-06 to 2026-11-02. Deploy days 81.

| book | days | end € | return | max DD | mean day |
|---|---:|---:|---:|---:|---:|
| selector | 81 | 16,428.80 | -17.9% | 33.4% | -0.216% |
| per_coin | 81 | 15,337.28 | -23.3% | 25.4% | -0.317% |
| with_label | 81 | 18,440.22 | -7.8% | 26.3% | -0.063% |
| flip | 81 | 10,715.63 | -46.4% | 50.9% | -0.732% |
| long | 81 | 17,223.44 | -13.9% | 35.3% | -0.121% |
| short | 81 | 11,009.85 | -45.0% | 52.9% | -0.673% |

### What the chooser deployed

Market chooser, days: flat 33, with_label 11, long 23, short 14.
Per coin, votes: flat 212, with_label 98, flip 55, long 61, short 55.

The selector and every fixed rule finish at or below the start on the days the chooser is allowed to trade.

The live residual book is not in this menu. The headline file does not
cover the two-year residual window, so a selector fit here cannot
replace that book.


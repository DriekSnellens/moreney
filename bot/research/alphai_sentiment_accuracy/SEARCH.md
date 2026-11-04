# Search a strategy for each news sentiment

One majority vote per coin per UTC day. Bullish and bearish are searched
separately. The menu is long or short, held 24 hours or 7 days, and the
7-day holds again with a 6% stop. Flat is the choice when no strategy
has 20 completed trades and a positive mean net.

The cut is halfway between the first and last vote midnight. Training
uses only trades that have exited before the cut. Entries on or after
the cut trade that frozen mapping. The walk-forward refits at each
entry from closed trades of the same sentiment. Fees are the Bitvavo
taker on both sides. The euro book is four slots starting at €20,000,
resized with equity, skipping a coin that is already open.
The menu and the cut were fixed before the run. Nothing is armed.

Daily votes 505. Directional rows with no 15m file: 2470.
Vote span 2026-08-06 to 2026-11-02, cut 2026-09-19.
Frozen map: bullish `long 7d`, bearish `short 7d`.

### Training means, trades that exited before the cut

| sentiment | strategy | n | mean net | chosen |
|---|---|---:|---:|---|
| bullish | long 24h | 159 | +0.628% |  |
| bullish | short 24h | 159 | -1.408% |  |
| bullish | long 7d | 75 | +0.693% | yes |
| bullish | short 7d | 75 | -0.833% |  |
| bullish | long 7d stop 6% | 75 | -0.275% |  |
| bullish | short 7d stop 6% | 75 | -1.526% |  |
| bearish | long 24h | 54 | -0.236% |  |
| bearish | short 24h | 54 | -0.666% |  |
| bearish | long 7d | 28 | -0.620% |  |
| bearish | short 7d | 28 | +0.088% | yes |
| bearish | long 7d stop 6% | 28 | -0.658% |  |
| bearish | short 7d stop 6% | 28 | -0.077% |  |

### Deployed on trades the search had not seen

Mean net is every deployed signal. The euro columns are the four-slot
book, which skips a coin already open and resizes the next slot.

| book | signals | slotted | mean net | end € | return | max DD |
|---|---:|---:|---:|---:|---:|---:|
| frozen map, second half | 122 | 19 | -0.044% | 20,451.64 | +2.3% | 3.3% |
| walk-forward, full span | 223 | 70 | -1.145% | 19,430.57 | -2.8% | 13.0% |

### Second half by sentiment

| sentiment | signals | mean net |
|---|---:|---:|
| bullish | 86 | -1.125% |
| bearish | 36 | +2.539% |

### Walk-forward choices

| sentiment | strategy | votes |
|---|---|---:|
| bearish | flat | 90 |
| bearish | long 24h | 2 |
| bearish | long 7d | 13 |
| bearish | short 7d | 28 |
| bearish | short 7d stop 6% | 1 |
| bullish | flat | 192 |
| bullish | long 24h | 91 |
| bullish | long 7d | 84 |
| bullish | long 7d stop 6% | 4 |

The frozen mapping averages -0.044% per signal on the second half. Its four-slot book ends at €20,451.64. The walk-forward book ends at €19,430.57. The search is implemented. It is not wired into the live bot.


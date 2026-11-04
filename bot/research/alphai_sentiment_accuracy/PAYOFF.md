# AlphaI sentiment, let the profit run and cap the loss

Same labels and the same next 15-minute Bitvavo open as the hit-rate
study. No profit target. A long stops when the low touches
entry × (1 − stop); if the open is already through, the fill is the
open. A short is the mirror. The stop is checked before the peak
updates. A trail, where one is on, is known at the close and fills
on the next open. Otherwise the exit is the open at the horizon.
A gap in the 15-minute grid drops the call.

Fee is the Bitvavo taker, 0.25% on the entry and again
on the exit. Gross is the same fill with a zero fee. The euro column
is the average net on one €5,000 signal. Adding those euros across
overlapping headlines is not an account result: one coin with many
headlines would dominate. The slot book below is the account.

Bitvavo spot cannot short. The short rows use the same taker so the
payoff shape can be compared. They are not a spot order. Stops of
3%, 6% and 10%, the 24h and 7d holds, and the 10% trail were fixed
before the run. Nothing is armed.

Directional labels 4786. Neutral labels 1267.
Labels with no Bitvavo 15m file: 2470.

### Sentiment side, every directional label

Bullish labels are long. Bearish labels are short.

| cell | n | stopped | trail | net>0 | avg gross | avg net | median | € / €5,000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hold 24h | 1871 | 0.0% | 0.0% | 41.4% | +0.075% | -0.424% | -0.673% | -21.22 |
| hold 7d | 882 | 0.0% | 0.0% | 49.2% | +0.856% | +0.353% | -0.089% | +17.64 |
| stop 3% to 7d | 882 | 67.5% | 0.0% | 29.4% | -0.067% | -0.565% | -3.484% | -28.25 |
| stop 6% to 7d | 882 | 39.0% | 0.0% | 44.9% | +0.226% | -0.274% | -1.054% | -13.69 |
| stop 10% to 7d | 882 | 17.1% | 0.0% | 47.4% | +0.362% | -0.139% | -0.369% | -6.94 |
| stop 6% trail 10% to 7d | 882 | 34.0% | 17.0% | 44.9% | +0.102% | -0.398% | -1.063% | -19.88 |

### Same timestamps, other sides

Flip sells a bullish label and buys a bearish one. Always-long and
always-short ignore the label and keep the entry time and the exit
rule. When always-short matches the bearish labels, the result is
the tape, not the headline.

| cell | n | stopped | trail | net>0 | avg gross | avg net | median | € / €5,000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hold 7d, flipped | 882 | 0.0% | 0.0% | 44.8% | -0.363% | -0.860% | -0.907% | -42.98 |
| hold 7d, always long | 882 | 0.0% | 0.0% | 36.5% | -0.450% | -0.947% | -2.167% | -47.34 |
| hold 7d, always short | 882 | 0.0% | 0.0% | 57.5% | +0.944% | +0.440% | +1.198% | +22.01 |
| stop 6% to 7d, flipped | 882 | 48.5% | 0.0% | 37.9% | -0.858% | -1.352% | -3.788% | -67.62 |
| stop 6% to 7d, always long | 882 | 48.1% | 0.0% | 32.4% | -1.088% | -1.582% | -3.893% | -79.08 |
| stop 6% to 7d, always short | 882 | 39.5% | 0.0% | 50.3% | +0.456% | -0.045% | +0.033% | -2.24 |
| stop 6% trail 10% to 7d, flipped | 882 | 42.3% | 13.0% | 37.4% | -0.847% | -1.341% | -2.953% | -67.06 |
| stop 6% trail 10% to 7d, always long | 882 | 42.9% | 18.4% | 32.2% | -1.188% | -1.680% | -3.194% | -84.02 |
| stop 6% trail 10% to 7d, always short | 882 | 33.4% | 11.7% | 50.1% | +0.443% | -0.058% | +0.005% | -2.92 |

### Bullish and bearish labels, separately

Same sentiment-side fills, split by the label. Bear avg is short
only where the label was bearish.

| cell | bull n | bull avg net | bear n | bear avg net |
|---|---:|---:|---:|---:|
| hold 24h | 1255 | -0.382% | 616 | -0.512% |
| hold 7d | 597 | -0.274% | 285 | +1.667% |
| stop 3% to 7d | 597 | -1.161% | 285 | +0.683% |
| stop 6% to 7d | 597 | -0.935% | 285 | +1.111% |
| stop 10% to 7d | 597 | -0.838% | 285 | +1.326% |
| stop 6% trail 10% to 7d | 597 | -1.092% | 285 | +1.057% |

### Bullish labels only

This is the spot book: long only, and only where the label was bullish.

| cell | n | stopped | trail | net>0 | avg gross | avg net | median | € / €5,000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hold 24h | 1255 | 0.0% | 0.0% | 40.9% | +0.118% | -0.382% | -0.706% | -19.08 |
| hold 7d | 597 | 0.0% | 0.0% | 40.5% | +0.225% | -0.274% | -1.339% | -13.72 |
| stop 3% to 7d | 597 | 75.9% | 0.0% | 20.9% | -0.665% | -1.161% | -3.484% | -58.05 |
| stop 6% to 7d | 597 | 45.2% | 0.0% | 35.7% | -0.438% | -0.935% | -2.830% | -46.75 |
| stop 10% to 7d | 597 | 20.4% | 0.0% | 38.7% | -0.341% | -0.838% | -1.734% | -41.89 |
| stop 6% trail 10% to 7d | 597 | 38.9% | 22.6% | 35.8% | -0.596% | -1.092% | -2.513% | -54.60 |

### One majority vote per coin per UTC day

Ties dropped. Entry is the next UTC midnight. One trade per coin
per day, still overlapping across coins. Bullish-only is the spot cut.

| cell | n | stopped | trail | net>0 | avg gross | avg net | median | € / €5,000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hold 7d, both sides | 225 | 0.0% | 0.0% | 45.8% | +0.721% | +0.218% | -0.763% | +10.92 |
| hold 7d, bullish only | 161 | 0.0% | 0.0% | 35.4% | +0.222% | -0.278% | -2.247% | -13.90 |
| stop 6% to 7d, both sides | 225 | 46.2% | 0.0% | 39.1% | +0.156% | -0.344% | -2.377% | -17.18 |
| stop 6% to 7d, bullish only | 161 | 53.4% | 0.0% | 29.2% | -0.442% | -0.939% | -6.469% | -46.95 |

### €20,000 book, 4 slots of €5,000

Signals in time order. A slot stays on that coin until the exit.
A new headline on an open coin is skipped, and so is a signal when
all four slots are full. Each taken trade is €5,000. Nothing is
resized after a win or a loss. Ties at the same entry time follow
the base name.

| book | taken | signals | pnl € | end € |
|---|---:|---:|---:|---:|
| hold 7d, both sides | 42 | 882 | +3014.52 | 23,014.52 |
| hold 7d, bullish only | 39 | 597 | +2164.50 | 22,164.50 |
| hold 7d, one vote a day, both sides | 42 | 225 | +5611.99 | 25,611.99 |
| stop 6% to 7d, both sides | 52 | 882 | -1564.24 | 18,435.76 |
| stop 6% to 7d, bullish only | 47 | 597 | -2373.98 | 17,626.02 |
| stop 6% to 7d, one vote a day, both sides | 49 | 225 | +1046.96 | 21,046.96 |

### €20,000 book, 4 slots, profits reinvested

Same entries and the same skip rules. Equity starts at the book size.
An open slot counts at its cost. The next slot is a quarter of equity,
and never more than the cash that is free. A closed slot returns
cost × (1 + net) before that size is set. Drawdown is the deepest drop
of this equity from its peak, measured when a slot closes. A slot that
is still open stays at cost.

| book | taken | pnl € | end € | return | max DD |
|---|---:|---:|---:|---:|---:|
| hold 7d, both sides | 42 | +2813.72 | 22,813.72 | +14.1% | 10.9% |
| hold 7d, bullish only | 39 | +1784.21 | 21,784.21 | +8.9% | 13.8% |
| hold 7d, one vote a day, both sides | 42 | +5736.08 | 25,736.08 | +28.7% | 4.5% |
| stop 6% to 7d, both sides | 52 | -1783.51 | 18,216.49 | -8.9% | 23.0% |
| stop 6% to 7d, bullish only | 47 | -2667.59 | 17,332.41 | -13.3% | 27.3% |
| stop 6% to 7d, one vote a day, both sides | 49 | +466.01 | 20,466.01 | +2.3% | 23.4% |

Reinvesting does not flip the sign of any of these books.

### Reading

Sentiment side: only `hold 7d` has a positive average net. The other locked widths do not, so that cell is not a book to trade. `hold 7d` averages +0.353% with a median of -0.089%.
Bullish labels only: every locked cell has a negative average net after the 0.25% taker on both sides.

No further stop was tried after this run.


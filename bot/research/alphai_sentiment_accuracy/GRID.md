# Sentiment grid, every combination of the existing levers

192 trade specifications × 24 sentiment states = 4608 combinations. The trade specs are entry, horizon,
side, stop and trail. The states are the label, relevance 8, the trailing
24-hour breadth (quiet under 5 headlines, otherwise a bullish share of
one half), and BTC+ETH versus every other coin. These sets were fixed
before the run. No new percentage was added.

A state leaves cash only when a spec has 20 trades that already exited
and a positive mean. The halfway cut uses the next-open entry. Training
cannot see a trade that exits after the cut. The second half trades the
frozen map. The weekly refit does the same with only closed trades.
Fees are the Bitvavo taker on both sides. The euro book is four slots
from €20,000, resized with equity. Nothing is armed.

Headlines with a next open: 2298. Span 2026-08-05 to 2026-11-02, cut 2026-09-18.
States that chose a spec: 7 of 24.
Cells with 20 training trades: 1152. Of those, positive mean: 231.
Train-positive cells that also have 20 test trades: 231. Share still positive on the test: 7.8%. Median test mean: -1.084%.

Luckiest cell on the second half, looked up after the fact and not used:
among cells with 20 train and 20 test trades: `bearish, rel<8, breadth bull, other` / `short 7d no stop no trail next midnight` +3.398% (test n=69, train n=51).
among cells with 20 test trades: `bullish, rel<8, breadth bear, other` / `short 7d no stop no trail plus 1h` +7.808% (test n=44, train n=7).

### Chosen on the first half, scored on the second

| state | chosen | train n | train mean | test n | test mean |
|---|---|---:|---:|---:|---:|
| bullish, rel<8, quiet, other | flat | 0 | — | 0 | — |
| bullish, rel<8, quiet, major | flat | 0 | — | 0 | — |
| bullish, rel<8, breadth bull, other | long 7d no stop no trail next midnight | 175 | +2.026% | 201 | -1.359% |
| bullish, rel<8, breadth bull, major | long 7d no stop no trail next midnight | 64 | +1.244% | 52 | -2.289% |
| bullish, rel<8, breadth bear, other | long 24h stop 3% no trail next midnight | 25 | +1.134% | 90 | -0.884% |
| bullish, rel<8, breadth bear, major | flat | 0 | — | 0 | — |
| bullish, rel>=8, quiet, other | flat | 0 | — | 0 | — |
| bullish, rel>=8, quiet, major | flat | 0 | — | 0 | — |
| bullish, rel>=8, breadth bull, other | long 24h no stop no trail next open | 20 | +1.724% | 62 | -1.224% |
| bullish, rel>=8, breadth bull, major | flat | 0 | — | 0 | — |
| bullish, rel>=8, breadth bear, other | flat | 0 | — | 0 | — |
| bullish, rel>=8, breadth bear, major | flat | 0 | — | 0 | — |
| bearish, rel<8, quiet, other | flat | 0 | — | 0 | — |
| bearish, rel<8, quiet, major | flat | 0 | — | 0 | — |
| bearish, rel<8, breadth bull, other | short 7d no stop no trail plus 1h | 52 | +0.408% | 70 | +2.610% |
| bearish, rel<8, breadth bull, major | long 24h no stop no trail next open | 25 | +0.166% | 60 | -0.950% |
| bearish, rel<8, breadth bear, other | long 24h no stop no trail next open | 40 | +0.715% | 88 | -1.296% |
| bearish, rel<8, breadth bear, major | flat | 0 | — | 0 | — |
| bearish, rel>=8, quiet, other | flat | 0 | — | 0 | — |
| bearish, rel>=8, quiet, major | flat | 0 | — | 0 | — |
| bearish, rel>=8, breadth bull, other | flat | 0 | — | 0 | — |
| bearish, rel>=8, breadth bull, major | flat | 0 | — | 0 | — |
| bearish, rel>=8, breadth bear, other | flat | 0 | — | 0 | — |
| bearish, rel>=8, breadth bear, major | flat | 0 | — | 0 | — |

### Books

Mean net is every deployed signal. Flat states are not signals.
The euro columns are the four-slot book.

| book | signals | slotted | mean net | end € | return | max DD |
|---|---:|---:|---:|---:|---:|---:|
| frozen map, second half | 623 | 62 | -0.860% | 17,366.19 | -13.2% | 18.5% |
| weekly refit, second half | 614 | 63 | -0.822% | 15,078.59 | -24.6% | 24.6% |
| weekly refit, full span | 790 | 82 | -1.184% | 15,093.75 | -24.5% | 24.5% |

The full grid is in `GRID_CELLS.csv`.


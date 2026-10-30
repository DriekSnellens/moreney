# Wet replay of the high-rise states

Question: the largest 7-day highs follow a name that is already
running. Does a one-slot book, filled like Bitvavo, keep any of
that high?

One name at a time, €20,000 compounded. Signal on the completed
close, buy the next open, sell with the Bitvavo taker (0.25% a
side) plus impact `0.02 × sqrt(notional / 20-day average quote
volume)`, capped at 3%. A name must already average €20,000 of
quote volume a day, so a one-day print on a dead tape is not
a fill.

Entries, one per day when the book is flat:

- `cont` — 3-day return at least +15%, 10-day return at least 25
  points ahead of BTC, close above the 20-day average and that
  average above the 50-day. The strongest 3-day return wins.
- `leader` — the strongest positive 3-day return that day.
- `hot` — the strongest name already up at least 40% in 3 days.

Exits. `hold7` sells the next open after 7 bars in the trade.
`trail10` sells inside the bar when the low trades through 10%
under the prior peak; a gap through that level fills at the open.
The peak does not include the bar being judged. `trail10_h7` takes
whichever comes first.

Tape through 2026-10-29. `Fresh 2026` restarts at €20,000 on 1 January
2026. The full path compounds from the first bar of the window.

Reproduce: `.venv/bin/python -m bot.research.rise_lab.sim`

## Full path

| book | PnL | DD | green W | trades | win | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| cont_hold7 | -19,999 | 100.0% | 22.6% | 95 | 23.2% | -18,158 | -1,838 | -3 |
| cont_trail10 | -19,991 | 100.0% | 28.8% | 380 | 28.7% | -15,525 | -4,448 | -18 |
| cont_trail10_h7 | -19,991 | 100.0% | 28.8% | 380 | 28.7% | -15,525 | -4,448 | -18 |
| leader_hold7 | -19,999 | 100.0% | 28.8% | 125 | 30.4% | -17,812 | -2,146 | -41 |
| leader_trail10 | -19,999 | 100.0% | 30.1% | 323 | 25.1% | -13,838 | -6,133 | -29 |
| leader_trail10_h7 | -19,999 | 100.0% | 11.0% | 251 | 21.9% | -19,947 | -52 | 0 |
| hot_hold7 | -19,999 | 100.0% | 17.8% | 66 | 22.7% | -11,974 | -8,025 | 0 |
| hot_trail10 | -19,995 | 100.0% | 30.1% | 422 | 27.0% | -15,471 | -4,480 | -43 |
| hot_trail10_h7 | -19,995 | 100.0% | 30.1% | 422 | 27.0% | -15,471 | -4,478 | -45 |

## Fresh 2026

| book | PnL | DD | green W | trades | win | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| cont_hold7 | -18,553 | 97.0% | 27.3% | 40 | 30.0% | 0 | 0 | -18,553 |
| cont_trail10 | -17,284 | 87.6% | 40.9% | 169 | 27.8% | 0 | 0 | -17,284 |
| cont_trail10_h7 | -17,284 | 87.6% | 40.9% | 169 | 27.8% | 0 | 0 | -17,284 |
| leader_hold7 | -19,981 | 99.9% | 27.3% | 42 | 16.7% | 0 | 0 | -19,981 |
| leader_trail10 | -19,832 | 99.8% | 31.8% | 209 | 22.0% | 0 | 0 | -19,832 |
| leader_trail10_h7 | -19,832 | 99.8% | 31.8% | 209 | 22.0% | 0 | 0 | -19,832 |
| hot_hold7 | -19,974 | 100.0% | 27.3% | 40 | 22.5% | 0 | 0 | -19,974 |
| hot_trail10 | -19,192 | 98.7% | 34.1% | 186 | 22.0% | 0 | 0 | -19,192 |
| hot_trail10_h7 | -19,192 | 98.7% | 34.1% | 186 | 22.0% | 0 | 0 | -19,192 |

## Reading

Every book loses. On the full path the loss is front-loaded: 2024
takes almost the whole €20,000, and the 2026 column is dust because
the book can no longer buy. The fresh 2026 book is the clean read.

1. Least bad in 2026 is `cont_trail10`: €-17,284, drawdown 87.6%, 169 trades, win rate 27.8%, average trade €-102. Worst is `leader_hold7` at €-19,981.
2. On continuation, the 7-day hold loses €-454 a trade (40 trades, all closed on the clock). The 10% trail loses €-102 a trade (169 trades, 169 closed on the trail). The 7-day clock never fired first, so `cont_trail10_h7` is the same book. The trail cuts the loss per trade and multiplies the number of round trips. The book still ends lower.
3. A green week does not save it. The least-bad 2026 book is green about 40.9% of weeks and still gives back most of the stake. The average trade is negative, so the losers outweigh the winners.

## Verdict

Nothing armed. The high in the horse race is a wick. Bought at the
next open, with the taker and impact, a 10% trail and a 7-day hold
both lose the one-slot book, on the full path and on a fresh 2026
book. The same order as the other labs: the wick is real, and the
fill does not keep it.


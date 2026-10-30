# Allocator footprint

Question: the people who put in the most capital set the price, and
they want return. Does their footprint — euros in names that are
already working, and a larger average print — have a pattern the
next ten sessions keep, after a Bitvavo fill?

This does not re-run the crowd-bot presets, the taker-ratio gates,
or the 3-day high-rise chase. Those are already measured. The new
axes are where the euros go, and how large the average print is.

Tape: Bitvavo EUR daily bars, 427 markets, through 2026-10-29.
Liquid means the prior 20 sessions average at least €20,000
of quote volume, so the day being judged cannot qualify the name.
249,110 liquid alt days. BTC is the regime, not a candidate.
Forward return buys the next open and sells the close 10
sessions later. `euros` weights that return by the signal day's
quote volume. Periods split at 2026-01-01.

Screen, fixed before the run. A volume or ticket pair passes only
when both periods have at least 30 events, the heavy side's median and its euro-weighted
return have the predicted sign, and the heavy side beats the light
side by at least 1% on the median (and is at least as good
on the euros). Predicted sign is positive when the footprint is
buying, negative when it is selling. Overlapping ten-session
windows are not independent; the one-slot book below is the costed
check. Nothing in that screen is refit after the table.

Reproduce: `.venv/bin/python -m bot.research.allocator_lab`

## 1. Where the euros print

Share of quote-volume euros on liquid alt days. A winner is already
up at least 10% over 20 sessions; a loser is already
down at least that much. The day is up or down against the prior close.

| bucket | before 2026 | 2026 |
|---|---:|---:|
| winner_up | 31.4% | 26.2% |
| winner_down | 17.4% | 15.2% |
| loser_up | 9.6% | 7.9% |
| loser_down | 14.5% | 14.8% |
| flat_up | 13.7% | 18.4% |
| flat_down | 13.4% | 17.5% |

## 2. Ten sessions after the volume footprint

| cell | n pre | median | euros | %>0 | high | n 2026 | median | euros | %>0 | high |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| winner_up_heavy | 8,937 | -3.7% | 4.4% | 42.5% | 14.5% | 5,614 | -4.3% | -3.7% | 37.8% | 12.2% |
| winner_up_light | 6,580 | -6.5% | -2.3% | 36.9% | 10.0% | 4,756 | -3.0% | -3.9% | 39.8% | 9.4% |
| winner_down_heavy | 4,431 | -3.9% | 3.7% | 42.4% | 14.4% | 2,999 | -4.4% | -3.2% | 37.4% | 12.1% |
| winner_down_light | 7,870 | -6.3% | -0.6% | 36.6% | 10.2% | 5,367 | -3.8% | -3.4% | 38.7% | 10.0% |
| loser_down_heavy | 5,049 | -1.7% | -1.1% | 44.7% | 11.6% | 2,757 | -3.9% | -2.4% | 38.1% | 9.0% |
| loser_down_light | 25,963 | -2.4% | -0.0% | 43.7% | 10.9% | 10,918 | -2.1% | -0.6% | 42.5% | 10.0% |
| loser_up_heavy | 4,205 | -3.9% | -5.7% | 39.5% | 10.4% | 1,906 | -4.6% | -2.8% | 33.5% | 9.9% |
| loser_up_light | 17,149 | -3.3% | -1.8% | 40.2% | 9.7% | 6,028 | -3.7% | -2.8% | 37.0% | 9.2% |
| flat_up_heavy | 5,434 | -4.2% | -1.1% | 39.2% | 9.5% | 3,549 | -2.5% | -0.6% | 41.0% | 10.3% |
| flat_up_light | 12,630 | -3.1% | -1.9% | 40.3% | 7.9% | 8,691 | -1.4% | 1.1% | 44.0% | 8.4% |
| flat_down_heavy | 3,770 | -3.8% | -0.6% | 38.4% | 8.6% | 2,744 | -2.3% | -0.1% | 41.0% | 9.2% |
| flat_down_light | 15,152 | -3.4% | -1.1% | 40.4% | 8.1% | 11,458 | -1.4% | 1.4% | 44.1% | 8.3% |

## 3. Same cells, every 10th bar of each name

| cell | n pre | median | euros | %>0 | high | n 2026 | median | euros | %>0 | high |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| winner_up_heavy | 1,031 | -4.3% | 2.3% | 40.4% | 13.5% | 516 | -3.2% | 0.4% | 39.5% | 12.3% |
| winner_up_light | 618 | -8.1% | -6.1% | 36.7% | 8.2% | 443 | -2.5% | -3.9% | 42.2% | 9.4% |
| winner_down_heavy | 506 | -2.7% | 10.4% | 44.9% | 15.2% | 338 | -3.4% | -1.9% | 36.4% | 11.4% |
| winner_down_light | 774 | -5.9% | 0.0% | 39.1% | 10.5% | 573 | -3.5% | -4.3% | 43.1% | 9.6% |
| loser_down_heavy | 558 | 0.3% | 3.3% | 50.7% | 14.2% | 213 | -5.9% | 4.1% | 35.2% | 9.8% |
| loser_down_light | 2,716 | -2.7% | 0.7% | 44.1% | 10.7% | 1,046 | -2.4% | -2.7% | 42.1% | 10.8% |
| loser_up_heavy | 360 | -2.2% | -4.0% | 45.0% | 10.8% | 218 | -3.5% | 0.1% | 35.3% | 10.1% |
| loser_up_light | 1,794 | -3.2% | 0.0% | 40.3% | 8.4% | 624 | -2.3% | -0.9% | 42.1% | 9.5% |
| flat_up_heavy | 526 | -2.9% | -0.6% | 39.2% | 9.0% | 345 | -3.1% | 3.8% | 39.7% | 9.7% |
| flat_up_light | 1,272 | -3.2% | -1.9% | 39.5% | 7.2% | 809 | -1.0% | 1.0% | 47.3% | 8.9% |
| flat_down_heavy | 436 | -3.2% | -6.2% | 40.4% | 8.4% | 284 | -2.5% | -5.1% | 40.5% | 9.8% |
| flat_down_light | 1,413 | -3.3% | -1.0% | 41.1% | 7.9% | 1,191 | -1.7% | -1.7% | 44.2% | 8.1% |

## 4. Euros inside the slow-return leaders

Each day with at least 10 liquid names, the leaders are the
top fifth by 20-session return. Concentration is their euro share
divided by their count share. Concentrated means at least
1.5; dispersed means at most 1.0. The
forward column is the median, across days, of that day's median
leader return from the next open to the close ten sessions later.

| slice | n pre | leader median | leader euros | n 2026 | leader median | leader euros |
|---|---:|---:|---:|---:|---:|---:|
| concentrated | 562 | -4.4% | -3.0% | 163 | -4.0% | -3.6% |
| dispersed | 49 | -6.7% | -6.2% | 50 | -3.4% | -4.9% |

## 5. The name that took the most euros that day

`gap` is the median of (that name's 10-session close minus the
median name that day). `euros` weights the same close by the name's
quote volume.

| magnet was | n pre | median | euros | gap | n 2026 | median | euros | gap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| any | 694 | -0.5% | 2.6% | 3.6% | 292 | -1.4% | -2.0% | 0.8% |
| winner | 294 | -2.4% | 4.1% | 4.5% | 104 | -4.2% | -5.1% | -0.3% |
| loser | 143 | 2.6% | 2.9% | 2.6% | 53 | -2.4% | -2.2% | 1.3% |
| flat | 257 | -1.2% | -0.7% | 3.7% | 135 | 0.4% | 1.6% | 1.1% |

## 6. Average print size

Binance `<BASE>USDT` daily klines for 17 bases already
cached by the entry-quality lab, through 2026-10-17.
Bitvavo does not publish trade counts. Average print is quote
volume divided by trade count. Large is at or above the 80th
percentile of the prior 60 days of that same name;
small is at or below the 20th. A buy also needs the Binance
taker-buy share at least 0.52 and a Bitvavo up close; a sell
needs 0.48 or less and a Bitvavo down close. The outcome is the
Bitvavo forward return, not the Binance close. The cache is the
liquid desk, which is where a large print can exist. It is not
a hand-picked list of winners.

### Ticket cells

| cell | n pre | median | euros | %>0 | high | n 2026 | median | euros | %>0 | high |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| large_buy | 249 | 0.5% | 1.0% | 51.0% | — | 138 | -2.4% | -1.2% | 41.3% | — |
| small_buy | 175 | -3.5% | 0.6% | 41.7% | — | 80 | -2.4% | -2.5% | 40.0% | — |
| large_buy_winner | 95 | -0.2% | 0.7% | 49.5% | — | 72 | -4.9% | -1.2% | 30.6% | — |
| small_buy_winner | 19 | -7.0% | 2.5% | 36.8% | — | 22 | 0.9% | -6.0% | 54.5% | — |
| large_sell | 397 | -2.0% | 2.2% | 44.1% | — | 262 | -0.6% | -0.0% | 45.4% | — |
| small_sell | 815 | -1.8% | -0.8% | 44.7% | — | 281 | -1.3% | 1.8% | 45.2% | — |
| large_sell_loser | 122 | 1.3% | -0.5% | 54.9% | — | 65 | -2.3% | -2.5% | 43.1% | — |
| small_sell_loser | 405 | -1.1% | -0.5% | 46.2% | — | 128 | -1.1% | 2.6% | 45.3% | — |

## 7. Wet one-slot books

€20,000 compounded, one name. Both rules require a liquid alt that
is already up at least 10% over 20 sessions and that
closed up on the day. `price` buys the strongest of those.
`size` keeps only names whose quote volume is at least 2×
the prior 20-day median (today excluded from that median) and
then buys the one with the most euros. `*_btc` adds a gate that
BTC's close is above its 50-session average. Fill is the next open,
Bitvavo taker 0.25% a side plus impact `0.02 × sqrt(notional / 20-day
average quote volume)`, capped at 3%. Exit is a 10% trail from the
prior peak, or the next open after 10 bars in the trade, whichever
comes first. A gap through the trail fills at the open. `Fresh 2026`
restarts at €20,000 on 1 January 2026.

A size book passes only when its euro PnL is positive and higher
than the matching price book, on the full path and on the fresh
2026 book. Light volume is a quote-volume ratio under 1; the book itself uses the heavy gate only.

## Full path

| book | PnL | DD | green W | trades | win | avg | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| size | -19,999 | 100.0% | 20.5% | 284 | 27.5% | -70 | -19,688 | -309 | -1 |
| price | -19,999 | 100.0% | 19.2% | 324 | 24.7% | -62 | -19,567 | -431 | -1 |
| size_btc | -18,596 | 99.2% | 22.6% | 211 | 28.9% | -88 | -19,024 | -698 | 1,126 |
| price_btc | -19,993 | 100.0% | 17.1% | 270 | 24.4% | -74 | -18,924 | -935 | -135 |

## Fresh 2026

| book | PnL | DD | green W | trades | win | avg | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| size | -18,722 | 99.0% | 31.8% | 137 | 24.8% | -137 | 0 | 0 | -18,722 |
| price | -19,916 | 99.6% | 27.3% | 163 | 22.1% | -122 | 0 | 0 | -19,916 |
| size_btc | 46,531 | 80.5% | 29.5% | 77 | 33.8% | 604 | 0 | 0 | 46,531 |
| price_btc | -19,374 | 99.0% | 18.2% | 98 | 21.4% | -198 | 0 | 0 | -19,374 |

## Reading

1. The euros sit in names that are already ahead. Before 2026, 31.4% of liquid-alt euros printed on an up day in a name already up at least 10% over 20 sessions, against 14.5% on a down day in a name already down 10%. In 2026 those shares are 26.2% and 14.8%.
2. Volume screen, both periods: none of them. On the heavy cells, the every-10th-bar median has the same sign as the full sample in 7 of 8 period-cells. On an up day in a winner with heavy volume, the median 10-session close is -3.7% before 2026 and -4.3% in 2026. The euro-weighted close is 4.4% and -3.7%.
3. Leader-concentration screen, both periods: does not pass. Magnet screen (most euros went to a winner, both periods): does not pass.
4. Ticket-size screen, both periods: none of them. The average Binance print is the public proxy for ticket size. It is not a wallet, and a 'large' print is large only versus that name's own last 60 days.
5. Fresh 2026, `size` PnL €-18,722 (137 trades, average €-137, drawdown 99.0%) against `price` €-19,916 (163 trades, average €-122). Full path `size` €-19,999 against `price` €-19,999. Size beats price with a positive book on both windows: no. The BTC-gated pair: no. Fresh 2026 `size_btc` finishes at €46,531 (drawdown 80.5%, 77 trades, median €-2,035, best €69,187 in IRYS, more than the book's whole profit, so the other trades together lost). The full path is €-18,596. Both windows have to be positive, so this cell does not pass.

## Verdict

Nothing armed. Before 2026, 31.4% of liquid-alt euros printed on an up day in a name already up at least 10% over 20 sessions, against 14.5% on a down day in a name already down 10%. In 2026 those shares are 26.2% and 14.8%. That allocation is the profile of the capital that prints the volume. It is not a fill. Requiring abnormal volume, or buying the single name that absorbed the most euros, does not beat simply buying the slow winner, once the next open, the taker, and impact are paid, on the full path and on a fresh 2026 book. A larger average print, where the trade-count cache exists, is held to the same screen and does not open a separate book. The slow residual pack remains the book that compounds; this footprint does not replace it. Fresh 2026 `size_btc` finishes at €46,531 (drawdown 80.5%, 77 trades, median €-2,035, best €69,187 in IRYS, more than the book's whole profit, so the other trades together lost). The full path is €-18,596. Both windows have to be positive, so this cell does not pass.


# Depth and signed flow

Question: can the size of the next rise be read off how low the
price sits and how much is being bought or sold? Bots are in that
flow as small tickets. This is the conditional median, not a fitted
model, and it does not re-run the crowd-bot presets.

Tape through 2026-10-29. 428 Bitvavo markets, 298 with a
Binance USDT taker print, 156,067 joined liquid days. Liquid
means the prior 20 Bitvavo sessions average at least €20,000.
Depth is one minus the close divided by the highest high of the
last 60 sessions, including today. Near is under 10%
off that high, mid is 10% to 30%, deep is 30%
or more. Buy quantity is taker-buy share times Binance quote volume,
divided by that name's own prior-20-day median of the same quantity.
Heavy is at least 2× and a taker share of at least 0.52
for a buy, or at most 0.48 for a sell. Light is under
1×. The mid band of quantity is left out. A small ticket is an
average print at or below the 20th percentile of the prior 60 days
of that name; a large ticket is at or above the 80th.

The forward path buys the next Bitvavo open. The high is the
largest high over the next 10 sessions. The close is the
close of session 10. A second table uses 20
sessions. The screen uses the 10-session table only.

Screen, fixed before the run. The shape holds when, on both sides
of 2026, each cell has at least 30 days, the median high
grows from near to mid to deep under heavy buying, the deep cell
beats the near cell by at least 2%, and it beats heavy
selling at the same depth by the same gap. The close holds when
that deep heavy-buy close is positive and beats both neighbouring
cells by at least 1%. A bot cell holds when the small
ticket has the larger high by the same high gap and the better
close, both periods. The book holds only when `deep_buy` is
positive and ahead of both `deep` and `buy`, on the full path and
on a fresh 2026 book.

Reproduce: `.venv/bin/python -m bot.research.depth_flow_lab`

Base rate, any joined day, 10 sessions: before 2026 the median high is 9.3% and the median close is -3.3% (n=99,411). In 2026 the high is 8.9% and the close is -2.3% (n=54,715).

## 1. Ten-session estimate

| location | flow | n pre | high | close | p90 high | n 2026 | high | close | p90 high |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| near | heavy_buy | 529 | 11.4% | -2.3% | 49.1% | 815 | 11.1% | -1.1% | 41.7% |
| near | light_buy | 372 | 3.8% | -0.3% | 23.7% | 497 | 6.7% | -0.6% | 28.2% |
| near | flat | 5,568 | 9.8% | -1.8% | 41.1% | 3,272 | 9.0% | -1.1% | 34.8% |
| near | light_sell | 724 | 4.5% | -1.3% | 30.9% | 693 | 6.6% | -0.4% | 27.3% |
| near | heavy_sell | 456 | 11.9% | -0.8% | 49.0% | 356 | 10.6% | -0.5% | 44.0% |
| mid | heavy_buy | 805 | 10.2% | -4.7% | 39.2% | 1,170 | 10.3% | -1.9% | 42.0% |
| mid | light_buy | 2,376 | 8.1% | -1.7% | 29.1% | 2,343 | 7.5% | -2.2% | 32.8% |
| mid | flat | 20,265 | 8.8% | -3.9% | 35.0% | 10,816 | 8.6% | -2.1% | 36.5% |
| mid | light_sell | 6,916 | 7.7% | -3.4% | 29.6% | 4,732 | 7.8% | -1.8% | 33.2% |
| mid | heavy_sell | 1,475 | 10.6% | -3.2% | 40.9% | 1,093 | 10.4% | -1.2% | 41.9% |
| deep | heavy_buy | 1,009 | 11.1% | -3.7% | 34.3% | 1,276 | 9.0% | -4.1% | 36.2% |
| deep | light_buy | 5,095 | 9.4% | -2.7% | 31.2% | 3,426 | 8.8% | -2.6% | 30.1% |
| deep | flat | 36,985 | 9.9% | -3.5% | 31.8% | 15,526 | 9.4% | -3.0% | 33.1% |
| deep | light_sell | 15,118 | 9.2% | -3.5% | 31.4% | 7,447 | 8.9% | -2.2% | 33.4% |
| deep | heavy_sell | 1,718 | 11.2% | -2.7% | 35.7% | 1,253 | 10.7% | -3.6% | 39.8% |

## 2. Twenty-session estimate

| location | flow | n pre | high | close | p90 high | n 2026 | high | close | p90 high |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| near | heavy_buy | 529 | 16.4% | -2.2% | 76.8% | 781 | 16.8% | -1.7% | 63.7% |
| near | light_buy | 372 | 5.2% | -0.6% | 29.4% | 467 | 9.7% | -1.9% | 44.6% |
| near | flat | 5,568 | 13.4% | -3.9% | 62.5% | 3,167 | 14.1% | -3.1% | 52.6% |
| near | light_sell | 724 | 6.5% | -2.3% | 40.2% | 664 | 11.4% | -1.9% | 52.1% |
| near | heavy_sell | 456 | 18.3% | -1.1% | 83.7% | 344 | 16.9% | 0.3% | 70.8% |
| mid | heavy_buy | 805 | 12.6% | -7.3% | 61.0% | 1,133 | 17.5% | -1.9% | 66.2% |
| mid | light_buy | 2,376 | 10.9% | -6.1% | 42.3% | 2,116 | 14.1% | -0.0% | 51.8% |
| mid | flat | 20,265 | 11.9% | -6.9% | 51.9% | 10,197 | 14.4% | -2.7% | 53.0% |
| mid | light_sell | 6,916 | 10.5% | -6.6% | 42.9% | 4,308 | 14.1% | -0.6% | 52.1% |
| mid | heavy_sell | 1,475 | 14.4% | -5.8% | 68.0% | 1,050 | 16.8% | -1.4% | 63.7% |
| deep | heavy_buy | 1,009 | 16.0% | -6.7% | 47.6% | 1,261 | 12.9% | -6.6% | 59.9% |
| deep | light_buy | 5,095 | 13.8% | -6.8% | 46.1% | 3,327 | 12.9% | -4.8% | 45.2% |
| deep | flat | 36,985 | 14.0% | -8.3% | 45.4% | 15,283 | 13.4% | -5.5% | 46.6% |
| deep | light_sell | 15,118 | 13.3% | -7.5% | 45.2% | 7,242 | 13.3% | -4.5% | 47.2% |
| deep | heavy_sell | 1,718 | 15.6% | -8.8% | 49.4% | 1,227 | 15.2% | -6.2% | 53.0% |

## 3. Does depth or quantity rank with the rise?

Spearman rank correlation with the 10-session high. This is a
summary of the same days. The screen above is the bins.

| sample | rho pre | n pre | rho 2026 | n 2026 |
|---|---:|---:|---:|---:|
| depth, all days | 0.06 | 99,411 | 0.05 | 54,715 |
| depth, heavy buy only | -0.00 | 2,343 | -0.07 | 3,261 |
| buy multiple, deep only | 0.04 | 59,925 | 0.03 | 28,928 |

## 4. Small tickets at a deep price

Same deep location. Heavy buy or heavy sell, split by ticket size.
Small tickets are the bot-and-retail print.

| ticket | side | n pre | high | close | n 2026 | high | close |
|---|---|---:|---:|---:|---:|---:|---:|
| small | buy | 167 | 14.4% | -1.0% | 201 | 9.9% | -2.8% |
| small | sell | 383 | 13.0% | -1.5% | 252 | 12.6% | -0.5% |
| large | buy | 361 | 9.8% | -6.3% | 469 | 8.0% | -4.5% |
| large | sell | 520 | 10.8% | -3.4% | 446 | 10.2% | -5.2% |

## 5. Wet one-slot books

€20,000 compounded, one name, Bitvavo taker 0.25% a side plus
impact `0.02 × sqrt(notional / 20-day average quote volume)`,
capped at 3%. `deep_buy` is the deep name with the heaviest buy
multiple that day. `deep` is the deepest name, flow ignored.
`buy` is the heaviest buy multiple at any depth. Exit is a 10%
trail from the prior peak, or the next open after 10 bars,
whichever comes first. A gap through the trail fills at the open.
`Fresh 2026` restarts at €20,000 on 1 January 2026.

## Full path

| book | PnL | DD | green W | trades | win | avg | median | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deep_buy | -19,968 | 99.9% | 30.8% | 208 | 29.3% | -96 | -18 | -14,904 | -4,466 | -598 |
| deep | -19,971 | 99.9% | 34.9% | 280 | 30.4% | -71 | -20 | -8,684 | -10,514 | -772 |
| buy | -19,994 | 100.0% | 32.2% | 275 | 28.0% | -73 | -12 | -16,445 | -3,328 | -220 |

## Fresh 2026

| book | PnL | DD | green W | trades | win | avg | median | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deep_buy | -19,117 | 96.2% | 29.5% | 73 | 20.5% | -261 | -203 | 0 | 0 | -19,117 |
| deep | -19,505 | 98.0% | 25.0% | 89 | 20.2% | -220 | -78 | 0 | 0 | -19,505 |
| buy | -19,702 | 98.8% | 31.8% | 89 | 21.3% | -222 | -116 | 0 | 0 | -19,702 |

## Reading

1. At a deep price with heavy buying, the 10-session median high is 11.1% before 2026 and 9.0% in 2026. The median close is -3.7% and -4.1%. Heavy buying near the high: high 11.4% / 11.1%, close -2.3% / -1.1%. Heavy selling at the same depth: high 11.2% / 10.7%, close -2.7% / -3.6%.
2. Shape screen: does not pass. Close screen: does not pass. The twenty-session table is the same rule on a slower clock. It is not a second screen.
3. Small-ticket buying at a deep price: does not pass (high 14.4% / 9.9%, close -1.0% / -2.8%). Small-ticket selling at a deep price: passes (high 13.0% / 12.6%, close -1.5% / -0.5%). A pass here is a larger high and a better close than the large ticket. It is not a positive close.
4. Fresh 2026 `deep_buy` is €-19,117 (drawdown 96.2%, 73 trades, best €2,374 in COW, the other trades lost more than this one made; median trade €-203). Full path €-19,968. Against `deep` and `buy`, both windows: no.

## Verdict

Nothing armed. The estimate is the pair of medians in the ten-session table: how low the close sits, and whether the taker print is a heavy buy or a heavy sell, read off as the median high and the median close that followed. The high does not grow with depth under heavy buying, on both sides of 2026, by the gap the screen requires. The close does not keep a positive edge over those cells. Small-ticket selling at a deep price has the larger high and the less-bad close than a large ticket, on both sides of 2026. That close is still negative, and the pre-specified book buys heavy demand, so the gap is not a fill. The one-slot book that buys the most intense buying at a deep price does not beat buying the deepest name, or buying the most intense buying at any depth, once the next open, the taker, and impact are paid.


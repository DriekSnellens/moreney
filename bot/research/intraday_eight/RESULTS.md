# Eight intraday books

15-minute bars, 2024-03-16 through 2026-10-03. 121 alts with a median
daily quote of at least €100,000. Four slots of a €20,000 book.
Longs on the spot book pay the Bitvavo taker and square-root impact.
Shorts are on the perp book only (0.05% fee and 0.05% slip per side).
There is no open-interest series. Funding is the Binance cache for the
names that have it. Rules are the package docstring. Nothing is armed.

| book | venue | pnl | max DD | 2024 | 2025 | 2026 | trades | win |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| live residual | spot | +€468,677 | 31.2% | +€132,986 | +€18,812 | +€316,879 | 96 |  |
| vwap_mr | spot long | -€19,980 | 99.9% | -€19,828 | -€152 | €0 | 3732 | 23% |
| ema_pullback | spot long | -€19,980 | 99.9% | -€19,980 | €0 | €0 | 2683 | 13% |
| ema_pullback | perp both | -€19,924 | 99.9% | -€14,579 | -€4,907 | -€439 | 14746 | 23% |
| sweep | spot long | -€19,980 | 99.9% | -€19,980 | €0 | €0 | 3530 | 22% |
| sweep | perp both | -€19,502 | 99.3% | -€11,266 | -€6,970 | -€1,266 | 38497 | 33% |
| orb | spot long | -€19,980 | 99.9% | -€19,869 | -€111 | €0 | 2748 | 14% |
| orb | perp both | -€19,192 | 98.1% | -€13,511 | -€5,307 | -€374 | 7645 | 19% |
| squeeze | spot long | -€19,980 | 99.9% | -€19,956 | -€24 | €0 | 2959 | 16% |
| squeeze | perp both | -€19,960 | 99.9% | -€16,017 | -€3,413 | -€530 | 14850 | 26% |
| rsi_macd | spot long | -€19,980 | 99.9% | -€19,980 | €0 | €0 | 2667 | 17% |
| supertrend | spot long | -€19,980 | 99.9% | -€19,970 | -€10 | €0 | 2425 | 23% |
| supertrend | perp both | -€19,943 | 99.9% | -€16,434 | -€3,188 | -€321 | 10691 | 34% |
| funding | spot long | -€19,903 | 99.7% | -€17,993 | -€1,689 | -€221 | 1978 | 20% |
| funding | perp long | -€8,699 | 77.2% | +€1,189 | -€5,152 | -€4,736 | 1994 | 33% |

Better than live: none.

On ETH alone, the VWAP book is up 0.39% per trade before costs and down 0.73%
after the Bitvavo taker and impact. The spot books above are flat after 2024
because the €20,000 is already gone. Nothing is armed.

Reproduce: `python -m bot.research.intraday_eight`

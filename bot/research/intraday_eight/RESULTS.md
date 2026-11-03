# Eight intraday books

The earlier table showed the same 99.9% drawdown because each book
spent the €20,000. A drawdown cannot pass 100%, and a later year
prints €0 once the stake is gone. That number is the ruined account,
not the tactic.

Window 2024-03-16 through 2026-10-03. 121 alts with a median daily quote
of at least €100,000. The live residual on this window
is +€468,677 with a 31.2% drawdown
(+€132,986 / +€18,812 / +€316,879, 96 trades).

VWAP bids the lower band known at the prior close, for the next bar
only, and offers that session VWAP. The EMA book bids EMA 9 the bar
after the close confirms the trend. Both rest a limit: a fill at the
limit pays the Bitvavo maker (0.15%) and a stop or a cross pays the
taker plus impact. The sweep uses the prior UTC day's high and low,
enters on the next open, and aims at the other side of that range.
Opening-range, squeeze, RSI/MACD and Supertrend still enter on the
next open, because the rule is a close breaking a level. Funding is
priced on the perp, where the funding cashflow is real; the spot row
is the price leg only. Shorts are not on the Bitvavo spot book.

Each signal below is a fixed €5,000. The sum does not stop at zero
and it is not one account: overlapping names each get €5,000. The
hole is the deepest peak-to-trough of that euro sum. The account is
one €20,000 book with four fixed €5,000 slots, and it stops taking
trades when the cash cannot fund a slot. A tactic that loses on
the average therefore spends the book down to just under €5,000,
so the account loss sits near €15,000. That is the slot floor,
not a shared drawdown. Read the average and the sum.
Parameters were locked before this replay. Nothing is armed.

| book | trades | win | avg net | pf | sum | hole | 2024 | 2025 | 2026 | account | DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| vwap_mr long | 22649 | 13% | -0.55% | 0.30 | -€624,994 | €627,082 | -€158,197 | -€271,525 | -€195,273 | -€15,025 | 75.2% |
| ema_pullback long | 69181 | 12% | -0.63% | 0.38 | -€2,186,950 | €2,188,376 | -€541,002 | -€858,552 | -€787,397 | -€15,053 | 75.7% |
| ema_pullback short | 78153 | 19% | -0.16% | 0.74 | -€612,699 | €617,081 | -€190,380 | -€168,724 | -€253,595 | -€15,014 | 76.5% |
| sweep long | 50895 | 14% | -0.86% | 0.42 | -€2,188,088 | €2,200,728 | -€525,113 | -€968,981 | -€693,993 | -€15,019 | 75.1% |
| sweep short | 50161 | 17% | -0.14% | 0.85 | -€350,490 | €369,491 | -€113,246 | -€86,126 | -€151,118 | -€15,042 | 78.5% |
| orb long | 35742 | 14% | -1.03% | 0.40 | -€1,848,297 | €1,850,902 | -€423,977 | -€703,027 | -€721,293 | -€15,010 | 75.0% |
| orb short | 37680 | 20% | -0.19% | 0.82 | -€358,380 | €369,426 | -€129,489 | -€105,486 | -€123,405 | -€15,019 | 79.0% |
| squeeze long | 19839 | 16% | -0.93% | 0.31 | -€918,311 | €919,368 | -€203,156 | -€376,022 | -€339,134 | -€15,069 | 77.6% |
| squeeze short | 21042 | 25% | -0.19% | 0.74 | -€203,668 | €219,132 | -€41,667 | -€78,548 | -€83,454 | -€15,001 | 79.0% |
| rsi_macd long | 68924 | 18% | -0.93% | 0.33 | -€3,215,386 | €3,220,897 | -€705,258 | -€1,214,530 | -€1,295,598 | -€15,040 | 76.1% |
| supertrend long | 30721 | 22% | -0.98% | 0.40 | -€1,504,968 | €1,506,976 | -€297,705 | -€646,292 | -€560,972 | -€15,044 | 75.2% |
| supertrend short | 27299 | 34% | -0.23% | 0.80 | -€314,120 | €321,211 | -€132,500 | -€22,727 | -€158,893 | -€15,127 | 78.1% |
| funding long | 2328 | 24% | -0.64% | 0.47 | -€74,001 | €75,188 | -€12,648 | -€33,611 | -€27,741 | -€15,051 | 75.9% |
| funding perp | 2328 | 34% | -0.07% | 0.91 | -€8,485 | €18,901 | +€5,322 | -€5,588 | -€8,219 | -€10,075 | 62.1% |

Exits:

- vwap_mr long: stop 19061, target 2173, time 1415
- ema_pullback long: signal 32894, stop 36287
- ema_pullback short: signal 36147, stop 42006
- sweep long: gap 9305, stop 34070, target 4351, time 3169
- sweep short: gap 8449, stop 32994, target 5146, time 3572
- orb long: gap 10882, stop 18846, time 6014
- orb short: gap 11397, stop 18440, time 7843
- squeeze long: gap 3167, signal 8755, stop 7917
- squeeze short: gap 3492, signal 9937, stop 7613
- rsi_macd long: gap 6099, signal 46749, stop 16076
- supertrend long: gap 10237, signal 3, stop 20481
- supertrend short: gap 8387, stop 18912
- funding long: gap 331, signal 322, stop 1675
- funding perp: gap 331, signal 322, stop 1675

Better than the live residual: none.

Reproduce: `python -m bot.research.intraday_eight`

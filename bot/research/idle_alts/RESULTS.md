# Idle alts

The live book is unchanged. On days it is flat at both closes,
idle euros can buy up to four non-desk alts after an 8% drop
that BTC did not share. Rules are the package docstring.

| book | pnl | max DD | 2024 | 2025 | 2026 | buys |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| live | +€468,677 | 31.2% | +€132,986 | +€18,812 | +€316,879 | 96 |
| control, no alts | +€468,677 | 31.2% | +€132,986 | +€18,812 | +€316,879 | 0 |
| live + idle alts | -€3,085 | 94.0% | +€70,358 | -€66,746 | -€6,698 | 960 |

Idle exits: 613 at +6%, 270 on the fourth session, 75 because the live book took the cash back.

Not better than live.
Nothing is armed.

Reproduce: `python -m bot.research.idle_alts`

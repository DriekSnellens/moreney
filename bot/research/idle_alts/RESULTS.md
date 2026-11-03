# Idle alts

The live book is unchanged. On days it is flat at both closes,
idle euros can buy up to four non-desk alts after an 8% drop
that BTC did not share. `capped` sells at +6% or on the fourth
session. `stop` sells 6% under the entry and lets a winner run
until the live book wants the cash. Rules were locked before the replay.

| book | pnl | max DD | 2024 | 2025 | 2026 | buys |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| live | +€468,677 | 31.2% | +€132,986 | +€18,812 | +€316,879 | 96 |
| control, no alts | +€468,677 | 31.2% | +€132,986 | +€18,812 | +€316,879 | 0 |
| capped profit | -€1,974 | 93.5% | +€70,358 | -€65,028 | -€7,304 | 950 |
| stopped loss | -€17,076 | 99.0% | +€52,158 | -€66,738 | -€2,495 | 607 |

Capped exits: 602 at +6% (+5.0%), 272 on the fourth session (-12.5%), 74 handed back (-9.4%).
Stopped exits: 501 at -6% (-6.8%), 1 gapped through the stop (-7.3%), 102 handed back (+15.2%).

The stopped book is not better than live.
Nothing is armed.

Reproduce: `python -m bot.research.idle_alts`

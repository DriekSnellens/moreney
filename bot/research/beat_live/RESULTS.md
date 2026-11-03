# Beat-live search

Window ending 2026-10-03, book €20,000, wet Bitvavo next-open.
AlphaI, the intraday books, the factor overlays, the bear sleeve
and the volume-spike books were already replayed. They are not
in this table. The live row is the armed residual pack. A runner
cell keeps that pack and, once the peak gain reaches the arm,
either widens the 10% trail to 20% or turns the trail and the
lock off. Below the
arm the exit is unchanged. `flatten_regime` keeps a qualifying alt
when BTC is under its SMA50, where the live book goes to cash.
`equity_ma20` starts no new name while the book is under its own
20-day equity average. A name already held is kept.

Considerably better means at least 1.5 times the live pnl
on 2024-03-16, each year at least the live year, drawdown not deeper,
and a pnl at least as high as the live book on every start from
2024-03-16 through 2024-03-22. Nothing is armed.

## 2024-03-16

| cell | pnl | max DD | trades | 2024 | 2025 | 2026 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| live | +€468,677 | 31.2% | 96 | +€132,986 | +€18,812 | +€316,879 |
| runner_20_trail20 | +€61,460 | 66.0% | 95 | +€77,830 | -€45,497 | +€29,127 |
| runner_40_trail20 | +€372,955 | 34.3% | 93 | +€88,675 | +€15,571 | +€268,709 |
| runner_40_hold | +€364,534 | 43.6% | 95 | +€94,546 | -€7,191 | +€277,179 |
| flatten_regime | +€91,049 | 68.3% | 128 | +€103,952 | -€57,491 | +€44,589 |
| equity_ma20 | +€279,947 | 31.4% | 70 | +€90,822 | +€18,548 | +€170,577 |

## Other start days

| cell | 2024-03-16 | 2024-03-17 | 2024-03-18 | 2024-03-19 | 2024-03-20 | 2024-03-21 | 2024-03-22 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| live | +€468,677 | +€359,102 | +€381,710 | +€431,164 | +€398,517 | +€396,355 | +€430,577 |
| runner_20_trail20 | +€61,460 | +€53,919 | +€58,649 | +€69,096 | +€67,041 | +€61,729 | +€68,971 |
| runner_40_trail20 | +€372,955 | +€282,704 | +€301,246 | +€341,950 | +€315,057 | +€313,280 | +€341,466 |
| runner_40_hold | +€364,534 | +€275,776 | +€293,995 | +€334,020 | +€307,571 | +€305,823 | +€333,544 |
| flatten_regime | +€91,049 | +€62,367 | +€67,428 | +€82,278 | +€71,198 | +€70,713 | +€82,141 |
| equity_ma20 | +€279,947 | +€207,390 | +€222,153 | +€254,826 | +€233,199 | +€231,775 | +€254,435 |

Considerably better: none.

Reproduce: `python -m bot.research.beat_live`

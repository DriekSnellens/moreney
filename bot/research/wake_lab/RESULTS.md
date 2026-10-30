# Wake scan

Question: after a real decline and a long quiet shelf, does the first
day of interest — a modest rise on expanded volume, still well under
the old high — show up before a later spike, often enough to be useful?

The shape was read off one chart: a listing dump, about three quiet
weeks, a short flush, then one green day of about +30% on much higher
volume, with the vertical day five sessions later. The rule is generic.
It does not name that market. The same thresholds are applied to every
Bitvavo EUR daily series.

Tape through 2026-10-29 (427 markets with a completed bar). Entry in the audit is the next day's open. The in-progress UTC day is
not a signal.

Phases, all on completed bars:

- Decline: the shelf median sits at least 40.0% under the high of the prior 90 days (or all available history once 30 days exist).
- Shelf: 21 days, close range at most 35.0%, median absolute day move at most 3.5%, at most 2 days wider than 8.0%. The shelf may end up to 10 days before the signal, so a short final dip still counts.
- Quiet: shelf median quote volume is at most 50.0% of the active (top 30%) quote volume before the shelf.
- Interest: that day's rise is 8.0% to 70.0%, the close is 15.0% to 55.0% off the recent low, quote volume is at least 3× the shelf and at least €20,000, and the close is still under 70.0% of the old high.
- One signal per name per 8 days.

`before the example month` is every signal before 2026-10-01. That slice was not used to set the thresholds. `example month` is the rest, and it contains the chart the shape was read from.

The volume-pop row is the ablation: the same up-day and volume multiple against the prior 21 days, with no decline and no shelf.

Reproduce: `.venv/bin/python -m bot.research.wake_lab --refresh`

## Full tape

Signals 1328, with a next open 1328. A boom is a high at least 50.0% above the next open.

| horizon | n | close mean | close median | % close > 0 | % high ≥ 20 | % high ≥ boom |
|---|---:|---:|---:|---:|---:|---:|
| 3d | 1326 | -0.9% | -2.7% | 39.1% | 21.3% | 4.5% |
| 5d | 1324 | -1.8% | -4.0% | 36.5% | 25.6% | 6.1% |
| 10d | 1320 | -4.0% | -6.1% | 32.7% | 32.3% | 8.2% |

## Before the example month

Signals 1313, with a next open 1313. A boom is a high at least 50.0% above the next open.

| horizon | n | close mean | close median | % close > 0 | % high ≥ 20 | % high ≥ boom |
|---|---:|---:|---:|---:|---:|---:|
| 3d | 1313 | -0.8% | -2.7% | 39.3% | 21.1% | 4.3% |
| 5d | 1313 | -1.8% | -4.0% | 36.6% | 25.4% | 5.8% |
| 10d | 1313 | -3.9% | -6.0% | 32.8% | 32.4% | 8.1% |

## Example month

Signals 15, with a next open 15. A boom is a high at least 50.0% above the next open.

| horizon | n | close mean | close median | % close > 0 | % high ≥ 20 | % high ≥ boom |
|---|---:|---:|---:|---:|---:|---:|
| 3d | 13 | -9.5% | -17.4% | 23.1% | 38.5% | 30.8% |
| 5d | 11 | -7.7% | -20.3% | 27.3% | 45.5% | 45.5% |
| 10d | 7 | -28.9% | -17.6% | 0.0% | 28.6% | 28.6% |

## Ablation — volume pop, before the example month

Signals 5428, with a next open 5428. A boom is a high at least 50.0% above the next open.

| horizon | n | close mean | close median | % close > 0 | % high ≥ 20 | % high ≥ boom |
|---|---:|---:|---:|---:|---:|---:|
| 3d | 5428 | -0.8% | -2.6% | 39.8% | 20.3% | 4.5% |
| 5d | 5428 | -0.7% | -3.4% | 39.2% | 26.5% | 6.7% |
| 10d | 5428 | -1.9% | -5.6% | 36.9% | 35.2% | 10.5% |

## Likeness bands, before the example month

Ten days after the next open. A higher likeness is a deeper decline,
a tighter shelf, a louder volume jump, and a lift nearer +30%.

| likeness | n | close median | % high ≥ boom |
|---|---:|---:|---:|
| 0.00–0.50 | 35 | -2.5% | 11.4% |
| 0.50–0.60 | 194 | -5.1% | 4.6% |
| 0.60–0.70 | 500 | -5.8% | 7.6% |
| 0.70–0.80 | 500 | -6.0% | 9.2% |
| 0.80–1.00 | 84 | -9.1% | 10.7% |

## Example month, signal by signal

| base | day | likeness | drawdown | lift | day ret | vol x | qvol |
|---|---|---:|---:|---:|---:|---:|---:|
| FUN | 2026-10-03 | 0.71 | 60.7% | 19.0% | 17.4% | 31.4 | 486,655 |
| HFT | 2026-10-03 | 0.65 | 85.2% | 51.3% | 35.9% | 89.0 | 601,993 |
| DOS | 2026-10-10 | 0.79 | 72.7% | 18.7% | 8.1% | 12.2 | 197,291 |
| LUMIA | 2026-10-10 | 0.73 | 43.4% | 28.0% | 24.8% | 115.2 | 1,824,690 |
| MANTA | 2026-10-11 | 0.73 | 56.4% | 17.8% | 9.8% | 16.6 | 458,398 |
| U | 2026-10-13 | 0.51 | 61.0% | 20.3% | 20.3% | 3.2 | 89,357 |
| EPIC | 2026-10-17 | 0.67 | 59.8% | 16.3% | 16.3% | 46.8 | 2,511,371 |
| IOST | 2026-10-20 | 0.70 | 61.4% | 42.7% | 42.3% | 65.0 | 757,062 |
| BIRB | 2026-10-21 | 0.55 | 46.4% | 16.1% | 8.7% | 22.1 | 341,878 |
| GROVE | 2026-10-22 | 0.79 | 86.4% | 30.1% | 30.1% | 20.3 | 180,862 |
| ARK | 2026-10-23 | 0.66 | 52.7% | 25.1% | 22.3% | 26.0 | 700,809 |
| NEX | 2026-10-26 | 0.77 | 80.2% | 31.9% | 28.6% | 72.0 | 838,189 |
| SYN | 2026-10-26 | 0.57 | 57.1% | 19.9% | 17.4% | 3.9 | 320,291 |
| XAN | 2026-10-27 | 0.69 | 51.8% | 30.4% | 29.5% | 11.0 | 527,671 |
| BICO | 2026-10-28 | 0.65 | 80.6% | 18.0% | 11.8% | 7.1 | 207,941 |

## Open now

Last 7 completed days, and the high since the signal is still under +50% from that close. Ranked by likeness to the shape.

| base | day | likeness | drawdown | shelf range | lift | day ret | vol x | qvol | high since |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NEX | 2026-10-26 | 0.77 | 80.2% | 29.7% | 31.9% | 28.6% | 72.0 | 838,189 | 16.8% |
| XAN | 2026-10-27 | 0.69 | 51.8% | 32.1% | 30.4% | 29.5% | 11.0 | 527,671 | 30.8% |
| ARK | 2026-10-23 | 0.66 | 52.7% | 30.5% | 25.1% | 22.3% | 26.0 | 700,809 | 9.5% |
| BICO | 2026-10-28 | 0.65 | 80.6% | 32.3% | 18.0% | 11.8% | 7.1 | 207,941 | 5.8% |
| SYN | 2026-10-26 | 0.57 | 57.1% | 25.7% | 19.9% | 17.4% | 3.9 | 320,291 | 13.5% |

## Reading

The rule matches the chart it was read from. On GROVE the only wake
is 2026-10-22: drawdown 86%, close +30% off the low, 20× the shelf
volume, likeness 0.79. The vertical close on 2026-10-27 sits outside
the interest band, so it is not a second signal. The next session
gave that pop back. The high above +50% printed later the same week,
and the close has been giving it back since.

That path is not the typical one.

1. Before October 2026 there are 1313 wakes with a full 10-day window. The median close is -6.0% and 32.8% of closes are positive. A high of +50% or more shows up in 8.1% of those windows.
2. The ablation drops the decline and the shelf. It keeps 5428 up days on 3× the prior 21-day volume. The 10-day boom rate is 10.5% and the median close is -5.6%. The shelf fires less often, and not into more spikes.
3. Likeness does not sort winners from losers. Every band has a negative 10-day median close.
4. October is 15 signals, and it is the month the thresholds were read from. Among the windows with five completed days, 45.5% print a +50% high and the median close is -20.3%. The high is a wick.

## Verdict

Nothing armed. The open list is who matches the shape on the last
completed days and has not already traded +50% above that close.
On this tape the shape describes a chart. It does not enrich for
the later spike. A volume pop without the shelf already captures
the wick more often, and the close after either signal is negative.


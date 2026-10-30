# What is followed by the largest rises

Question: after the quiet-shelf wake failed to enrich for spikes,
which prior state on this tape is followed by the largest rises,
in both the earlier years and in 2026?

Every Bitvavo EUR name, daily bars, quote volume at least €20,000 on the signal day. Entry is the next open. The high and the close are measured over the next 7 bars. The in-progress UTC day is dropped. Rows run through 2026-10-22.

States overlap: a hot day is also liquid. The 3-day return buckets
do not overlap. `before 2026` was not used to pick the continuation
rule; that rule is the one the moonshot preimage already measured
as the highest precision on a +50% high within 7 days.

Reproduce: `.venv/bin/python -m bot.research.rise_lab`

## Before 2026

| state | n | high median | high p90 | close median | % high ≥ 20 | % high ≥ 50 |
|---|---:|---:|---:|---:|---:|---:|
| 3d return negative | 67409 | 8.1% | 28.2% | -2.3% | 17.8% | 3.0% |
| 3d return 0 to 15% | 50237 | 7.6% | 28.1% | -3.1% | 18.0% | 3.0% |
| 3d return 15 to 40% | 7252 | 11.1% | 38.5% | -5.0% | 28.2% | 6.1% |
| 3d return ≥ 40% | 875 | 16.9% | 58.9% | -11.8% | 44.3% | 13.8% |
| any liquid day | 125773 | 8.1% | 29.0% | -2.7% | 18.7% | 3.2% |
| 10d return under 12% | 101396 | 7.7% | 27.4% | -2.5% | 17.1% | 2.8% |
| decline, quiet shelf, first interest day | 645 | 10.4% | 34.7% | -5.2% | 27.3% | 4.7% |
| 20d high, day ≥ 6%, volume ≥ 2× | 1861 | 12.4% | 46.1% | -4.7% | 32.5% | 8.5% |
| 10d up and ≥ 3.5% ahead of BTC | 33754 | 8.6% | 32.3% | -4.6% | 21.9% | 4.1% |
| 3d return ≥ 15% | 8127 | 11.6% | 40.7% | -5.5% | 29.9% | 6.9% |
| 10d excess vs BTC ≥ 25% | 6975 | 12.3% | 43.9% | -6.2% | 32.4% | 7.9% |
| 3d ≥ 15%, excess ≥ 25%, above SMA20 > SMA50 | 1655 | 13.6% | 48.2% | -5.6% | 36.7% | 9.2% |
| strongest 3d return that day | 660 | 15.1% | 55.7% | -10.2% | 38.3% | 12.6% |

## 2026

| state | n | high median | high p90 | close median | % high ≥ 20 | % high ≥ 50 |
|---|---:|---:|---:|---:|---:|---:|
| 3d return negative | 36540 | 7.2% | 28.6% | -1.9% | 17.4% | 3.2% |
| 3d return 0 to 15% | 30318 | 7.5% | 29.4% | -1.9% | 18.5% | 3.6% |
| 3d return 15 to 40% | 3818 | 11.8% | 44.8% | -4.0% | 31.2% | 8.3% |
| 3d return ≥ 40% | 671 | 18.1% | 101.3% | -15.3% | 47.2% | 22.1% |
| any liquid day | 71347 | 7.6% | 30.3% | -2.0% | 18.9% | 3.8% |
| 10d return under 12% | 56901 | 7.2% | 28.3% | -1.8% | 17.3% | 3.3% |
| decline, quiet shelf, first interest day | 675 | 11.2% | 48.1% | -5.6% | 29.6% | 9.2% |
| 20d high, day ≥ 6%, volume ≥ 2× | 1568 | 12.0% | 53.7% | -5.4% | 32.6% | 10.8% |
| 10d up and ≥ 3.5% ahead of BTC | 21372 | 9.0% | 35.6% | -3.0% | 23.5% | 5.3% |
| 3d return ≥ 15% | 4489 | 12.5% | 50.8% | -5.0% | 33.6% | 10.4% |
| 10d excess vs BTC ≥ 25% | 4312 | 13.2% | 50.6% | -5.8% | 35.0% | 10.3% |
| 3d ≥ 15%, excess ≥ 25%, above SMA20 > SMA50 | 1191 | 15.0% | 70.3% | -6.5% | 40.7% | 15.5% |
| strongest 3d return that day | 295 | 20.4% | 123.9% | -16.8% | 50.5% | 23.1% |

## Reading

The next week's high scales with how hard the name is already rising.
A negative or mild 3-day return is followed by the same high as any
liquid day. From there the median high, the p90, and the share of
+50% highs all step up together, in the years before 2026 and again
in 2026.

1. The strongest 3-day return on the board is the top of that scale. Before 2026 the median high is 15.1% and 12.6% of the next 7 days trade +50% above the next open (n=660). In 2026 those are 20.4% and 23.1% (n=295). A 3-day return already at +40% or more is the same family: median high 16.9% then 18.1%, +50% high 13.8% then 22.1%.
2. The rule measured earlier on an 80-name universe is the broad version: 3-day return at least +15%, 10-day return at least 25 points ahead of BTC, close above the 20-day average and that average above the 50-day. Median high 13.6% before 2026 and 15.0% in 2026. A +50% high lands 9.2% then 15.5% of the time, against 3.2% and 3.8% on any liquid day.
3. A quiet 10-day tape, and the decline-shelf-interest wake, sit next to the base rate. In 2026 the wake's median high is 11.2% and its +50% rate is 9.2%, on a base of 7.6% and 3.8%.
4. The close does not keep the high. Every state above has a negative 7-day median close. The states with the largest highs give the most back: the day's leader closes at -16.8% in 2026, and a name already up 40% in three days closes at -15.3%. The high is real. A hold to the 7-day close is not where it is paid.

## Verdict

The structural high is continuation of a name that is already
running, not a quiet base waking up. The fattest 7-day highs follow
the strongest 3-day return on the board. The same ordering holds
before 2026 and in 2026. Nothing here is armed: the close after
those highs is negative, and the live book is a milder leader held
for weeks, which is a different payoff than this 7-day high.


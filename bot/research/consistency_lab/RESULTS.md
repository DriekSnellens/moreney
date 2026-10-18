# Consistency lab — how green can weeks and months get?

Owner goal (18 Oct 2026): "liefst elke week en maand groen". This lab
measures what the tape allows instead of promising it. Every variant is a
generic overlay on the armed `residual_full` pack (EUR 20k, compounding,
WET fills, 16-name universe, Bitvavo daily bars through 2026-10-18). Weeks
are ISO weeks, months calendar months, both marked on the equity curve.

Run: `.venv/bin/python -m bot.research.consistency_lab`

Lessons already in the project that bound this search:
- `daily_green_lab/CONTINUOUS.md`: structurally green every day is not
  available; best OOS green-day rate 40–45% on 81 names.
- `crowd_lab/RESULTS.md`: RSI/EMA/grid/DCA defaults lose 2024–2026 after
  fees; sub-1% fades are fee-destroyed.
- `bear_sleeve/RESULTS.md`: no profitable bear sleeve on Bitvavo spot; cash
  is the bear tactic; single clocks are a lottery on the start day.
- `volume_velocity/RESULTS.md`: minute-level volume bursts in the EUR
  200–300k section lose after taker fees.
- `entry_quality_lab/RESULTS.md`: buy-high filters, news proxies, taker
  flow and early peak-cashing all cost 45–100% of the PnL.

## Full window 2024-03-16 → 2026-10-18 (139 weeks, 32 months)

| variant | PnL | DD | green W | red W | flat W | worst W | green M | red M | worst M | red streak M |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline (live pack) | 355,988 | 31.6% | 32% | 29% | 39% | -15.4% | 53% | 44% | -23.1% | 2 |
| btc25_alt75 | 208,994 | 25.0% | 37% | 38% | 25% | -12.2% | 56% | 44% | -17.2% | 2 |
| btc50_alt50 | 103,048 | 28.1% | 37% | 38% | 25% | -10.2% | 53% | 47% | -14.7% | 3 |
| btc_only_sma50 | 9,194 | 39.1% | 34% | 38% | 28% | -15.1% | 44% | 56% | -23.0% | 3 |
| alt_size50 (half the book) | 104,740 | 17.9% | 32% | 29% | 39% | -8.4% | 53% | 44% | -11.2% | 2 |
| alt_size33 (a third) | 53,225 | 12.7% | 32% | 29% | 39% | -5.8% | 53% | 44% | -7.3% | 2 |
| vol_t40 (vol-target 40%) | 84,874 | 15.5% | 32% | 29% | 39% | -7.2% | 53% | 44% | -8.9% | 2 |
| vol_t60 | 164,235 | 23.0% | 32% | 29% | 39% | -8.9% | 53% | 44% | -12.0% | 2 |
| n_alts2 | 27,974 | 41.8% | 26% | 35% | 39% | -13.5% | 50% | 50% | -17.7% | 2 |
| n_alts3 | 18,371 | 34.4% | 26% | 36% | 38% | -14.3% | 47% | 53% | -16.5% | 1 |
| exit_trail6 | 47,464 | 28.7% | 29% | 29% | 42% | -15.3% | 44% | 56% | -17.7% | 3 |
| exit_trail8 | 80,404 | 30.8% | 31% | 29% | 40% | -15.3% | 53% | 47% | -22.3% | 2 |
| exit_half_at15 | 340,592 | 31.6% | 30% | 30% | 40% | -15.4% | 53% | 47% | -23.0% | 2 |
| exit_stop5 (hard -5% stop) | 151,714 | 37.7% | 29% | 32% | 39% | -15.3% | 44% | 56% | -20.9% | 3 |
| exit_time5 | 51,134 | 34.0% | 29% | 32% | 39% | -17.4% | 50% | 50% | -22.1% | 2 |
| rebalance3 | 20,120 | 61.9% | 29% | 34% | 37% | -19.1% | 34% | 66% | -24.2% | 3 |
| rebalance5 | 99,763 | 34.7% | 29% | 31% | 40% | -18.4% | 38% | 62% | -16.3% | 3 |
| rebalance10 | -2,178 | 52.4% | 21% | 28% | 51% | -15.3% | 25% | 75% | -21.6% | 6 |
| rebalance14 | 31,628 | 43.8% | 19% | 25% | 56% | -15.3% | 41% | 59% | -22.5% | 3 |
| immediate_rebuy | 98,598 | 63.9% | 32% | 33% | 35% | -27.1% | 41% | 59% | -27.9% | 3 |
| ens_7clocks (7 staggered week clocks, 1/7 each) | 19,874 | 34.5% | 29% | 37% | 34% | -10.2% | 34% | 66% | -14.3% | 6 |
| ens_5trails (trail 8–12%, 1/5 each) | 205,650 | 26.7% | 33% | 30% | 37% | -15.3% | 53% | 47% | -20.4% | 2 |
| ens_7clocks_5trails | 9,836 | 36.7% | 28% | 40% | 32% | -10.0% | 34% | 66% | -13.0% | 5 |
| ens_7clocks_btc50 | 12,282 | 28.1% | 34% | 46% | 20% | -10.9% | 38% | 62% | -9.6% | 5 |
| ens_7clocks_vol60 | 9,672 | 22.5% | 29% | 37% | 34% | -6.7% | 31% | 69% | -8.6% | 6 |
| ens_7clocks_half15 | 20,773 | 29.6% | 30% | 35% | 35% | -8.7% | 41% | 59% | -14.2% | 5 |
| ens_7clocks_n2 | 7,645 | 29.6% | 29% | 38% | 33% | -9.8% | 31% | 69% | -11.1% | 3 |

IS (→2025-12-31) / OOS (2026) for the relevant rows:

| variant | IS PnL | IS green M | IS worst M | OOS PnL | OOS green M | OOS worst M |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 151,798 | 55% | -12.7% | 32,119 | 50% | -21.7% |
| btc25_alt75 | 91,746 | 59% | -12.3% | 24,653 | 50% | -16.6% |
| alt_size50 | 50,337 | 55% | -6.2% | 16,508 | 50% | -11.0% |
| alt_size33 | 28,016 | 55% | -4.1% | 9,264 | 50% | -7.3% |
| vol_t40 | 34,045 | 55% | -8.9% | 19,921 | 50% | -7.3% |
| ens_7clocks | 7,634 | 27% | -14.3% | 6,118 | 40% | -10.2% |
| ens_5trails | 95,903 | 55% | -10.7% | 16,951 | 40% | -20.0% |

Baseline monthly returns: 15 of 32 months green, 14 red, 3 flat (cash).
Median month +1.7%. Two months (2024-11 +163%, 2026-09 +120%) carry the
result; the month after each was red (2024-12 was green, 2026-10 -23%).

## What this says

1. **No lever gets anywhere near "every week green".** The best green-week
   share is 37% (with BTC in the mix), the best green-month share 56%.
   Red weeks are 29–46% of all weeks for every variant. The pack earns in
   roughly one month out of two and spends the rest flat or giving back.
2. **Size changes the depth of red, not its frequency.** A third of the
   book keeps 53% green months exactly, cuts the worst week from -15.4% to
   -5.8%, the worst month from -23% to -7.3%, DD from 32% to 13%, and
   still returns +266% over 2.5 years (+€53k on €20k). Vol-targeting at
   40% does the same with slightly better OOS.
3. **Tighter exits make it worse, not better.** Trail 6%, a -5% hard stop,
   5-day time stop, 3-day clock: fewer green months, same or deeper worst
   month, longer red streaks. Churn adds red weeks.
4. **The staggered-clock ensemble is the honest expectation.** Running the
   identical pack on all 7 weekday clocks returns +€10–20k (≈ +50–100%)
   over 2.5 years with 34% green months and a 6-month red streak. The
   +1,700% of the single baseline path is one of those seven clocks. That
   gap is parameter and start-day luck, not edge.
5. The only structure with more green than red months in this project is
   nothing in this lab; cash (0%) is the closest thing to "never red".

## Verdict

"Every week and month green" is not available from this strategy or from
any variant of it, and the earlier labs show it is not available from the
crowd's indicator bots, grids, fades or minute-volume bursts either. What
is available: the same expectancy at a size where the red months are
single-digit (`alt_size33` or `vol_t40`), accepting that about half the
months are red and that the headline +1,700% is a lucky path whose fair
value is closer to +50–100% per 2.5 years.

Nothing armed by this lab.

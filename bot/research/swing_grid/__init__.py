"""Intraday swing grid. Thresholds locked before the wet replay.

The day's own high and low are not known when the orders go in. Selection
and limit prices use completed prior UTC sessions only. A 15-minute bar
does not reveal whether the low or the high printed first, so the headline
cell refuses a round trip inside one bar.

Universe is the live desk. One name per UTC day, the highest average chop
over the five most recent completed sessions, alphabetical on a tie. Chop
is (high − low − absolute net move) / open. The average must be at least
0.02. Median quote volume over those sessions must be at least €100,000.
The last of those sessions must be at most two calendar days old. Each
buy rung is half of current equity and may not exceed 10% of that median
quote volume. Otherwise the book stays in cash.

The reference price is the prior session's close. The range fraction is
the mean of (high − low) / open over the same five sessions. Two buy
limits rest at 0.35 and 0.70 of that range below the reference, but only
when they are strictly below the session open. Each filled buy arms a
paired sell the same distance above the reference. Unfilled limits expire
at the session close. Inventory still open on the last 15-minute bar is
sold at that close.

Resting limits pay the Bitvavo retail maker fee (0.15%) and fill at the
limit. The flatten pays the retail taker fee (0.25%) plus impact
0.02 × sqrt(notional / median quote volume), capped at 3%.

Cells, also locked before the run:

- strict: a lot bought inside a bar cannot be sold inside that bar.
- path: a bar that closes at or above its open is treated as low-then-high,
  so a same-bar round trip is allowed. A down bar is high-then-low.
- retest: after 16 completed bars, if the printed range is at least 2% of
  the open, buy a retest at 20% up from the printed low and sell at 20%
  down from the printed high. Levels come from bars that have already
  closed. The sell price freezes when the buy fills. One full-book lot.
- hold: buy the same name at the session open and sell it at the close,
  both as taker with the same impact. Control for holding the jumpy coin.
- oracle: not tradable. Buy the session low and sell the session high,
  maker both sides. A ceiling, not a candidate.

A follow-up specified the order the first replay did not test. Locked
before that second replay, and not fit to it: once a 15-minute bar has
closed, the buy sits 1% above the lowest low of the completed bars. That
is around the trough already printed, and the order does not use a lower
low from the bar that fills it. One full-book lot per session. The sell
is 5% or 10% above the fill. Cells ``bounce5`` and ``bounce10``. A same-bar
round trip does not count. Leftover inventory is sold at the session close.

Book €20,000, compounding, long only, flat by the close. Report the full
path, the path before 2026, and 2026, each restarted at €20,000. Nothing
is wired into the live book.
"""

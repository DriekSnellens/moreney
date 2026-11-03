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

The third replay asks which change to that bid is profitable. Four cells,
locked before the run, each changing one lever:

- ``confirm5``: the low must be held by a later bar that closes back above
  it before the bid exists. Sell 5% above the fill. Flat at the close.
- ``stop5``: the same bid and 5% sell, but a later bar that trades 2%
  through the low that set the bid is a taker stop. A bar that touches
  both the stop and the target takes the stop.
- ``shallow5``: the same bid and 5% sell, and no bid once the low is more
  than 4% under the session open.
- ``hold3``: the same bid and 5% sell, kept for up to three sessions
  instead of sold at the first close. Still flat after the third close.

The fourth replay changes the name or the target, not the stop. Locked
before that run:

- ``liquid5``: the same 1% bid and 5% sell, on the name with the highest
  median turnover among those that clear the chop floor.
- ``early5``: the same bid and sell, and the bid expires after the first
  16 quarter-hours.
- ``reclaim``: the same bid, sold at the session open when that open is
  far enough above the bid to clear the maker round trip.
- ``prior``: the buy is set at the open, halfway down the prior five-day
  range, and the sell is the prior close.

Book €20,000, compounding, long only. Report the full path, the path
before 2026, and 2026, each restarted at €20,000. Nothing is wired into
the live book.
"""

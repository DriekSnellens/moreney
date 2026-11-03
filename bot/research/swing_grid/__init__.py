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

The fifth replay only fades a dip when the larger trend is up, or when
today has not already exceeded a normal range, plus one tight round trip
on the busiest name. Locked before that run:

- ``sma_bounce``: bounce5, and only a name whose prior close is above its
  own 20-session average.
- ``sma_reclaim``: the reclaim sell, with that same average.
- ``sma_prior``: the prior-close sell, with that same average.
- ``quiet5``: bounce5, and no bid once today's range already exceeds the
  prior five-day average range.
- ``clip``: on the busiest name, buy 0.5% under the open and sell 0.5%
  above the fill. 0.5% is the round trip that still clears the 0.30%
  maker cost.

The sixth replay changes the fill, the scale of the exit, or which rung
rests. Locked before that run:

- ``double5``: the first trade back to within 1% of a completed-bar low
  is a test and is not a buy. A later bar that trades back to within 1%
  of that same low, without printing more than 0.5% through it, buys at
  1% above that low. A break of more than 0.5% resets the count. The
  0.5% break is half the cushion: a wick larger than that is a new low.
  Sell 5% above the fill. Flat at the close. One shot.
- ``scale``: the bounce5 entry. Sell half at 2% and the rest at 5%.
  2% is about six times the 0.30% maker round trip, so the first sale
  clears that cost. Flat the remainder at the close.
- ``shallow_rung``: the strict grid's 0.35 rung only. The 0.70 rung is
  the buy that catches a breakdown. Notional stays half the book.

The seventh replay does not move that rung. The daily flatten is what
gives a maker round trip back, a stop was already worse, and the printed
low is not the only reference. Locked before the run:

- ``scratch``: the bounce5 entry and 5% sell. Once 64 bars have closed
  (two-thirds of a 96-bar day) that sell is replaced by a maker sell at
  the fill. A session that never reaches bar 64 does not scratch. Still
  flat at the close if the scratch does not print.
- ``vwap``: after four completed bars, buy 1% under their volume-weighted
  typical price and sell that same price. 1% is about three times the
  0.30% maker round trip. The bar being traded is not in the average.
  One full-book shot. Flat at the close.
- ``carry``: the strict two-rung grid, and inventory is not sold at the
  close. The paired sell stays working on later sessions. No new name
  while a lot is open. Leftovers are sold at the last close of the window.

The eighth replay keeps those distances and changes which dip is taken.
Locked before the run:

- ``mid_exit``: buy the 0.35 rung and sell the prior close, not the
  upper rung. Half the book. The far side of the range is a breakaway,
  so the exit is the reference price that already traded.
- ``second_day``: bounce5 only when the prior session's low is below
  the low of the session before it. The first down day is the break.
  The buy is the next session.

A cell is convincing only when the full path, the path before 2026, and
2026 are all positive, the full path has at least 40 completed sells, the
largest day is under half of that profit when that day is a gain, and the
full-path drawdown is smaller than the full-path return. Otherwise it is
not a strategy. Nothing is wired into the live book.
Book €20,000, compounding, long only. Each window restarts at €20,000.
"""

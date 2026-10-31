"""Search for a book that beats the live residual pack by a locked margin.

Families, each fixed before its own tape run:

* scale-in: half the sleeve first, add the rest only after the close is
  through an arm and the name is still the residual leader
* runner trail: the 10% trail stays until the peak is well above entry,
  then the trail widens
* weekday trail: the price trail fires only on one weekday; the lock
  still checks every close
* trail confirm: the trail stays at 10%. A full sell needs two or three
  consecutive closes through that level. One cell banks half on the
  first close. The lock still sells on a single close.
* trail touch: the 10% level is known from the prior peak. If that day's
  low trades it, sell there (a gap fills at the open). One cell does this
  for the trail, one for the lock, one for both. The close rule still
  runs when the low does not reach the level.
* beta rank: same qualifiers, but the score subtracts beta times the BTC
  move instead of assuming beta is 1. Beta is estimated on 20, 40 or 60
  daily log returns.
* IR rank: the live score divided by the volatility of the alt-minus-BTC
  daily residual, over 10, 20 or 40 days.
* rearm: after a lock, or after a close that is still above the entry,
  open the next leader before the seven-day clock. A losing exit waits
  the full week. One cell waits one day instead of buying the next open.
* BTC tide: a new name is opened only when BTC's own lookback return is
  at least 0, 2 or 5 percent. A name already held is left to the trail.
* green pause: after a close that is still above the entry, the next
  entry waits 14, 21 or 28 days. A losing exit keeps the seven-day clock.
* liquid universe: the same book, but the sleeve is the 12, 16 or 24
  most-traded names that day on the whole venue, each with 60 days of history.
* BTC veto: the live book cashes a qualifying alt when BTC closes under
  its SMA50. These cells require a 5% or 10% break, or they drop the veto.
  The alt's own SMA50 stays.
* rank slot: among names that clear the floor, buy the second score, the
  third, or the smallest raw excess. An empty slot stays in cash.
* red cut: sell only while the close is still under the entry, after 2, 4
  or 6 days. A green position keeps the live trail and lock.
* red leader: the same 2, 4 and 6 day grid, but the sell is dropped while
  the name is still the residual leader. A red leader stays on the trail.
* gap cap: cancel an alt buy when the next open is more than 3, 5 or 8
  percent above the signal close. Sells still fill, and the book waits
  for the clock it already set.
* lead exit: no day count. Sell when the held name is no longer the top
  qualifier, when that is also a red close, or when its excess is no
  longer above the floor. The trail and the lock still run.

A family is a find only when ``bar.judge_family`` says so. Nothing in
this package is wired into the live clip.
"""

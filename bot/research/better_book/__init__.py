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

A family is a find only when ``bar.judge_family`` says so. Nothing in
this package is wired into the live clip.
"""

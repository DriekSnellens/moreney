"""Day and week horizon lab.

The armed residual pack is a multi-week trend book: one name, a 7-day clock,
a 10% trail. Minute scalps on the same tape lose to taker fees. This lab
measures the two horizons in between.

* Week — on the clock, recycle a name that never closed through the runner
  arm, and leave a name that did on the trail.
* Day — one UTC session, flat at the next open, unless the close itself
  cleared the runner arm and the spec graduates that lot onto the trail.
* Bank — 4h momentum that is still rising. A target or a stop fills inside
  the bar; a hold sells only once that rise is no longer positive.
"""

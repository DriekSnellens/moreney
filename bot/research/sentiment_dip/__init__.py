"""News sentiment decides which dip is allowed. Nothing here is armed.

The order is the locked bounce: 1% above the low already printed today,
sold 5% above the fill, flat at the session close. AlphaI's live
daily-recommendation scorer supplies the tone. A name is a buy when its
net score is at least 1 and the bullish headlines are at least as many
as the bearish ones. A name is not a buy when the score is -4 or worse,
or when it has bearish headlines, no bullish headline, and a score at or
below zero. The watch-list fill that promotes weaker names up to eight
buys is not used.

The window is the seven UTC publication days before the session, not
including the session day. Stories that print during the session are
not an input. Relevance below 6 is ignored, which is the scorer's own
floor. The archive on disk is already at that floor and starts on
2026-08-05, so there is no path before 2026.

Cells, locked before the replay:

- ``tone``: bounce only on the choppiest desk name that sentiment marks
  as a buy. No buy name means cash.
- ``block``: the usual choppiest name, and cash when sentiment marks
  that name as not a buy. A name with no headline is still allowed.
- ``plain``: the same bounce with no sentiment. The control.

``rank`` was locked after those three cells were read, and before it was
replayed. The bounce, the seven-day window, and the buy and avoid scores
stay as above. Among names that are buys and that clear the chop,
liquidity, and capacity gates, take the highest net score. An equal
score breaks alphabetically. Cash when no buy clears the gates.

Book €20,000, compounding, Bitvavo maker and taker as in the swing lab.
One name. Nothing is wired into the live book.
"""

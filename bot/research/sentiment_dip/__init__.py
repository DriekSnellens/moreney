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

The percent grid was locked after that reading, and before its own replay.
Bids sit 0%, 1%, 2%, or 4% above the printed low. Offers sit 3%, 5%, 8%,
or 12% above the fill. The 1% / 5% pair is the control. Fees, the one-shot
rule, the same-bar refusal, and the close flatten stay as they are.
``tone``, ``block``, and ``plain`` are crossed with the grid on the news
overlap only. ``rank`` is not crossed with it. The same sixteen pairs
also run on the full tape with no sentiment, so both periods can be
judged. A pair is not a strategy unless the full path, the path before
2026, and 2026 are all positive, the full path has at least 40 maker
exits, a positive largest day is under half the full profit, and the
full drawdown is smaller than the full return. The best cell is not
refit. Nothing is armed.
"""

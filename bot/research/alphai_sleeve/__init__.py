"""A €10,000 sleeve held in several names at once. Nothing here is armed.

AlphaI's sentiment-summary endpoint counts bullish, neutral and bearish
news over seven UTC days, today included. The docs call that a news
count. The radar is a different endpoint and is not used: AlphaI says
those scores describe news activity, not a price forecast, and that a
snapshot is not a point-in-time backtest.

This replay counts the archive the same way, with two causal cuts. The
session day is not an input, because the live rollup's today-bucket is
still incomplete at the open. Insider rows are left out, which is what
the rollup does. Relevance below 6 is absent from the file on disk, so
the count is stricter than the public news feed's default of 4.

A name is eligible when the prior seven publication days contain at
least one bullish headline and more bullish headlines than bearish
ones. Neutral is not a buy. Missing news is not a buy. Rank is the
bullish-minus-bearish count, then the base alphabetically.

The sleeve starts at €10,000 and compounds. ``four`` holds at most four
names, equal weight, so each slice is a smaller trade. ``all`` holds
every eligible name, equal weight. ``chop`` and ``book`` ignore the
news. ``chop`` holds the four jumpiest names that clear the same
liquidity gate. ``book`` holds every name that clears it.

Orders go in at the session open and, on the last day, out at the
close. Both sides pay the Bitvavo taker and the square-root impact.
A name that stays selected is resized to its share. A name that leaves
is sold. A slice above 10% of median quote volume is clipped. The
median is the five prior usable sessions, which must be no more than
two days old and at least €100,000.

The archive starts on 2026-08-05. AlphaI's own coverage note says the
collectors expanded in June 2026 and that earlier months are sparse, so
there is no path before 2026 for the news cells. ``chop`` and ``book``
are also run on the full tape, because they do not need the archive.
Nothing is wired into the live book.

The alt-market run was locked after that desk reading, and before its
own replay. The universe is every Bitvavo market quoted in EUR and
marked trading, except BTC and the cash pegs EURC, EURCV, EUROP, USDC,
USDCV, USDT, DAI, FRAX, TUSD, USDE, FDUSD, PYUSD, USD and EUR. The tape
is the daily candle, not the fifteen-minute desk cache. A listed name
needs a five-day median quote of at least €10,000. A liquid name uses
the desk floor of €100,000, on that same wide universe. Capacity, the
seven-day count, the four-name cap, and the taker costs stay as above.
The desk names are repeated on the daily tape as the control. Nothing
is armed.
"""

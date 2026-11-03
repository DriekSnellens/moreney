"""The AlphaI radar decides the buy. Nothing here is armed.

``GET /api/signals/snapshot/`` is a live snapshot. AlphaI documents it as
news activity, not a price forecast, and not a point-in-time backtest:
later corrections can change the baseline. This lab does not replay that
endpoint over 2024.

The wet book below uses the archived crypto wire (relevance at least 6;
the public news default is 4, so these scores are not AlphaI's ``news_z``).
A story that names more than five tickers is dropped. The signal day is
the UTC publication day. The baseline is the prior 30 calendar days and
does not include the signal day. A name needs five baseline stories on
three different days. Sentiment is withheld unless the signal day has two
labeled stories covering at least half of that name's stories. The tone
change is the signal-day mean minus the baseline mean. Neutral labels are
missing, not zero.

A buy requires ``news_z`` of at least 1 and a tone change of at least
+0.15, which is AlphaI's published positive band. A tone change of -0.15
or worse is not a buy. A missing tone is not a buy. ``spike`` also
requires a Poisson tail under 0.001. ``hot_any`` ignores tone, so the
difference from ``hot`` is the don't-buy. ``chop`` ignores the wire and
buys the jumpiest name. One name, the whole €20,000 book, filled at the
next session's open, held while the same name stays selected. Bitvavo
taker 0.25% plus impact ``0.02 * sqrt(notional / median quote)``, capped
at 3%, on the way in and the way out. A name whose book would exceed 10%
of its median quote stays unbought.

The archive starts on 2026-08-05, so the first decision is thirty days
later. There is no path before 2026. The live snapshot, when the key is
present, is printed as today's decision only.
"""

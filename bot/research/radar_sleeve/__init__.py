"""Small fast sleeve on a point-in-time news-spike proxy.

AlphaI's radar (``/api/signals/snapshot/``) is a live snapshot. Its payload
says ``point_in_time`` is false and the current values are not historical
versions, so this lab does not replay that endpoint.

The proxy below was locked before the wet replay. It reads the archived
crypto stories already on disk (relevance at least 6; the live radar uses 4)
and builds a market-adjusted 24h rate against the prior 30 calendar days.
A story that names more than five tickers is dropped. A name needs five
baseline stories on three different days before it can score. Sentiment
delta is the change in bullish-minus-bearish tone, and it is withheld unless
the signal day has two labeled stories covering at least half of that name's
stories. The sleeve is 15% of a €20,000 book, one name, highest score then
alphabetical, held while it stays selected, filled at the next open. It does
not consult the residual rank or the BTC average. The other 85% stays on the
live residual book. Idle sleeve cash earns nothing.

Cells, also locked before the run: ``spike`` (Poisson tail under 0.001 and
a non-negative sentiment delta), ``hot`` (normal z of that tail at least 1
and a non-negative sentiment delta), ``hot_any`` (the same z, sentiment
ignored). Nothing here is armed. The archive does not reach before 2026.
"""

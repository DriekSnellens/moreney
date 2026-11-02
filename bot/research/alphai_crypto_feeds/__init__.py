"""Record unused AlphaI crypto feeds and measure archived news on residual_full.

Radar (``/api/signals/snapshot/``) and the seven-day sentiment summary are
point-in-time snapshots. AlphaI documents the radar as news activity, not a
price forecast and not a point-in-time backtest, so those feeds are appended
to a forward ledger only.

The wet replay uses archived ``/api/news/`` rows (category ``crypto``) and the
same scorer as the live daily recommendations. A find is not armed here. The
live residual book is unchanged.
"""

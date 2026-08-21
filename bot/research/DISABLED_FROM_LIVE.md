# Research package — disconnected from live

This tree is **not** on the live Bitvavo micro hot path.

Live production flags live in `bot/live/production_flags.py`.
Session setting `live_disable_research_hooks=True` skips CVD inject and shadow observer.

Do not re-wire research into live order placement without a LIMITED_LIVE review.

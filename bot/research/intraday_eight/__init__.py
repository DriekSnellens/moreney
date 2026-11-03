"""Eight intraday books. Nothing here is armed.

Bars are 15 minutes. A signal uses that bar's close. The fill is the
next bar's open. Longs pay the Bitvavo taker and the square-root
impact. Shorts are not on the Bitvavo spot book; a second book prices
them as a perp at 0.05% fee and 0.05% slip per side. There is no open
interest in the cache.

The session is the UTC day, because these markets do not close.
Parameters below were locked before the replay. A book is better than
live only when 2024, 2025 and 2026 are each at least the live year,
the full path is higher, and the drawdown is not deeper.
"""

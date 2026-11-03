"""Eight intraday books. Nothing here is armed.

Bars are 15 minutes. The session is the UTC day, because these markets
do not close. VWAP and the EMA pullback rest a limit at the level known
at the prior close: the fill is that limit (or the open, when the open
is already through it) and pays the Bitvavo maker. A stop or a cross
pays the taker and the square-root impact. The sweep uses the prior
UTC day's high and low. Opening-range, squeeze, RSI/MACD and Supertrend
enter on the next open, because those rules are a close confirmation.

Shorts are not on the Bitvavo spot book. A short is priced as a perp
at 0.05% fee and 0.05% slip per side. There is no open interest in
the cache. Funding cashflow is on the perp book only.

Parameters were locked before the replay. A book is better than live
only when 2024, 2025 and 2026 are each at least the live year, the
full path is higher, and the drawdown is not deeper.
"""

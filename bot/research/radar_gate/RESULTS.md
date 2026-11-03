# Radar buy gate

Rules are the module docstring in `__init__.py`. They were locked before
this replay. The wet path reads the archived crypto wire, not
`/api/signals/snapshot/`. That endpoint is a live snapshot and AlphaI
does not offer it as a point-in-time backtest. Archive relevance is at
least 6; the public news default is 4, so these z-scores are not the
live radar's `news_z`.

Book €20,000. One name. Next-open fills, taker 0.25% plus impact.
Archive days with stories: 90. First story 2026-08-05.
Last story 2026-11-02. Replay 2026-09-05 → 2026-11-02.

| cell | pnl | max DD | days held | buys | trips | largest day | names |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| hot | -€4,137 | 23.1% | 19 | 17 | 17 | +€1,645 | ADA 6, XRP 5, DOGE 3, NEAR 3, SOL 1, UNI 1 |
| spike | -€1,554 | 7.8% | 1 | 1 | 1 | -€1,564 | DOGE 1 |
| hot_any | -€8,687 | 46.5% | 50 | 38 | 38 | -€1,821 | NEAR 10, ADA 8, UNI 8, ETH 6, SOL 5, DOGE 4 |
| chop | +€9,774 | 40.6% | 57 | 14 | 14 | +€6,698 | NEAR 24, FET 16, ARB 10, UNI 6, OP 2, DOT 1 |

## Reading

`hot` is the radar rule. It finishes -€4,137.
Completed round trips pay the taker both ways. Cash earns nothing
while no name clears the tone bar. `hot_any` buys unusual activity
even when the tone is missing or negative, which is the don't-buy
turned off. `spike` also requires a Poisson tail under 0.001.
`chop` buys the jumpiest desk name and ignores the wire.
There is no path before 2026 in this archive, so this cannot clear
a two-period bar. Nothing is armed.

## Live radar

Live snapshot as of 2026-11-03T12:33:52Z. point_in_time=False.
A buy needs news_z of at least 1 and a tone change of at least +0.15,
and the name has to be in the desk universe. Anything else is not a buy.
The book would buy `cash`.

| name | news_z | tone change | decision |
| --- | ---: | ---: | --- |
| ETH | 1.56 | -0.24 | do not buy |
| NEAR | 1.05 | -0.10 | do not buy |
| SOL | 0.71 | +0.56 | do not buy |
| XRP | 0.52 | +0.46 | do not buy |
| BNB | 0.40 | -0.36 | outside the book |
| ONDO | 0.40 | — | outside the book |
| ARB | 0.23 | — | do not buy |
| BTC | 0.12 | -0.18 | outside the book |
| AAVE | 0.00 | — | outside the book |
| ADA | 0.00 | — | do not buy |
| HYPE | 0.00 | -0.22 | outside the book |
| LINK | 0.00 | +0.52 | do not buy |

Reproduce: `python -m bot.research.radar_gate`

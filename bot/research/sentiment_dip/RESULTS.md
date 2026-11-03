# Sentiment dip

Rules are the module docstring in `__init__.py`. `tone`, `block`, and
`plain` were locked before their first replay. `rank` was locked after
that reading and before its own. The order is the 1% bid above today's
printed low, sold 5% higher. Sentiment is AlphaI's live score on the
seven publication days before the session. Archive 2026-08-05 → 2026-11-02.
Replay 2026-08-12 → 2026-11-02. Book €20,000.
The archive does not reach before 2026.

| cell | pnl | max DD | days selected | days filled | maker exits | flattens | maker pnl | flatten pnl | largest day | names |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| tone | -€2,568 | 39.0% | 79 | 72 | 27 | 45 | +€26,813 | -€29,382 | -€3,371 | NEAR 40, UNI 18, XRP 9, ADA 7, ARB 3, LINK 1 |
| block | +€485 | 31.8% | 73 | 69 | 27 | 42 | +€25,575 | -€25,090 | -€2,725 | FET 25, NEAR 24, ARB 10, UNI 9, XRP 2, OP 2 |
| plain | -€1,649 | 34.7% | 83 | 77 | 29 | 48 | +€25,309 | -€26,959 | -€2,546 | FET 25, NEAR 25, UNI 10, ARB 10, ADA 7, XRP 2 |
| rank | -€11,348 | 56.7% | 79 | 71 | 9 | 62 | +€5,564 | -€16,912 | -€1,830 | SOL 26, NEAR 19, ADA 11, ETH 8, UNI 6, XRP 6 |

## Reading

`tone` buys the dip only on a name sentiment marks as a buy. It finishes -€2,568.
`block` skips the choppiest name when sentiment says it is not a buy. It finishes +€485.
`plain` is the same bounce with the wire ignored. It finishes -€1,649.
`rank` keeps the buy rule and takes the highest score instead of the choppest name. It finishes -€11,348.
A missing headline is not a buy under `tone` or `rank`. It is still allowed under `block`.
There is no path before 2026, so this overlap cannot clear a two-period bar.
No cell clears even that one-window check.
Nothing is armed.

Reproduce: `python -m bot.research.sentiment_dip`

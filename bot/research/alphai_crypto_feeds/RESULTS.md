# AlphaI crypto feeds — residual overlap

Archive `2026-08-05` → `2026-11-02`, 4360 crypto stories, complete `True`.
Published `2026-08-05T05:13:00Z` → `2026-11-02T11:20:48Z`. Replay `2026-08-05` → `2026-11-01`, book €20,000, wet next-open, live residual knobs.

Radar and the seven-day sentiment summary are snapshots. They are recorded
on the forward ledger and are not in this replay. AlphaI's own docs say the
radar is not a point-in-time backtest. The news archive does not reach
before 2026, so this overlap cannot clear the two-period bar.

A day with no archived story is left on the residual rank. A covered day
with no buy name goes to cash under gate, intersect and prefer. Avoid
blocks only names the live scorer marks avoid. Nothing is armed.

## Residual, no AlphaI

- off: pnl +18089  dd  31.7%  trades  14  hold cash

## Archived news on the same window

| Cell | PnL | maxDD | trades | covered days | empty buy days |
|---|---:|---:|---:|---:|---:|
| w1_gate | +8230 | 14.2% | 8 | 89 | 6 |
| w1_intersect | -966 | 19.1% | 12 | 89 | 6 |
| w1_prefer | -927 | 27.0% | 14 | 89 | 6 |
| w1_avoid | +18089 | 31.7% | 14 | 89 | 0 |
| w3_gate | +4739 | 16.1% | 10 | 89 | 4 |
| w3_intersect | +5366 | 19.8% | 12 | 89 | 4 |
| w3_prefer | +2880 | 27.7% | 14 | 89 | 4 |
| w3_avoid | +18089 | 31.7% | 14 | 89 | 0 |
| w7_gate | +643 | 16.0% | 8 | 89 | 0 |
| w7_intersect | +3321 | 26.4% | 14 | 89 | 0 |
| w7_prefer | +3321 | 26.4% | 14 | 89 | 0 |
| w7_avoid | +11822 | 31.6% | 12 | 89 | 0 |

## Reading

Ungated residual on this overlap finished +18089. The highest cell that changes a fill is w7_avoid at +11822 (drawdown 31.6%). It does not beat the ungated book. Rules are the live daily-recommendation scorer over the trailing 1, 3 and 7 publication days, locked before the replay. The Basic plan keeps 90 days and refuses the boundary date, so 2024–2025 is absent and the two-period bar is not in play. Nothing is armed.

## Forward snapshot

Radar rows: 24h=26, 4h=10. Sentiment tickers: 17. Errors: 0.

Reproduce:

```bash
.venv/bin/python -m bot.research.alphai_crypto_feeds
```

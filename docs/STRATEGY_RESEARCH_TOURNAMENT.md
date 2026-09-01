# Strategy Research Tournament

## 1. Why the old maker strategy failed

Maker inventory showed positive gross opportunities but realized NET was destroyed by adverse selection after trade-through fills, especially on Bitvavo→Bitvavo routes that were early-stopped after large losses. Gross spread ≠ executable edge.

## 2. Why positive spread ≠ positive NET

Fees, slippage, latency buffers, transfer/rebalance, and adverse markout sit between mid/spread and cash. The tournament always subtracts shared retail taker round-trip costs before any candidate can proceed.

## 3. Why trade-through adverse selection matters

Conservative execution assumes trade-through fills (no queue priority fantasy). Historical paper evidence: adverse often exceeds the thin NET margin. Execution replay therefore adds fill-rate haircuts and extra adverse — never queue fills.

## 4. Why strategies compete under identical rules

Every family sees the same research tape, chronological DEV/FREEZE/OOS split, fee table, adverse assumptions, sample floors, and verdict vocabulary. No private optimistic economics.

## 5. The five strategy families

| ID | Question |
|----|----------|
| `lead_lag` | Does venue A predict venue B before B moves? |
| `cross_venue_dislocation` | Do synchronized dislocations predictably converge? |
| `short_horizon_mean_reversion` | Do deviations from causal cross-venue fair value revert? |
| `order_book_imbalance` | Does L1 imbalance predict short-horizon direction? |
| `short_horizon_momentum` | Does recent move continue? |

Research-only. Execution disabled.

## 6. Gate sequence

```
DATA → SIGNAL → DEVELOPMENT FIT → FREEZE → UNTOUCHED OOS
  → NET ECONOMICS → EXECUTION REPLAY → STABILITY → SCORE
```

Fail-closed at each gate. Failed gates cannot become `PAPER_CANDIDATE`.

## 7. Causal OOS

Centralized `chronological_split` (60% DEV / 10% FREEZE / 30% OOS). No shuffle. Parameters selected only on DEVELOPMENT, frozen with fingerprint, evaluated once on OOS.

## 8. Economics

Shared `net_waterfall_from_edge` using `venue_taker_fee` + `expected_waterfall`. Waterfall:

GROSS − FEES − SLIPPAGE − ADVERSE − LATENCY = EXPECTED NET

## 9. Execution replay

Conservative fill rate + extra adverse. No invented books, no queue fills, no relaxed trade-through.

## 10–11. PAPER_CANDIDATE meaning

Survived all gates under research assumptions. **Not** proven live profitable. Label: `RESEARCH CANDIDATE — NOT PROVEN LIVE PROFITABLE`.

## 12. How to run

```bash
python -m bot.research.tournament.runner
# optional: --path data/research_marketdata --readiness data/market_data_research_report.json
```

Requires prior market-data acceptance report for horizon readiness.

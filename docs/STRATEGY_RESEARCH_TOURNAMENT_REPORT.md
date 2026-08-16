# Strategy Research Tournament — Report

**Package:** `bot.research.tournament`  
**Command:** `python -m bot.research.tournament.runner`  
**Execution:** disabled  
**Claim:** ALL STRATEGIES REJECTED (valid research outcome — no PAPER_CANDIDATE)

---

## A. Existing architecture reused

| Component | Reuse |
|-----------|--------|
| Research tape JSONL | `data/research_marketdata` |
| Tape scan / fingerprint | `bot.market_data.research.tape_scan` |
| Chronological split | `bot.market_data.research.chrono_split` |
| Horizon readiness | `data/market_data_research_report.json` |
| Venue fees | `bot.core.venue_fees.venue_taker_fee` |
| Waterfall | `bot.opportunity.waterfall.expected_waterfall` |
| Dual clocks / Bitvavo null ts | existing research schema (unchanged) |

Not duplicated: Net accounting production path, PaperExecutor fills, maker strategy.

Architecture:

```
RESEARCH TAPE
  -> DATA ACCEPTANCE / HORIZON READINESS
  -> SHARED TAPE INDEX (EUR × binance/bitvavo/okx)
  -> ROBUST CHRONOLOGICAL SPLIT (DEV / FREEZE / OOS)
  -> STRATEGY CANDIDATES (5 families)
  -> SIGNAL → FIT(DEV) → FREEZE → OOS
  -> NET ECONOMICS → EXECUTION REPLAY → STABILITY
  -> TOURNAMENT SCOREBOARD + REGISTRY
```

## B. Strategy interface

`StrategyResearchCandidate` / `GatedFamily` lifecycle — identical gates for every family. No future leakage; OOS cannot mutate frozen parameters.

## C. Dataset used

| Field | Value |
|-------|-------|
| DATASET_ID | `mdresearch-research_md_v1-30b67335eda31bd1` |
| Duration | ~22582 s (~6.3 h) |
| Indexed points | ~245k L1 mids (EUR, core venues) |
| Label | OBSERVED real tape (not synthetic) |

## D. Data readiness

From market-data acceptance: slow horizons READY_WITH_CAUTION; 50/100/250ms NOT_READY.  
Lead-lag requested 100/250 → explicitly unsupported (not substituted).

## E. Chronological split

Robust percentile bounds (reject stale outlier timestamps that previously emptied DEVELOPMENT):

- DEVELOPMENT: ~60%
- FREEZE_BOUNDARY: ~10%
- UNTOUCHED_OOS: ~30%
- shuffled=false, zero overlap

## F. Development findings

| Strategy | Dev signals | Dev effect (mean fwd) |
|----------|------------:|----------------------:|
| lead_lag | 7285 | predictive on DEV |
| cross_venue_dislocation | 1966 | predictive on DEV |
| short_horizon_mean_reversion | 4076 | predictive on DEV |
| order_book_imbalance | 7846 | predictive on DEV |
| short_horizon_momentum | 889 | predictive on DEV |

## G. Frozen experiment specifications

Immutable per-family params recorded (horizon, thresholds, venues). Registry: `data/research_tournament/registry.jsonl`.

## H. Untouched OOS findings

| Strategy | OOS class | Outcome |
|----------|-----------|---------|
| lead_lag | REVERSED | OOS_FAILED |
| short_horizon_momentum | CONSISTENT but CI fails signal gate | OOS_FAILED |
| others | CONSISTENT | proceeded to later gates |

## I. NET economics

Shared retail taker round-trip + adverse 8 bps + slippage 2 bps + latency 2 bps.

| Strategy | Expected NET |
|----------|-------------:|
| order_book_imbalance | **< 0** → COST_NEGATIVE |
| cross_venue_dislocation | ~+2.88 EUR / €100 notional (research edge) |
| short_horizon_mean_reversion | ~+2.06 EUR / €100 notional |

## J. Execution replay

Conservative fill_rate=0.55 + extra adverse. No queue fills.

Survivors of economics remained positive on shadow replay before stability gate.

## K. Stability

`cross_venue_dislocation` and `short_horizon_mean_reversion` marked **CONCENTRATED_RESULT** (top symbol/route share > 70%) → UNSTABLE.

## L. Tournament scoreboard

| STRATEGY | VERDICT | FAILED_GATE | DEV_SIGNALS | OOS_SIGNALS | EXPECTED_NET | EXECUTION_NET |
|----------|---------|-------------|------------:|------------:|-------------:|--------------:|
| lead_lag | OOS_FAILED | OOS | 7285 | 4649 | — | — |
| cross_venue_dislocation | UNSTABLE | STABILITY | 1966 | 1951 | 2.88 | 1.56 |
| short_horizon_mean_reversion | UNSTABLE | STABILITY | 4076 | 4596 | 2.06 | 1.11 |
| order_book_imbalance | COST_NEGATIVE | ECONOMICS | 7846 | 7109 | -0.39 | — |
| short_horizon_momentum | OOS_FAILED | OOS | 889 | 343 | — | — |

Tournament promotion score = 0 for all (no PAPER_CANDIDATE).

## M. Rejected strategies and exact gate

See scoreboard. All five rejected.

## N. Any PAPER_CANDIDATE

**NONE**

## O. Remaining blockers

1. Fast horizons still NOT_READY (Bitvavo exchange_ts).
2. Surviving slow edges are concentrated / cost-negative after shared fees.
3. Do not enable execution; do not tune thresholds to manufacture a candidate.
4. Collect longer multi-regime tape; re-run same criteria version.

## P. Performance

| Metric | Value |
|--------|------:|
| Tape load | ~179 s |
| Full tournament | ~255 s |
| Index throughput | ~1375 points/s |
| Peak memory | ~96 MB |
| Per-strategy | ~10–19 s |

## Q. Regression verification

- Paper executor / maker / opportunity economics: no tournament imports.
- Fees/fills/live-equivalent PnL unchanged.
- `LEAD_LAG_EXECUTION_ENABLED` remains false.
- Synthetic fixtures used only for mechanics tests — not alpha claims.

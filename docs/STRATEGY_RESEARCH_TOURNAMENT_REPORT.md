# Strategy Research Tournament — Report

**Package:** `bot.research.tournament`  
**Command:** `python -m bot.research.tournament.runner`  
**Execution:** disabled  
**Claim:** ALL STRATEGIES REJECTED (valid research outcome — no PAPER_CANDIDATE)

**Rerun:** OBSERVED full tape after market-data refresh (`cursor/observed-tape-tournament-rerun-c05a`). Same criteria version `research_tournament_v1`. No threshold tuning after OOS.

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

| Field | Prior run | This rerun |
|-------|-----------|------------|
| DATASET_ID | `mdresearch-research_md_v1-30b67335eda31bd1` | `mdresearch-research_md_v1-52186beeca2c407c` |
| Duration | ~22582 s (~6.3 h) | ~37553 s (~10.4 h) |
| Indexed points | ~245k L1 mids | **832924** L1 mids (EUR, core venues) |
| Acceptance companion | (older) | `mdresearch-research_md_v1-27116902be243a23` (~7.73M events; tape still growing during runs) |
| Label | OBSERVED | **OBSERVED** (not synthetic) |

Artifacts: `data/research_tournament/mdresearch-research_md_v1-52186beeca2c407c/`.

## D. Data readiness

From refreshed market-data acceptance (`DATA_READY_FOR_SLOW_HORIZONS`):

- Slow horizons (500/1000/2000/5000 ms): `READY_WITH_CAUTION`
- Fast horizons (50/100/250 ms): `NOT_READY` (Bitvavo null `exchange_ts`)
- Lead-lag fast horizons remain explicitly unsupported (not substituted)

## E. Chronological split

Robust percentile bounds (unchanged method):

- DEVELOPMENT: ~60%
- FREEZE_BOUNDARY: ~10%
- UNTOUCHED_OOS: ~30%
- shuffled=false, zero overlap

## F. Development findings

| Strategy | Dev signals | Dev gate |
|----------|------------:|----------|
| cross_venue_dislocation | 1817 | predictive on DEV |
| lead_lag | 3166 | **NO_SIGNAL** (no predictive separation) |
| order_book_imbalance | 9463 | predictive on DEV |
| short_horizon_mean_reversion | 3618 | predictive on DEV |
| short_horizon_momentum | 2231 | predictive on DEV |

## G. Frozen experiment specifications

Immutable per-family params recorded (horizon, thresholds, venues). Registry append: `data/research_tournament/registry.jsonl`. Criteria version unchanged: `research_tournament_v1`.

## H. Untouched OOS findings

| Strategy | OOS class / note | Outcome |
|----------|------------------|---------|
| lead_lag | failed earlier at SIGNAL | NO_SIGNAL |
| order_book_imbalance | WEAKENED; oos_signal_gate_failed | OOS_FAILED |
| short_horizon_momentum | REVERSED; oos_signal_gate_failed | OOS_FAILED |
| cross_venue_dislocation | proceeded past OOS | later STABILITY |
| short_horizon_mean_reversion | proceeded past OOS | later STABILITY |

## I. NET economics

Shared retail taker round-trip + adverse 8 bps + slippage 2 bps + latency 2 bps (unchanged).

| Strategy | Expected NET (€ / €100 notional research) |
|----------|------------------------------------------:|
| cross_venue_dislocation | ~+2.97 |
| short_horizon_mean_reversion | ~+1.82 |
| others | failed before economics or N/A |

## J. Execution replay

Conservative fill_rate=0.55 + extra adverse. No queue fills. `trade_through_baseline=True`.

| Strategy | Execution NET |
|----------|-------------:|
| cross_venue_dislocation | ~+1.61 |
| short_horizon_mean_reversion | ~+0.98 |

## K. Stability

Both economics survivors marked **CONCENTRATED_RESULT** (top symbol/route share > 70%) → **UNSTABLE**.

## L. Tournament scoreboard (this rerun)

| STRATEGY | VERDICT | FAILED_GATE | DEV_SIGNALS | OOS_SIGNALS | EXPECTED_NET | EXECUTION_NET |
|----------|---------|-------------|------------:|------------:|-------------:|--------------:|
| cross_venue_dislocation | UNSTABLE | STABILITY | 1817 | 1667 | 2.97 | 1.61 |
| short_horizon_mean_reversion | UNSTABLE | STABILITY | 3618 | 3323 | 1.82 | 0.98 |
| order_book_imbalance | OOS_FAILED | OOS | 9463 | 10398 | — | — |
| short_horizon_momentum | OOS_FAILED | OOS | 2231 | 3722 | — | — |
| lead_lag | NO_SIGNAL | SIGNAL | 3166 | 0 | — | — |

Tournament promotion score = 0 for all (no PAPER_CANDIDATE).

### Comparison vs prior (~6.3 h tape)

- Longer tape did **not** produce a PAPER_CANDIDATE.
- `lead_lag` degraded from OOS_FAILED → **NO_SIGNAL** on DEV (honest; not rescued).
- `order_book_imbalance` no longer cost-negative first; fails OOS signal gate instead.
- Concentration still kills the only positive NET survivors.

## M. Rejected strategies and exact gate

See scoreboard. All five rejected under frozen criteria.

## N. Any PAPER_CANDIDATE

**NONE**

## O. Remaining blockers

1. Fast horizons still NOT_READY (Bitvavo exchange_ts).
2. Surviving slow edges remain concentrated after shared fees + trade-through replay.
3. Do not enable execution; do not tune thresholds to manufacture a candidate.
4. Multi-regime / longer tape still welcome — re-run **same** criteria only.

## P. Performance

| Metric | Value |
|--------|------:|
| Tape load | ~618 s |
| Full tournament | ~771 s |
| Index throughput | ~1347 points/s |
| Points indexed | 832924 |
| Peak memory | ~301 MB |

## Q. Regression verification

- Paper executor / maker / opportunity economics: no tournament imports into live path.
- Fees/fills/live-equivalent PnL unchanged.
- `LEAD_LAG_EXECUTION_ENABLED` remains false.
- Synthetic fixtures used only for mechanics tests — not alpha claims.

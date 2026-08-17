# Regime Hypothesis Lab (H-0005 / H-0007)

Independent research strategies. **Parents remain REJECTED.** Execution stays **OFF**.

These are hypotheses, not proven alpha. Forensic NET is never strategy profitability.

## Frozen protocol (`regime_lab_v1`)

Freeze before inspecting results. Changing protocol constants creates a **new hypothesis version**.

| Item | Value |
|---|---|
| Forensic / DISCOVERY tape | `ts <= FORENSIC_OOS_END_NS` |
| Fresh labeled tape | `ts > FORENSIC_OOS_END_NS` |
| Split | DEV 60% / FREEZE 10% / OOS 30% chronological |
| H-0005 | `cross_venue_dislocation_freshness` — admit CVD iff pre-trade `quote_age_ms < 250` |
| H-0007 | `short_horizon_mean_reversion_wide_spread` — admit SHMR iff pre-trade `spread_bps >= 20` |
| Sparse density | recorded feature only; not an admission threshold |
| Cost model | unchanged tournament waterfall (fees, slippage, buffer, adverse, capital lock) |
| Stability | same 70% caps; `ROUTE_UNIVERSE_LIMITED` annotates one-route universes without relaxing the cap |
| Ranking | `CANDIDATE` — does not affect production |

Admission is `ADMITTED` / `REJECTED` / `UNSUPPORTED_DATA`. Missing freshness is never a silent signal. Bitvavo `exchange_ts` is never invented.

## Controls

A. unmodified parent  
B. regime-gated strategy  
C. no-trade baseline  
D. regime-only descriptive (unconditional forward in FRESH or WIDE)

## Run

```bash
python -m bot.research.regime_lab.runner --stride 4
```

Verdicts are mechanical (`OOS_PASS`, `OOS_FAIL`, `INSUFFICIENT_DATA`, `INSUFFICIENT_FRESH_DATA`, `UNSTABLE`, `COST_NEGATIVE`, `NO_SELECTIVE_EDGE`, `NON_PARTICIPATION_ONLY`, `UNSUPPORTED_DATA`). The LLM is advisory and post-hoc only.

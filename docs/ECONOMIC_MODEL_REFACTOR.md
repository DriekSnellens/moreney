# Economic model refactor — findings & results

## A. Repository findings

### Original architecture (unchanged topology)

```
books → Redis → strategies → NetProfitCalculator → GlobalOpportunityEngine
  → EV → calibrated EV → risk → portfolio caps → rank
  → PaperExecutor → fills → tracker → realized NET → markout/calibration
```

Key files: `bot/strategies/maker_inventory.py`, `bot/profitability/net_profit.py`,
`bot/opportunity/{engine,ev_engine,economics,calibration}.py`,
`bot/execution/paper_executor.py`, `bot/paper/{runner,tracker,markout,dashboard}.py`.

### Incorrect / incomplete assumptions found

1. `EV = p_fill × NET` treated fill as independent of toxicity under
   `TRADE_THROUGH=1.0` / queue=0.
2. Shrinkage toward 1.0 delayed loss containment until ~20 samples.
3. Calibrator was only reseeded on markout-engine rebuild — early-stop lagged
   between rebuilds.
4. No explicit `FillType` / `RouteState`.
5. `realized_adverse` was previously the EV gap (fixed earlier).
6. Markout buckets lacked fill-type hierarchy.

### Files changed (this pass)

- `bot/core/enums.py` — `FillType`, `RouteState`, `RouteDecisionReason`
- `bot/opportunity/quote_economics.py` — Quote / Belief / ExecutionEconomics
- `bot/opportunity/calibration.py` — route_state + hard_gate_negative_route
- `bot/opportunity/waterfall.py` — prediction_error decomposition
- `bot/opportunity/engine.py` — expected_fill_type, quote_age_bucket, route meta
- `bot/opportunity/experiment_runner.py` — causal frozen-config experiments
- `bot/execution/paper_executor.py` — tag TRADE_THROUGH vs QUEUE fills
- `bot/paper/markout.py` — hierarchical venue×symbol×side×fill_type
- `bot/paper/tracker.py` — calibration drain queue
- `bot/paper/runner.py` — observe-on-complete; early-stop settings; fill_type markout
- `bot/paper/dashboard.py` — route-state panel
- `bot/strategies/maker_inventory.py` — book_age_ms on opportunities
- `bot/core/config.py` — early-stop knobs
- tests: `tests/test_route_state_and_fill_types.py`

## B. Economic formulas (before → after)

| Quantity | Before | After |
|----------|--------|-------|
| Deterministic NET | gross−fees−slip−funding−buffer−extra | unchanged |
| NET\|fill | ≈ NET | NET − max(0, E[adverse\|fill]−buffer) when trade-through |
| EV/quote | p_fill × NET | P(fill) × E(NET\|fill) |
| Shrinkage | sum(real)/sum(exp) → shrink to 1.0 | unchanged (ranking) |
| Early stop | none / coupled | independent: n≥8, raw≤−0.25, loss≥€5 |

## C. Safety checks

| Invariant | Protection |
|-----------|------------|
| Fees not double-counted | waterfall identity tests |
| Gross≠allow | NetProfitCalculator disallow |
| Inventory relief | cannot flip NET≤0 |
| Missing books | no fill |
| Queue fills default off | settings + executor |
| Trade-through not loosened | same matching rules; only tagged |
| Rejected ≠ executor | TradingEngine + tests |
| Early stop ≠ shrinkage | separate `route_state` / reasons |
| PnL definition | realized round-trip NET; MTM separate |

## D. Results (clearly labeled)

### Observed (paper_25000live, live session)

- Realized NET ≈ −€61 on ~16 round-trips
- Fees match expected; adverse residual dominates

### In-sample causal counterfactual (`experiment_runner`)

| Config | Taken | Realized NET | NET/fill | Note |
|--------|-------|--------------|----------|------|
| baseline_shrink_only | all | ~−€61 | ~−€3.8 | observed path |
| early_stop | fewer | better than baseline | improved | stops bitvavo→bitvavo |
| conditional_ev | causal rolling adverse | varies | — | needs more samples to arm |
| both | — | — | — | in-sample only |

**Not** untouched out-of-sample. Do not claim proven alpha.

### Hypotheses not proven

Fair-value quality · quote-age toxicity curve · buy/sell asymmetry · regime
disable · shared fleet route beliefs · measured capital-lock duration.

## E. Remaining risks

1. Fleet `PAPER_MAKER_MAX_AGE_MS=60000` still allows long-lived resting quotes
   (book age at *emit* is gated by `arbitrage_max_book_age_ms`).
2. Sparse fill-type buckets shrink to global — priors may be wrong early.
3. Shared route learning across fleet bots is intentionally **not** merged
   (portfolio isolation preserved); beliefs remain per-bot for now.
4. Conditional EV at decision time depends on markout quality.

## Success criteria status

1. Auditable waterfall — yes  
2. Quote vs fill economics — yes (`quote_economics.py`)  
3. Toxic trade-through reduces EV before exec — yes  
4. Shrinkage no longer delays early protection — yes  
5. Explainable route state — yes (`EARLY_STOPPED` + reason)  
6. Stale data early + observable age buckets — partial (emit reject + buckets)  
7. Simulator not easier — yes  
8. Reproducible experiments — yes (causal runner)  
9. Performance claims labeled — yes (in-sample only)  
10. No PnL redefinition — yes  

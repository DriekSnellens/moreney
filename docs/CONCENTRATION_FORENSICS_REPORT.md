# Concentration Forensics Report

**Package:** CONCENTRATION_FORENSICS  
**Criteria:** `concentration_forensics_v1`  
**Execution:** OFF  
**Parents:** remain REJECTED  
**Claim:** none (descriptive analysis only)

This analysis does not retune parameters, loosen gates, or change fees, fills, PnL, OOS, or execution.

## How to read these numbers

- Tournament STABILITY uses `abs(forward)` shares. Frozen single-route families always have `top_route_share=1` (**ROUTE_SHARE_TAUTOLOGY**). That is not a discovered venue mechanism.
- Forensic **sum NET** is descriptive per-event waterfall accounting. It is **not** tournament EXPECTED_NET and is **not** claimed profit.
- A REGIME_DEPENDENT class creates a **new hypothesis ID**. The parent strategy remains REJECTED and is not modified.
- Replay uses frozen params and the frozen OOS window. A later larger tape with the same stride can shift which rows are indexed; counts may differ slightly from the tournament scoreboard.

## Frozen tournament context

- DATASET: `mdresearch-research_md_v1-d71a392a288f1195`
- Tape duration: 87267.0 s
- Stride: 4 (matches the observed-tape tournament rerun)
- Frozen params source: `data/research_tournament/rerun_stride4/results.json`
- OOS window: `{'end_ts_ns_inclusive': 1786967758683398912, 'fraction': 0.3, 'label': 'UNTOUCHED_OOS', 'start_ts_ns': 1786946526693526784, 'untouched_by': ['feature_discovery', 'parameter_tuning', 'threshold_selection', 'model_fitting']}`

Per-event NET uses the same shared waterfall rates as the tournament. The **sum** of per-event NET is forensic accounting, not tournament EXPECTED_NET (which is mean-edge × notional − costs once).

## cross_venue_dislocation

- Parent verdict: `UNSTABLE` / `STABILITY`
- Frozen params: `{'dislocation_bps': 40.0, 'follower': 'bitvavo', 'horizon_ms': 500, 'leader': 'binance', 'venue_a': 'binance', 'venue_b': 'bitvavo'}`
- Tournament EXPECTED_NET: 3.6719 (unchanged)
- Forensic sum NET: 4236.8519 over 1238 OOS events
- Route tautology: True

### Top contributors (descriptive)

- Top 1 symbols NET: 1634.8497 (38.6%)
- Top 5 symbols NET: 4231.9053 (99.9%)
- Top 10 symbols NET: 4236.8519 (100.0%)
- HHI abs-forward (symbols): 0.3177
- Top symbol: `SOLEUR` NET=1634.8497 (38.6%); rest=2602.0021
- Top venue pair: `binance|bitvavo` NET=4236.8519 (100.0%)
- Top hour: `8` NET=1020.6482 (24.1%)
- Top chrono block: `BLOCK_1` NET=1039.4721 (24.5%)
- Top 10 events share: 5.2%

### Chronological blocks (equal width on frozen OOS; not chosen by PnL)

- Positive blocks: 5
- Negative blocks: 0
- Median block PnL: 900.1848
- Mean block PnL: 847.3704
- Best: BLOCK_1 NET=1039.4721
- Worst: BLOCK_4 NET=660.2198

| Block | signals | gross | fees | slippage | adverse | NET | NET/trade |
|---|---:|---:|---:|---:|---:|---:|---:|
| BLOCK_1 | 308 | 1184.2321 | 107.8000 | 6.1600 | 24.6400 | 1039.4721 | 3.3749 |
| BLOCK_2 | 262 | 1031.6614 | 91.7000 | 5.2400 | 20.9600 | 908.5214 | 3.4676 |
| BLOCK_3 | 235 | 838.9039 | 82.2500 | 4.7000 | 18.8000 | 728.4539 | 3.0998 |
| BLOCK_4 | 174 | 741.9998 | 60.9000 | 3.4800 | 13.9200 | 660.2198 | 3.7944 |
| BLOCK_5 | 259 | 1021.9148 | 90.6500 | 5.1800 | 20.7200 | 900.1848 | 3.4756 |

### Classification

- CONCENTRATION_SOURCE: regime quote_age_regime focus=STALE share=0.737 features=['market_return_bps', 'quote_age_ms']
- CONCENTRATION_CLASS: `REGIME_DEPENDENT`
- STRUCTURAL_FEATURE_FOUND: YES
- RECOMMENDED_ACTION: Create a NEW regime-gated hypothesis using only pre-trade features.

- ROUTE_SHARE_TAUTOLOGY: frozen params select a single venue/pair, so tournament top_route_share=1 by construction. Not used as VENUE_SPECIFIC.
- tournament_top_route_share=1.0

### Leave-one-group-out (forensic; not used to drop losers)

**symbol** FULL=4236.8519
- WITHOUT `SOLEUR`: 2602.0021 (group NET=1634.8497)
- WITHOUT `ETHEUR`: 2868.9913 (group NET=1367.8606)
- WITHOUT `BTCEUR`: 3164.0331 (group NET=1072.8187)
- WITHOUT `LINKEUR`: 4118.2384 (group NET=118.6134)
- WITHOUT `XRPEUR`: 4199.0890 (group NET=37.7628)
- WITHOUT `NEAREUR`: 4233.0823 (group NET=3.7696)
- WITHOUT `ADAEUR`: 4234.1855 (group NET=2.6664)
- WITHOUT `LTCEUR`: 4238.7435 (group NET=-1.8916)

**venue_pair** FULL=4236.8519
- WITHOUT `binance|bitvavo`: 0.0000 (group NET=4236.8519)

**chrono_block** FULL=4236.8519
- WITHOUT `BLOCK_1`: 3197.3798 (group NET=1039.4721)
- WITHOUT `BLOCK_2`: 3328.3305 (group NET=908.5214)
- WITHOUT `BLOCK_5`: 3336.6671 (group NET=900.1848)
- WITHOUT `BLOCK_3`: 3508.3980 (group NET=728.4539)
- WITHOUT `BLOCK_4`: 3576.6321 (group NET=660.2198)

### Regime contrast (pre-trade features only)

- event_density_regime: focus=`SPARSE` share=73.3% structural=True features=['book_imbalance', 'vol_bps', 'event_density', 'market_return_bps']
- liquidity_regime: focus=`MEDIUM` share=79.2% structural=True features=['vol_bps', 'market_return_bps']
- market_return_regime: focus=`FLAT` share=100.0% structural=False features=[]
- quote_age_regime: focus=`STALE` share=73.7% structural=True features=['market_return_bps', 'quote_age_ms']
- signal_strength: focus=`STRONG` share=100.1% structural=True features=['signal_strength_bps', 'spread_bps', 'vol_bps', 'event_density', 'market_return_bps', 'cross_venue_divergence_bps']
- spread_regime: focus=`TIGHT` share=99.7% structural=True features=['signal_strength_bps', 'spread_bps', 'vol_bps', 'event_density', 'market_return_bps', 'cross_venue_divergence_bps']
- volatility_regime: focus=`LOW` share=94.2% structural=True features=['spread_bps', 'vol_bps', 'event_density', 'market_return_bps']

### Null checks (fixed seed; not an alpha claim)

- seed=20260817 N=199
- top symbol abs-forward share: 38.2% p_signal=0.2300
- top block abs-net share: 24.5% p_rotate=0.5900

## short_horizon_mean_reversion

- Parent verdict: `UNSTABLE` / `STABILITY`
- Frozen params: `{'deviation_bps': 20.0, 'horizon_ms': 500, 'venue': 'bitvavo'}`
- Tournament EXPECTED_NET: 2.2127 (unchanged)
- Forensic sum NET: 5001.4026 over 2393 OOS events
- Route tautology: True

### Top contributors (descriptive)

- Top 1 symbols NET: 2116.5885 (42.3%)
- Top 5 symbols NET: 5008.7495 (100.1%)
- Top 10 symbols NET: 5013.7446 (100.2%)
- HHI abs-forward (symbols): 0.3271
- Top symbol: `SOLEUR` NET=2116.5885 (42.3%); rest=2884.8141
- Top venue pair: `bitvavo` NET=5001.4026 (100.0%)
- Top hour: `6` NET=1026.6185 (20.5%)
- Top chrono block: `BLOCK_1` NET=1239.6038 (24.8%)
- Top 10 events share: 5.7%

### Chronological blocks (equal width on frozen OOS; not chosen by PnL)

- Positive blocks: 5
- Negative blocks: 0
- Median block PnL: 1003.9275
- Mean block PnL: 1000.2805
- Best: BLOCK_1 NET=1239.6038
- Worst: BLOCK_4 NET=775.4548

| Block | signals | gross | fees | slippage | adverse | NET | NET/trade |
|---|---:|---:|---:|---:|---:|---:|---:|
| BLOCK_1 | 538 | 1573.1638 | 269.0000 | 10.7600 | 43.0400 | 1239.6038 | 2.3041 |
| BLOCK_2 | 469 | 1304.4755 | 234.5000 | 9.3800 | 37.5200 | 1013.6955 | 2.1614 |
| BLOCK_3 | 460 | 1289.1275 | 230.0000 | 9.2000 | 36.8000 | 1003.9275 | 2.1825 |
| BLOCK_4 | 447 | 1052.5948 | 223.5000 | 8.9400 | 35.7600 | 775.4548 | 1.7348 |
| BLOCK_5 | 479 | 1265.7010 | 239.5000 | 9.5800 | 38.3200 | 968.7210 | 2.0224 |

### Classification

- CONCENTRATION_SOURCE: regime spread_regime focus=WIDE share=1.000 features=['signal_strength_bps', 'spread_bps', 'vol_bps', 'market_return_bps', 'quote_age_ms', 'cross_venue_divergence_bps']
- CONCENTRATION_CLASS: `REGIME_DEPENDENT`
- STRUCTURAL_FEATURE_FOUND: YES
- RECOMMENDED_ACTION: Create a NEW regime-gated hypothesis using only pre-trade features.

- ROUTE_SHARE_TAUTOLOGY: frozen params select a single venue/pair, so tournament top_route_share=1 by construction. Not used as VENUE_SPECIFIC.
- tournament_top_route_share=1.0

### Leave-one-group-out (forensic; not used to drop losers)

**symbol** FULL=5001.4026
- WITHOUT `SOLEUR`: 2884.8141 (group NET=2116.5885)
- WITHOUT `ETHEUR`: 3428.1155 (group NET=1573.2871)
- WITHOUT `BTCEUR`: 3690.6100 (group NET=1310.7926)
- WITHOUT `ATOMEUR`: 5007.6644 (group NET=-6.2618)
- WITHOUT `UNIEUR`: 5006.3139 (group NET=-4.9112)
- WITHOUT `ADAEUR`: 4996.9205 (group NET=4.4821)
- WITHOUT `NEAREUR`: 4997.8034 (group NET=3.5992)
- WITHOUT `XRPEUR`: 4998.0414 (group NET=3.3612)

**venue_pair** FULL=5001.4026
- WITHOUT `bitvavo`: 0.0000 (group NET=5001.4026)

**chrono_block** FULL=5001.4026
- WITHOUT `BLOCK_1`: 3761.7988 (group NET=1239.6038)
- WITHOUT `BLOCK_2`: 3987.7071 (group NET=1013.6955)
- WITHOUT `BLOCK_3`: 3997.4751 (group NET=1003.9275)
- WITHOUT `BLOCK_5`: 4032.6816 (group NET=968.7210)
- WITHOUT `BLOCK_4`: 4225.9478 (group NET=775.4548)

### Regime contrast (pre-trade features only)

- event_density_regime: focus=`SPARSE` share=99.1% structural=True features=['depth_eur', 'event_density', 'quote_age_ms']
- liquidity_regime: focus=`THIN` share=51.6% structural=True features=['depth_eur']
- market_return_regime: focus=`FLAT` share=100.0% structural=False features=[]
- quote_age_regime: focus=`STALE` share=52.2% structural=True features=['quote_age_ms']
- signal_strength: focus=`STRONG` share=103.8% structural=True features=['signal_strength_bps', 'spread_bps', 'depth_eur', 'book_imbalance', 'vol_bps', 'market_return_bps', 'quote_age_ms', 'cross_venue_divergence_bps']
- spread_regime: focus=`WIDE` share=100.0% structural=True features=['signal_strength_bps', 'spread_bps', 'vol_bps', 'market_return_bps', 'quote_age_ms', 'cross_venue_divergence_bps']
- volatility_regime: focus=`HIGH` share=59.7% structural=True features=['book_imbalance', 'vol_bps', 'market_return_bps']

### Null checks (fixed seed; not an alpha claim)

- seed=20260817 N=199
- top symbol abs-forward share: 40.7% p_signal=0.0050
- top block abs-net share: 24.8% p_rotate=0.0950

## Hypotheses

- NEW_HYPOTHESES_CREATED: ['H-0005', 'H-0007']
- LLM_USED: NO
- PRODUCTION_TRADING_CHANGED: NO
- NEXT_RESEARCH_ACTION: Queue the new independent hypotheses for a fresh DEV/OOS tournament. Do not inherit parent PnL. Do not implement as production strategies yet.

A new hypothesis ID does **not** implement a new strategy. Parents were not modified.

## LLM advisory

LLM not used (UNAVAILABLE).

## Final output

```
DATASET: mdresearch-research_md_v1-d71a392a288f1195

STRATEGIES_ANALYZED: ['cross_venue_dislocation', 'short_horizon_mean_reversion']

CROSS_VENUE_DISLOCATION:

CONCENTRATION_SOURCE: regime quote_age_regime focus=STALE share=0.737 features=['market_return_bps', 'quote_age_ms']

CONCENTRATION_CLASS: REGIME_DEPENDENT

STRUCTURAL_FEATURE_FOUND: YES

RECOMMENDED_ACTION: Create a NEW regime-gated hypothesis using only pre-trade features.


SHORT_HORIZON_MEAN_REVERSION:

CONCENTRATION_SOURCE: regime spread_regime focus=WIDE share=1.000 features=['signal_strength_bps', 'spread_bps', 'vol_bps', 'market_return_bps', 'quote_age_ms', 'cross_venue_divergence_bps']

CONCENTRATION_CLASS: REGIME_DEPENDENT

STRUCTURAL_FEATURE_FOUND: YES

RECOMMENDED_ACTION: Create a NEW regime-gated hypothesis using only pre-trade features.


NEW_HYPOTHESES_CREATED: ['H-0005', 'H-0007']

LLM_USED: NO

PRODUCTION_TRADING_CHANGED:
NO

NEXT_RESEARCH_ACTION: Queue the new independent hypotheses for a fresh DEV/OOS tournament. Do not inherit parent PnL. Do not implement as production strategies yet.
```


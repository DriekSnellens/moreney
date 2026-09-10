# Product Decision: Retire CVD → Capital Velocity Desk

**Status:** Product decision + Phase-1 unlock wired  
**Date:** 2026-09-02  
**Capital:** €2k–€4k Bitvavo + OKX  
**Live strategy:** `maker_inventory` only  

---

## Verdict (one line)

**Abandon cross-venue dislocation (CVD).** No strategy family in this repo has **proven live-executable positive NET** on Bitvavo+OKX at this capital. The only remaining live-wired path is a **time-boxed maker Capital Velocity Desk** with hard kill criteria — not a claim of €50–100/day.

---

## 1. Why CVD is retired

| Evidence | NET | Honesty |
|----------|----:|---------|
| Research final validation (mid, fill=1) | **+€212k** | Mid illusion |
| Paper fleet CVD inject | **+€3.4k** | Paper taker / mid-optimistic |
| LIVE_SHADOW TOB taker | **−€416** (vs research expected +€5.5k) | Executable |
| Longer shadow run | **−€13.6k** | Executable |
| Economic parity | ~99% live reject (`DIFFERENT_PRICE_SELECTION`) | Mid ≠ TOB |

Root cause (~83% of gap): research books **mid** dislocation as capturable; live TOB lock finds ~0 / negative cross.  
Detail: `docs/CVD_SHADOW_GAP_DIAGNOSIS.md`, `data/research/shadow_validation/accumulator.json`.

**Product flag:** `live_cvd_abandoned=True` (and `live_disable_research_hooks=True`).  
No LIMITED_LIVE CVD sleeve. No dual-sleeve €50–100 thesis that depends on S2 CVD.

---

## 2. Inventory after CVD (all families)

| Family | Best honest outcome | Deploy? |
|--------|---------------------|---------|
| CVD | Shadow **negative** | **RETIRED** |
| maker_inventory | Live ≈ **−€9.6** bridge; TT lab OOS **−€125**; fill lab **−€62** / 17 RTs | **Only live path** — expectancy **unproven** |
| lead_lag / OBI / momentum | Tournament **OOS_FAILED** | No |
| mean_reversion | Tiny mid NET, **UNSTABLE** | Research only |
| executable VWAP arb | Lab **0 accepts** under gates | Unknown / thin |
| funding / triangle / FX / equity | Off or no data | No |

Sources: `docs/STRATEGY_LAB_REPORT.md`, `docs/STRATEGY_RESEARCH_TOURNAMENT_REPORT.md`, `data/research/strategy_pnl_gap.json`, `data/research/live_underperformance_diagnosis.json`.

**There is no proven profitable replacement.** Claiming one would invent alpha the tape rejects.

---

## 3. Proposed strategy: Capital Velocity Desk

### 3.1 What it is

A **single-sleeve** live desk that only tries to do one job: recycle **new** focus bags through the active ring while vault underwater bags stay never-loss.

```
┌──────────────────────────────────────────────────────────┐
│                 CAPITAL VELOCITY DESK                      │
│  Strategy: maker_inventory                                 │
│  Working capital: active ring €1k / venue                  │
│  Vault: underwater + long-hold (never-loss, cut_loss=0)    │
├──────────────────────────────────────────────────────────┤
│  Unlock: Util-B ignores *other* underwater bags            │
│  Soft ring momentum while NEED (0.0005 vs full 0.0015)     │
│  Same-base underwater adds still blocked                   │
│  CVD inject / shadow: ABANDONED                            │
├──────────────────────────────────────────────────────────┤
│  Prove first: NET/hour > 0 with ring filled                │
│  Kill hard if expectancy stays ≤ 0 (see §5)                │
└──────────────────────────────────────────────────────────┘
```

### 3.2 Why this (and not another mid story)

1. **Only live-wired** strategy on Bitvavo+OKX with inventory, exits, dual-venue ledger.
2. Current loss is dominated by **capital deadlock** (ring €0 while ~€3.8k free) — an architecture bug, not yet a settled expectancy test.
3. Throughput math (idealized, fee-free): €55 × 1.2% ≈ **€0.66**/full exit → €20/day needs ~30 exits; €50 needs ~76. That is **aspirational**, not evidenced.

### 3.3 Honest target ladder

| Stage | Gate | Target |
|-------|------|--------|
| **0 Deadlock** | Ring stays €0 | Fix unlock (this PR) |
| **1 Deploy** | Ring ≥ €600 within 24h of free ≥ €500 | Capital moves |
| **2 Velocity** | ≥ 2 fills/hour over 48h with ring filled | Recycle works |
| **3 Expectancy** | Sleeve NET ≥ €0 over 48h with ≥ 30 closed RTs | Edge after costs |
| **4 Floor** | NET/hour ≥ **€0.50** over 5–7 days | Keep running |
| **Stretch** | €20–45/day closed-trade netto | Only if stage 4 holds |

Do **not** use dashboard €20–50/day as a forecast until stage 4 is green.

### 3.4 What we deliberately do **not** do

- Re-enable CVD / mid≥40 bps taker arb
- Loosen fill model off trade-through to invent maker edge
- Forced cut-loss on vault bags (product hatch; operator-only)
- Claim dual-sleeve €50–100 while S2 is abandoned

---

## 4. Implementation (this change set)

| Knob | Value | Role |
|------|------:|------|
| `live_cvd_abandoned` | **True** | Product retirement; forces research hooks off |
| `live_disable_research_hooks` | **True** | No CVD inject / shadow on live hot path |
| `live_micro_ring_util_b_ignore_underwater` | **True** | Unlock Util-B despite vault underwater |
| `live_micro_ring_momentum_min_return` (session) | **0.0005** | Soft floor while ring NEED |
| `paper_buy_momentum_min_return` (session) | **0.0015** | Full floor when ring OK |
| `live_micro_buy_quality_underwater_count` | **4** | Pause only after several *new* bad bags |

Same-base blocks (`UNDERWATER_BASE_BLOCK`, `UNDERWATER_ADD_BLOCK`) stay on.

---

## 5. Kill criteria (hard)

| Gate | Kill if |
|------|---------|
| Deploy | After unlock, ring still **€0 for ≥24h** with free ≥ €500 |
| Velocity | Ring ≥ €600 but **fills/hour < 2** over 48h |
| Expectancy | Sleeve NET **< €0 over 48h** with ≥30 closed round-trips |
| Toxicity | Mean adverse ≳ expected NET margin (repeat ~27 vs ~8–12 bps) |
| Drawdown | Velocity sleeve **−€50/day** or weekly FIFO ≤ **−€75** |
| Thesis | After 5–7 days unlocked: **NET/hour < €0.50** → abandon maker as profit path; cash/vault only |

On thesis kill: stop new ring buys, keep never-loss harvest on BE+ bags, open a **TOB-honest** research track (below) — do not resurrect CVD mid.

---

## 6. Parallel research (not live)

If velocity desk fails kill gates, next research (shadow only):

1. Synchronized **TOB + depth** tape (not mid).
2. Re-run **VWAP-executable** short-horizon mean-reversion / lockable-edge arb with the same gates that rejected CVD.
3. Promote only if LIVE_SHADOW NET ≥ 0 on a fresh run with `PriceSelection=TOB`.

Mean-reversion was the least-dead tournament survivor and is still **not** deployable today.

---

## 7. Operator checklist

1. Deploy this branch; confirm `why_idle` shows `CVD_ABANDONED` and `UTIL_B_IGNORE_UNDERWATER on`.
2. Confirm ring leaves NEED within hours when free cash exists.
3. Track sleeve NET/hour, fills/hour, adverse bps — not scan emits.
4. Hit any kill gate → pause new risk and reassess; do not flip CVD back on.

---

## 8. Related artifacts

- `docs/CVD_SHADOW_GAP_DIAGNOSIS.md`
- `docs/LIVE_UNDERPERFORMANCE_DIAGNOSIS.md` (if present) / `data/research/live_underperformance_diagnosis.json`
- `docs/STRATEGY_LAB_REPORT.md`
- `docs/PAPER_VS_RESEARCH_PNL_GAP_REPORT.md` (if present) / `data/research/strategy_pnl_gap.json`
- Supersedes dual-sleeve CVD half of `docs/PROFIT_ARCHITECTURE_DUAL_SLEEVE.md` (that thesis is product-rejected for S2).

# Paper vs Research PnL Gap — Strategy Mismatch (CVD vs maker_inventory)

**Generated:** 2026-09-01T19:51:36.757800+00:00

Research-only. No live logic, parameters, or safety gates changed.

## 1. Executive Summary

The Paper vs Research PnL gap is primarily a **strategy and economics mismatch**, not a single calibration bug. Positive paper fleet results (+€3.4k) come from **CVD inject** on paper taker semantics; negative live results (-€9.6) come from **maker_inventory** with never-loss exits. Research canonical (+€212k) is a third world: historical mid-convergence at fill_prob=1.0. Shadow/parity prove that even the **same CVD signals** flip negative under taker top-of-book pricing (~99% live gate reject).

### Comparison at a glance

| World | Strategy | PnL / expected | Unit count | €/unit | Execution model |
|-------|----------|---------------:|-----------:|-------:|-----------------|
| Research (canonical replay) | `cross_venue_dislocation` | €212011.77768994423804883243 | 67443 | 3.143569795085394155788331332 | Taker round-trip; mid dislocation; fill_prob=1.0 (baseline) |
| Research (moderate realism) | `cross_venue_dislocation` | €75449.0876486249965808878960733458 | 67443 | 1.118708949018059644157108908 | 50% miss + partial fills + fee/slip overlays |
| Paper fleet (5 twins, Aug 20–27) | `cross_venue_dislocation (CVD inject)` | €3439.889225806955029258244836 | 4194 | 0.8201929484518252334902825074 | PaperExecutor + frozen CVD inject; NOT maker_inventory |
| Paper lab (8021, maker sandbox) | `maker_inventory` | €0.02779682630784743437828839 | 1 | — | Post-only maker composite + triangle + funding |
| Live micro (8020) | `maker_inventory` | €-9.622744997389643006513026041 | — | — | Live maker buys + trail/taker exits; never-loss |
| Shadow paper (incomplete sample) | `cross_venue_dislocation` | €5517.812960840839 | 1096 | 5.034500876679597627737226277 | Observe-only taker sim on live L1 |
| Shadow taker sim (same candidates) | `cross_venue_dislocation` | €-416.34325205833613 | 1096 | -0.3798752299802336952554744526 | Top-of-book taker prices (not mid dislocation) |

## 2. Paper Fleet — waar komt de +€3.4k vandaan?

De vijf capital twins (`200live`–`25000live`) draaien `_build_strategy()` met `paper_maker_enabled=false` → `cross_exchange_arbitrage` + funding. **Maar ~99% van realized PnL komt uit CVD inject** (`cross_venue_dislocation`), niet uit de geselecteerde arb-strategie.

- **Aggregate CVD realized:** €3439.889225806955029258244836
- **Trades:** 4194 | **Executions:** 10282 | **Opportunities seen:** 619943
- **Execution rate (exec/opp):** 0.0166
- **€/trade (paper CVD):** 0.8201929484518252334902825074
- **€/signal (research canonical @€100):** 3.143569795085394155788331332

Per instance (tracker.realized_pnl):

- **200live**: start €200 → equity €541.1062909431525836372461921 | realized €679.8088952952598653652569045 | primary `cross_venue_dislocation (inject)`
- **500live**: start €500 → equity €863.7201295763479704853930393 | realized €723.7260206796428449057571911 | primary `cross_venue_dislocation (inject)`
- **1000live**: start €1000 → equity €1167.273220906356724559110144 | realized €336.7078758218293863629224139 | primary `cross_venue_dislocation (inject)`
- **5000live**: start €5000 → equity €5365.614150408404185076102984 | realized €734.4992047699923535013677990 | primary `cross_venue_dislocation (inject)`
- **25000live**: start €25000 → equity €25491.67327266326263412877816 | realized €1013.697957624963794461924911 | primary `cross_venue_dislocation (inject)`

## 3. Research CVD — wat meet canonical replay?

- **CANONICAL_REPLAY_NET:** €212011.77768994423804883243
- **Signals:** 67443
- **MODERATE_REALISM NET:** €75449.0876486249965808878960733458

Hypothese: okx|bitvavo **mid** dislocatie ≥40 bps convergeert over 5s. Kosten: taker/taker round-trip (~35 bps) + slip/adverse/latency. Baseline: **elke signal = fill** (fill_prob=1.0).

## 4. Waarom research ≠ paper ≠ live

### [HIGH] STRATEGY_MISMATCH

**Production live/paper-hot path runs maker_inventory, research validates cross_venue_dislocation**

Paper fleet PnL (+€3.4k CVD) measures frozen CVD inject on a taker-style paper path. Live micro (-€9.6) measures maker spread capture with inventory/trail/never-loss. These are different alphas, not comparable without explicit relabeling.

*Quantified:* Paper CVD aggregate minus live maker realized = €3449.511970804344672264757862

### [HIGH] PRICING_MISMATCH

**Research gross uses mid dislocation; live gate uses ask-minus-ask**

Economic parity: 3156/3184 candidates pass frozen research but fail live NetProfitCalculator. Root cause: DIFFERENT_PRICE_SELECTION. Breakeven under frozen costs ≈47 bps vs 40 bps signal threshold.

### [HIGH] EXECUTION_MODEL

**Canonical replay assumes fill_prob=1.0; paper/live face gates and resting fills**

Paper CVD: 0.0166 exec/opportunity rate. €0.8201929484518252334902825074/trade vs research €3.143569795085394155788331332/signal. Shadow taker sim: mean gap ≈€5.66/candidate vs frozen expected NET.

*Quantified:* Shadow taker sim minus research expected (same 1096 cands) = €-5934.15621289917513

### [MEDIUM] SCALE_AND_PERIOD

**Research totals are full-tape OOS; paper is one week live tape at fleet scale**

Research canonical €212011.77768994423804883243 on 67443 signals @ €100 notional. Paper fleet CVD €3439.889225806955029258244836 on 4194 trades over ~168h. Extrapolation requires matched notional, window, and route — not done here.

### [MEDIUM] INVENTORY_AND_EXIT

**Live maker never realizes losses; paper CVD counts round-trip wins only**

Live dominant skips: time_stop_below_be=19041, trail_no_trusted_cost=3778. CVD paper tracker shows 0 losing trades on cross_venue_dislocation — optimistic vs live bag-holding.

## 5. Economic parity & shadow (zelfde CVD, andere wereld)

- Parity candidates: **3184**
- Pass research / fail live: **3156** (DIFFERENT_PRICE_SELECTION)
- Shadow candidates: **1096** (windows 3/20 complete)
- Shadow RESEARCH_EXPECTED_NET: **€5517.8130**
- Shadow LIVE_SHADOW_EXECUTION_NET: **€-416.3433**
- Mean execution gap: **€-5.6570**/observation

Voorbeeld (parity sample): BTCEUR 161 bps dislocation → research +€1.14, live NET −€1.77 omdat leader_ask > follower_ask terwijl mids divergeren.

## 6. Live maker_inventory

- **Bridge realized PnL:** €-9.622744997389643006513026041
- **Portfolio MTM:** €4082.85993046565817895
- Economie: post-only maker quotes, sequential buy→trail sell, **never sell below break-even**
- Dominant skips: `time_stop_below_be`, `trail_dust`, `focus_base_required`, `buy_quality_pause`

## 7. Eerlijke vergelijking (aanbevolen)

- Compare paper_lab (maker_inventory) vs live micro on same env knobs — not paper_200live vs research.
- Compare shadow RESEARCH_EXPECTED_NET vs LIVE_SHADOW_EXECUTION_NET on completed windows only (need 20 windows).
- Re-run final_validation slice on Aug 20–27 live tape fingerprint for apples-to-apples period match.
- Do not extrapolate research €212k to fleet €200 starting capital without notional and signal rate conversion.

## 8. Conclusie

De gap is **verwacht** zolang paper-fleet success op **CVD inject** wordt afgelezen terwijl live op **maker_inventory** draait. Research +€212k is geen voorspelling voor live maker PnL; het is een upper bound op een **ander product** (mid-dislocation taker arb). Shadow/parity tonen dat zelfs binnen CVD de live pricing formule ~100% reject geeft. Volgende research-stap: voltooi shadow validation (20 windows) en vergelijk `paper_lab` (maker) met live micro op identieke config — niet paper_200live vs canonical.

# Profit Architecture — Dual-Sleeve Path to €50–100/day

**Status:** Design proposal (no live wiring in this document’s PR)  
**Capital assumption:** €2k–€4k deployable across Bitvavo + OKX (pocket already present)  
**Infra assumption:** market data, dual-venue ledger, micro bridge, GOE, intelligence stack — already live  
**Goal:** Compose engines that **already exist** into an architecture that can hit **€50–100/day netto**, without inventing a third strategy.

---

## 0. Why current live cannot hit the target

Today one sleeve tries to do everything and is deadlocked:

| Constraint | Effect |
|------------|--------|
| Live = `maker_inventory` only | CVD (best evidenced alpha) is off hot path |
| Active ring = BE+ focus only | Underwater bags do not count as “working” |
| Never-loss + cut-loss=0 | Stuck bags cannot recycle |
| `ring_soft_block_underwater_eur=25` | One €55 bag kills Util-B refill of free cash |
| Expectation conflates worlds | Paper CVD / alt-beta MTM ≠ live maker harvest |

**Diagnosis (existing):** `docs/LIVE_UNDERPERFORMANCE_DIAGNOSIS.md`  
**Gap (existing):** `docs/PAPER_VS_RESEARCH_PNL_GAP_REPORT.md`

The fix is not “more coins” or “lower one fee knob.” It is a **two-lane capital architecture** with an explicit unlock of maker velocity and a gated path for CVD.

---

## 1. Target model (make the math honest)

### 1.1 Band

| Band | Meaning |
|------|---------|
| **Floor €50/day** | Minimum acceptable once both sleeves are live and green |
| **Stretch €100/day** | Strong day / high CVD activity |
| **Ramp €20–50/day** | Maker-only while CVD still in LIMITED_LIVE |

These are **closed-trade netto** targets (fees in), not MTM alt-beta.

### 1.2 Split that can add up

With ~€4k free across venues and ~€2k pocket budget:

| Sleeve | Working capital | Role | Contribution band |
|--------|----------------:|------|-------------------|
| **S1 — Maker Velocity** | €1.000–€1.500 / venue (ring) | Continuous recycle of BE+ bags | **€20–45/day** |
| **S2 — CVD Alpha** | €500–€1.000 risk sleeve | Discrete dislocation bursts | **€30–70/day** on active days |
| **Vault** | Remainder | Never-loss reserve; no new risk | €0 by design |

**Combined:** €50–100/day is plausible when S1 runs continuously and S2 fires on dislocations — **using engines already in the repo**.

### 1.3 Maker throughput (S1) — already documented

| Clip | Soft arm | €/full exit | For €30/day | For €45/day |
|------|---------:|------------:|------------:|------------:|
| €55 | 1.2% | ≈ €0.66 | ~45 exits | ~68 exits |
| Ring €1k @ 1.2% | — | €12 / full turn | ~2.5×/day | ~3.8×/day |

Requirement: **capital actually in the ring** and exits that clear. Today: ring = €0 → S1 contribution = €0.

### 1.4 CVD contribution (S2) — already evidenced in paper

Paper fleet CVD historically ~€0.82/trade at fleet scale; research moderate realism still strongly positive on the same signal family.  
Live shadow is currently **NO-GO** → S2 starts in **observe / limited** only, but the **architecture reserves a sleeve** so graduation does not require a rewrite.

---

## 2. Proposed architecture: Dual-Sleeve Profit Desk

```
┌─────────────────────────────────────────────────────────────────┐
│                     PROFIT DESK (live micro host)                │
│  PaperRunner cycle + MicroBudgetLiveExecutor + LiveMicroEngine   │
├───────────────────────────────┬─────────────────────────────────┤
│  SLEEVE S1 — MAKER VELOCITY   │  SLEEVE S2 — CVD ALPHA           │
│  Strategy: maker_inventory    │  Strategy: cross_venue_disloc.   │
│  Capital: active ring         │  Capital: cvd_risk_sleeve         │
│  Goal: €/hour recycle         │  Goal: €/signal when dislocation │
│  Exit: trail + exit_engine    │  Exit: round-trip / hedge book   │
│  Gate: never-loss on vault;   │  Gate: shadow VALIDATED →         │
│        soft Util-B on ring    │        LIMITED_LIVE → SCALE      │
├───────────────────────────────┴─────────────────────────────────┤
│  SHARED QUALITY / RISK STACK (already wired)                     │
│  entry_quality → opportunity_engine → GOE/EV → risk              │
│  adverse_selection → resting_order_intel → execution_quality     │
│  venue_ledger + funding advice (no auto-transfer)                │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1 Design principles

1. **Two P&L owners, one host.** Do not attribute CVD paper totals to maker, or vice versa.
2. **Capital is reserved per sleeve.** S2 cannot eat S1 ring; S1 cannot spend S2 risk budget.
3. **Vault is sacred.** Never-loss stays on vault inventory; sleeve policies can differ.
4. **Graduate CVD; don’t smuggle it.** Research hooks stay off until LIMITED_LIVE flag flips.
5. **Measure velocity, not scans.** KPIs = fills/hour, ring turns/day, NET/hour, sleeve PnL — not `opportunities_emitted`.

---

## 3. Sleeve S1 — Maker Velocity (unlock what you already run)

### 3.1 Engines to compose (exist today)

| Engine | Path | Job in S1 |
|--------|------|-----------|
| `MakerInventoryStrategy` | `bot/strategies/maker_inventory.py` | Emit post-only quotes |
| Active ring + Util-B | `micro_bridge_executor.py` | Keep €1k BE+ working per venue |
| Velocity sleeve | same | Daily loss cap on working capital |
| Trail + BE harvest | `trail_policy.py` + bridge | Soft/hard / partial recycle |
| Exit engine | bridge | Touch/improve stale BE+ asks |
| Opportunity / entry quality | `strategies/opportunity_engine.py`, `entry_quality.py` | Size/reject bad entries |
| Adverse + resting intel | `bot/intelligence/*` | Cancel toxic resting bids |
| Capital intelligence | `intelligence/capital_intelligence.py` | Dynamic deployable vs reserve |

### 3.2 Architectural change: break the deadlock (without abandoning never-loss)

**Problem today:** underwater book ≥ €25 disables the soft path that was meant to refill an empty ring.

**Proposed capital states (per venue):**

| State | Condition | Allowed actions |
|-------|-----------|-----------------|
| `RING_HEALTHY` | active ≥ 70% of ring target | Normal momentum + focus-only |
| `RING_NEED` | active < ring target, free ≥ €50 | Soft momentum + focus relax (Util-B) |
| `RING_NEED_STUCK` | NEED **and** underwater ≥ soft_block | **Still allow RING_NEED buys on free cash** into *new* focus bases; do **not** disable Util-B solely because vault bags are underwater |
| `SLEEVE_PAUSED` | sleeve daily loss cap hit | No new S1 buys |
| `VAULT_ONLY` | kill / circuit | Exits ≥ BE only |

**Key rule change (conceptual):**

> Soft-block underwater applies to **adding size on underwater bases** and to **risking vault**,  
> **not** to “may free cash open a fresh BE+ focus bag while NEED.”

That single separation is the architectural unlock. Cut-loss remains a **product decision**, not a requirement for S1 to breathe.

### 3.3 Optional product hatch (explicit, not silent)

| Hatch | Effect |
|-------|--------|
| `cut_loss` off (current) | Vault bags wait for BE recovery |
| `cut_loss` limited (product) | Cap underwater notional (e.g. max €150 stuck) by realizing loss on oldest bags | Frees ring definition and psychology; must be logged as intentional |

Architecture works with hatch closed; hatch only accelerates unlock.

### 3.4 S1 operating loop

```
every cycle:
  reconcile venue cash + inventory
  classify capital state (per venue)
  maker_inventory scan → rank (focus boost while NEED)
  GOE + opportunity_engine size/reject
  adverse/resting gate resting bids
  place/replace/cancel
  trail/exit_engine harvest BE+ → recycle into free → refill ring
```

### 3.5 S1 success criteria (prove before claiming €50)

| KPI | Ramp green | Floor green |
|-----|------------|-------------|
| Active ring fill | ≥ €600 / venue sustained | ≥ €800 / venue |
| Fills / hour (buys+sells) | ≥ 4 | ≥ 8 |
| Ring turns / day | ≥ 1.5 | ≥ 3 |
| NET € / hour (S1 only) | ≥ €1.0 | ≥ €2.0 |
| Session underwater notional | trending ↓ or flat | < €100 |

---

## 4. Sleeve S2 — CVD Alpha (graduate the proven engine)

### 4.1 Engines to compose (exist today)

| Engine | Path | Job in S2 |
|--------|------|-----------|
| Frozen CVD candidate | `bot/paper/cvd_candidate.py` | Emit dislocation opportunities |
| Shadow validation | `bot/research/shadow_validation/` | Live observe → verdict |
| Economic parity | `bot/research/economic_parity/` | Keep research↔live NET honest |
| Final validation | `bot/research/final_validation/` | ROBUST_PAPER_CANDIDATE already |
| GOE + profitability | `bot/opportunity/*`, `bot/profitability/*` | Live fee/slip gates |
| Live micro engine | `bot/live/micro_engine.py` | Place dual-venue legs |

### 4.2 Graduation ladder (do not skip)

```
SHADOW_OBSERVE  →  SHADOW_VALIDATED  →  LIMITED_LIVE  →  SCALE
     │                    │                  │              │
  hooks observe        go/no-go           tiny sleeve     grow sleeve
  no orders            checklist green    hard loss cap   still capped
```

**LIMITED_LIVE wiring (when green):**

- Flip only a **narrow** flag, e.g. `live_cvd_limited_enabled=True`, **not** blanket `live_disable_research_hooks=False`.
- CVD emits enter the same GOE → risk → `MicroBudgetLiveExecutor` path.
- Fills debit **`cvd_risk_sleeve_eur` only** (separate from active ring).
- Daily loss cap on S2 (e.g. €25–€40) pauses CVD buys independently of S1.
- Max concurrent CVD notional hard cap.

### 4.3 Why S2 belongs in the architecture *now*

Even while shadow is NO-GO, reserving S2:

1. Stops pretending maker alone must carry €100/day.
2. Gives CVD a clean capital home when VALIDATED.
3. Prevents a future “turn all research hooks on” rewrite.

### 4.4 S2 success criteria

| Stage | Gate |
|-------|------|
| Shadow | VALIDATED per `shadow_validation` protocol (windows, fill realism, positive LIVE_SHADOW_EXECUTION_NET) |
| LIMITED_LIVE | ≥ N closed round-trips; sleeve PnL ≥ 0 after fees over M days; no vault bleed |
| SCALE | Raise sleeve only after LIMITED_LIVE green; never from ring |

---

## 5. Shared stack — keep what already works

Do **not** rebuild these; bind them explicitly to both sleeves:

| Layer | Keep | Binding |
|-------|------|---------|
| Venue ledger | Dual cash + inventory | Per-sleeve notional accounting tags |
| GOE waterfall | Profitability → EV → risk | Same gates; sleeve metadata on opportunity |
| Opportunity engine | Score/size/reject | S1 uses full EQ; S2 uses dislocation-specific metadata |
| Adverse selection | Cancel toxic resting | S1 primary; S2 for resting legs if any |
| Resting order intel | HOLD/CANCEL/REPLACE | S1 maker quotes |
| Execution quality | MAKER/TAKER/WAIT | Exit engine already uses taker cushion |
| Funding service | Rebalance **advice** | Manual or future transfer policy — not auto |
| Production flags | Split research from live | Narrow CVD limited flag instead of global hooks dump |

**Stay off live until separately validated:** triangle desk, funding basis strategy, lead-lag execution, FX/equity stubs, regime auto-apply.

---

## 6. Capital map (example €4k free / €2k pocket)

```
Venue Bitvavo (~€1.9k)                 Venue OKX (~€1.9k)
├─ S1 ring target €1.0k                ├─ S1 ring target €1.0k
├─ S2 CVD sleeve €0.5k (shared risk)   │  (CVD sleeve may be venue-agnostic pool)
└─ Vault remainder                     └─ Vault remainder

Shared desk controls:
  - sleeve_s1_daily_loss_cap
  - sleeve_s2_daily_loss_cap
  - desk_daily_kill (hard stop new risk)
```

Tags on every order/fill: `sleeve=S1|S2|VAULT_EXIT` so dashboard PnL splits cleanly.

---

## 7. Implementation phases (product sequence)

### Phase 0 — Expectation hygiene (1 commit)

- Dashboard: show **S1 target** €20–45 and **Desk target** €50–100 separately.
- Stop publishing a single band that conflates alt-beta with maker.

### Phase 1 — Unlock S1 (highest leverage, no CVD)

Code changes (conceptual — product approval required):

1. Split soft-block: underwater blocks **same-base adds**, not all Util-B.
2. Make `live_micro_ring_momentum_min_return` **actually softer** than full momentum.
3. KPI panel: ring fill, fills/hour, NET/hour, sleeve PnL.
4. Optional: raise `ring_soft_block_underwater_eur` as temporary bridge.

**Exit criterion:** S1 green KPIs for 48h.

### Phase 2 — LIMITED_LIVE CVD (only if shadow VALIDATED)

1. Add `live_cvd_limited_enabled` + `live_cvd_risk_sleeve_eur` + S2 loss cap.
2. Wire CVD candidate → GOE → bridge with `sleeve=S2` tag (narrow path).
3. Shadow continues in parallel as monitor.

**Exit criterion:** S2 LIMITED_LIVE green.

### Phase 3 — Desk scale

1. Raise S2 sleeve toward €1k only after Phase 2.
2. Keep S1 ring full; never fund CVD from ring.
3. Desk target €50–100 becomes the operating band.

---

## 8. Why this can work (with your capital + infra)

| Asset you already have | How the architecture uses it |
|------------------------|------------------------------|
| €2k–€4k dual-venue cash | Fund S1 rings continuously; reserve S2 risk |
| Maker + trail + exit engines | S1 recycle machine (once unlocked) |
| CVD research + shadow | S2 alpha lane (once validated) |
| GOE / EQ / intelligence | Shared quality — protect, don’t invent edge |
| Micro bridge + live engine | Single execution host for both sleeves |
| Paper fleet evidence | Proves CVD *can* print; live graduation is the missing bridge |

You do not need a new strategy family. You need:

1. **Capital that can deploy while vault bags heal**, and  
2. **A second sleeve that owns the alpha that paper already proved**, without contaminating maker accounting.

---

## 9. Explicit non-goals

- Auto cut-loss without a product decision.
- Re-enabling all research hooks on the hot path.
- Triangle/funding/lead-lag as part of the €50–100 desk before their own gates.
- Claiming €100/day from maker alone at 1.2% soft arm without ≥8 ring turns/day.
- Extrapolating research €212k replay to this pocket 1:1.

---

## 10. Decision asks (for you)

To implement Phase 1 next, confirm:

1. **Approve S1 unlock rule:** Util-B may open fresh focus bags while NEED even if other bases are underwater (same-base adds still blocked).
2. **Cut-loss hatch:** keep off / limited / review later?
3. **Desk target messaging:** split S1 vs Desk bands on the dashboard?
4. **S2:** start Phase 2 design only after shadow VALIDATED, or prepare LIMITED_LIVE wiring behind a hard-off flag now?

Once those are answered, Phase 1 is an implementation PR against this architecture — not another diagnosis.

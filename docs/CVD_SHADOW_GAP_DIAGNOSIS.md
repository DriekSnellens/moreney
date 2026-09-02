# CVD Shadow Gap Diagnosis — Why LIVE_SHADOW is −€416 vs Research +€5.5k

Research-only. No live parameter or safety-gate changes.

## 1. Executive summary

LIVE_SHADOW_EXECUTION_NET €-416.34 vs RESEARCH_EXPECTED_NET €5517.81 (gap €-5934.16, mean €-5.66/cand) is ~83% price-selection: research invents mid gross that top-of-book taker cannot lock on okx|bitvavo. Fills are fine (~58% full); fees match. Positive LIVE_SHADOW under honest TOB requires a different executable edge filter or execution mode — not fee tweaks and not enabling LIMITED_LIVE on the current mid≥40bps trigger.

### Snapshot

| Metric | Value |
|--------|------:|
| RESEARCH_EXPECTED_NET (B) | €5517.81 |
| LIVE_SHADOW_EXECUTION_NET (C) | €-416.34 |
| Gap sum (C−B) | €-5934.16 |
| Mean / median gap per candidate | €-5.66 / €-5.66 |
| Candidates | 1096 |
| Complete windows | 3 / 20 |
| Fill / partial / no-fill | 58.2% / 38.4% / 1.2% |

## 2. What B vs C actually measure

| World | Formula | Assumption |
|-------|---------|------------|
| **B Research expected** | `€100 × mid_dislocation − 47bps` | Full mid-gap capture, fill_prob=1 |
| **C Live shadow** | TOB fill after 10/50ms − costs×fill_frac | Sell→bid, buy→ask; no fabricated fills |

Code: `economics.expected_from_dislocation` vs `outcomes._captured_edge` + `shadow_execution_net`.

## 3. Concrete example (FULL_FILL)

- Candidate `ea8fd710f2-00000156`
- Mid edge ≈ **559.0 bps** → research NET **€5.12**
- Shadow fill=1652.58 hedge=1654.8 → shadow NET **€-0.60**
- Gap ≈ **€-5.7238629419203475**

The mid book looked ~5.5% dislocated; the lockable taker cross paid almost nothing (or went the wrong way after the hedge ask).

## 4. Ranked gap decomposition

### #1 `MID_VS_TOB_PRICE_SELECTION` (~83% / €-4925)

**Research books mid dislocation as capturable gross; shadow locks TOB taker cross**

B = notional × |mid_okx−mid_bitvavo| − 47bps costs (fill_prob=1). C = (fill_entry−fill_hedge)/mid after 10/50ms observe − costs×fill_frac. On this tape mean mid edge is hundreds of bps while mean TOB captured edge is ~0 or negative — follower books are wide; lifted ask sits near leader bid so the lockable cross is flat.

_Classification: honesty_

### #2 `NO_FILL_HAIRCUT` (~12% / €-712)

**Research still books full mid NET on candidates that shadow cannot fill**

NO_FILL rate=0.012392755004766444; shadow books €0, research keeps mid NET.

_Classification: realism_

### #3 `PARTIAL_FILL_HAIRCUT` (~5% / €-297)

**Research assumes full €100 notional; shadow scales costs/gross by fill_fraction**

PARTIAL_FILL rate=0.3841754051477598; FULL_FILL=611 PARTIAL=403.

_Classification: realism_

### #4 `FEE_MODEL` (~0% / €0)

**Fee/slip/adverse/latency rates match (35+2+8+2 bps) — not the bug**

Same FEE_RATE_ROUNDTRIP and buffers in protocol.py. Fee tweaks cannot flip sign while TOB captured gross ≤ 0.

_Classification: honesty_

## 5. Levers toward research (honest ranking)

| Lever | Realism | Expected effect on LIVE_SHADOW |
|-------|---------|--------------------------------|
| `ALIGN_RESEARCH_TO_TOB` — Make research expected use lockable TOB gross (honesty) | honesty | unchanged (~negative); closes reporting gap |
| `LOCKABLE_EDGE_GATE` — Only fire CVD when TOB (bid_rich−ask_cheap)/mid ≥ breakeven (~47bps) | realism | → ~€0 (few/no fires), not +€5k |
| `FOLLOWER_SPREAD_SANITY` — Reject signals when follower implied spread is extreme (e.g. >200bps) | realism | less negative / flatter; not research-scale profits |
| `PASSIVE_CAPTURE_EXPERIMENT` — Test maker/passive capture of dislocation instead of taker-taker lock | unproven | unknown — needs new shadow mode, not current C |
| `MID_ACCOUNTING_CHEAT` — Score shadow with mid edge (do not do for go-live) | cheat | artificially → +€k; unsafe for LIMITED_LIVE |
| `FEE_CUT_ONLY` — Lower fee/adverse buffers alone | cheat | still negative |

### Details

**ALIGN_RESEARCH_TO_TOB** (honesty)

RESEARCH_EXPECTED collapses toward shadow; stops the fake +€5.5k forecast

Files: `bot/research/shadow_validation/economics.py`, `bot/research/economic_parity/formulas.py`

**LOCKABLE_EDGE_GATE** (realism)

Rejects mid-only mirages; trade set shrinks sharply on this tape

Files: `bot/research/shadow_validation/detector.py`, `bot/paper/cvd_candidate.py`

**FOLLOWER_SPREAD_SANITY** (realism)

Data-quality filter; removes wide-book mid phantoms

Files: `bot/research/shadow_validation/detector.py`, `bot/research/shadow_validation/books.py`

**PASSIVE_CAPTURE_EXPERIMENT** (unproven)

Different execution alpha; may harvest mid gap if quotes rest inside

Files: `bot/live/micro_bridge_executor.py`, `bot/strategies/maker_inventory.py`

**MID_ACCOUNTING_CHEAT** (cheat)

Would print research-like positives; lies about executability

Files: `bot/research/shadow_validation/outcomes.py`

**FEE_CUT_ONLY** (cheat)

Cannot flip sign while captured TOB gross ≤ 0

Files: `bot/research/shadow_validation/protocol.py`

## 6. Path to positive LIVE_SHADOW?

No honest path from current mid≥40bps CVD + TOB taker shadow to research-like +€5k. Realistic path: (1) lockable-edge gate + spread sanity → flat/near-zero LIVE_SHADOW; (2) prove passive/maker capture in a new shadow mode; (3) only then LIMITED_LIVE. Do not re-score shadow on mids to 'match research'.

## 7. Implication for LIMITED_LIVE

Keep `live_cvd_limited_enabled=false` until either:

1. Lockable-edge + spread-sanity shadow prints **stable non-negative** C, or
2. A new passive-capture shadow mode is VALIDATED,

Bitvavo+OKX being up is necessary but irrelevant to this gap — venues were already in the shadow sample that produced −€416.

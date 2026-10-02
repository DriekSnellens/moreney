# Ignition lab notes

Desk-universe **classic** early-signal (quiet + brk20 + day≥+6% + vol≥2×) stays the strict path.
Loosening those classic gates alone adds trades and **cuts** PnL on the wet desk tape.

## Exit ablation (€2k book, next-open entry, desk universe)

| Exit | full (24-03→26-10) | 2026 YTD | last 180d |
|---|---:|---:|---:|
| trail 15% (old live) | +€690 | +€1,561 | +€1,975 |
| trail 12% | +€1,493 | +€1,977 | +€2,206 |
| trail 12% + ratchet 30%→10% | **+€1,790** | **+€2,070** | **+€2,307** |

Shipped classic exits: `trail_pct=0.12`, `trail_ratchet_arm_pct=0.30`, `trail_ratchet_pct=0.10`.

## Miss #2 — explosive starts (coil hybrid)

Explosive legs rarely fire on the classic brk20 day; day-0 fails brk20 / day_ret / vol.
Soft/coil mid-leg entries catch the run. Wet desk ablation (`HYBRID_ENTRY.json`):

| Mode | full PnL | notes |
|---|---:|---|
| classic-only (trail12+ratchet) | ~+€2.7k | rare same-day hit on explosive starts |
| **desk coil_hybrid + coil-trail 25%** | **+€6.8k** | ~21% desk / ~80% leg catch vs ~2% classic |

Coil gates (generic, no coin hardcodes): liquid + quieter + compress (5d span &lt; 0.5× 20d) + brk5 + day≥2.5% + vol≥1.2× + close_loc≥0.65.
Classic wins rank when both fire. Coil lots use `coil_trail_pct=0.25` (then shared ratchet).

Artifacts: `EARLY_ENTRY_GRID.json`, `HYBRID_ENTRY.json`, `EXPLOSIVE_CAPTURE.json`.

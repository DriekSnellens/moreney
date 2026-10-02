# Ignition lab notes

Desk-universe early-signal (quiet + brk20 + day≥+6% + vol≥2×) stays the entry.
Loosening day/vol/breakout gates adds trades and **cuts** PnL on the wet desk tape.

## Exit ablation (€2k book, next-open entry, desk universe)

| Exit | full (24-03→26-10) | 2026 YTD | last 180d |
|---|---:|---:|---:|
| trail 15% (old live) | +€690 | +€1,561 | +€1,975 |
| trail 12% | +€1,493 | +€1,977 | +€2,206 |
| trail 12% + ratchet 30%→10% | **+€1,790** | **+€2,070** | **+€2,307** |

Shipped defaults: `trail_pct=0.12`, `trail_ratchet_arm_pct=0.30`, `trail_ratchet_pct=0.10`.
Entry gates unchanged. Near-miss (one gate away) is diagnostics only — not an entry.

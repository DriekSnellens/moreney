# AlphaI gate lab — residual_full (100% alt)

asof `2026-10-03`  window `2026-09-19` → `2026-10-03`  book €20,000  
Real AlphaI sessions only (13 days). Wet next-open Bitvavo. Trail 10%, floor 3.5%, SMA50, cash when no alt.

## Verdict

| Variant | PnL | maxDD | Notes |
|---|---:|---:|---|
| **off** (no AlphaI) | **+€6,948** | 10.7% | Rode NEAR; highest PnL |
| **asof16 + intersect** | **+€1,944** | **2.6%** | Best *real* gate: lower DD, still green |
| asof07 + intersect / prefer / overlap | +€840 | 10.7% | Softer, but diverts day-1 NEAR→SOL |
| **override_gate ≥15–25%** | +€6,948 | 10.7% | Same as off here (NEAR excess ~60%) |
| asof07/13 **hard gate** | **−€1,004** | 10.7% | Worst — cash when winner ≠ pick |
| asof16 hard gate | €0 | 0% | No trades (too strict) |

**Best gate optimization on this sample:** use AlphaI as **intersect** (best residual name that is also a pick), with picks as-of **16:00 UTC** — not a hard 07:00 gate.

Hard gate at 07:00 is what hurts 100% alt. Soft modes at 07 help less than moving the as-of hour + intersect.

## Why

On 2026-09-19 07:00, RS winner was **NEAR** (excess +60%) but AlphaI picks were ETH/LINK/SOL/XRP.

- `gate` → cash (misses NEAR)
- `intersect` / `prefer` @07 → **SOL** (pick in residual list, weaker)
- `override_gate ≥15%` → keeps **NEAR** (strong RS exception)
- `intersect` @16 → different allow-set / path → much lower DD

FET was never an AlphaI pick. A hard gate would block FET; `override_gate` would **not** block a non-pick with excess ≥15%.

## Practical recommendation for 100% alt + AlphaI

1. **Preferred:** `intersect` + as-of **16:00** (or your last buy window that day) — best PnL/DD among gates that actually bind.
2. **If you must keep moonshot RS:** `override_gate` with a high floor (e.g. 20–25%) so only extreme excess bypasses AlphaI — accepts FET-like risk when RS is “too strong”.
3. **Avoid:** hard `requires_alphai_pick` gate at 07:00 on residual_full.

Reproduce:

```bash
.venv/bin/python -m bot.research.alphai_gate_lab
```

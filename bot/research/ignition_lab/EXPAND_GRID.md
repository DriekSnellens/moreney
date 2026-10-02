# Top-80 liquid entry×exit grid vs desk-16

220 combos (22 entries × 10 exits), wet next-open, €2k, Bitvavo 1d.
Expanded = desk-16 + top-80 liquid EUR. Exit candidates include shipped
`trail12 + ratchet30→10%` plus time-stops / tighter trails.

## Desk-16 baseline (shipped exits)

| Window | PnL | maxDD | trades |
|---|---:|---:|---:|
| full | +€2,658 | 25.6% | 9 |
| 2026 | +€2,081 | 14.8% | 3 |
| last 180d | +€2,317 | 10.3% | 2 |

## How top-80 *can* beat desk

**6 / 220** beat desk on the **full** window. **0 / 220** beat desk on *all*
three windows. Every full-window winner used **`quiet_max=0.15`** (live is 0.12).

| Combo | full | Δ vs desk | 2026 | 180d | outside PnL |
|---|---:|---:|---:|---:|---:|
| **quiet15 + t12/r30 + time14** | **+€5,074** | **+€2,416** | +€1,821 | +€2,500 | +€2,369 |
| quiet15 + trail 8% | +€4,043 | +€1,384 | +€1,383 | +€1,899 | +€928 |
| quiet15 + t12/r30 + time21 | +€3,551 | +€893 | +€1,531 | +€1,984 | +€2,068 |
| quiet15 + t12/r25→8 | +€3,442 | +€783 | +€1,561 | +€1,967 | +€1,849 |
| quiet15 + t12/r30 (no time) | +€3,164 | +€506 | +€1,561 | +€1,967 | +€1,680 |

Outside names that paid under the winner: ALGO, BCH, CRV, GRASS, KAS, ONDO, QNT, SEI, VET, VIRTUAL.

## What did *not* help top-80

- Live gates (`quiet12 + day6% + vol2`) on expanded — still below desk.
- Stricter vol (2.5×/3×), day≥8%, brk10, min_points 4–5, require xs15/trend/r3/expand — none beat desk on full.
- Looser day/vol without quieter raise — more trades, worse PnL.

## Tradeoff

`quiet15 + time14` wins full & 180d vs desk, but **loses 2026 YTD**
(+€1,821 vs desk +€2,081). No free lunch across all windows.

Raw: `EXPAND_GRID.json`. Re-run: `.venv/bin/python -m bot.research.ignition_lab.expand_grid`

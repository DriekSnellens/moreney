# AlphaI pack compare — real picks only

asof `2026-10-03`  window `2026-09-19` → `2026-10-03`  book €20,000  decision hour 07:00 UTC

AlphaI coverage: **13 days** (merged `pick_outcomes` + earlier tmp ledger), wet next-open Bitvavo fills. **No price-proxy AlphaI.** Days without picks stay empty until the first real session. Calmar is inflated on a ~2-week window — prefer raw PnL + maxDD.

## Hard constraint

FET was **never** an AlphaI pick in this overlap. A live AlphaI gate would have blocked the current FET residual entry.

## Winners on this overlap

| Goal | Pack | AlphaI | PnL | maxDD |
|---|---|---|---:|---:|
| Best PnL | `live_residual_full` (= `res100_sma_trail10`) | **off** | **+€6,948** | 10.7% |
| Best PnL with AlphaI on | `live_clip_20_80` / `btc50_res50_trail10` | **gate** | +€1,249 | **2.8%** |
| Least DD | BTC sleeve / AlphaI-gated mixes | gate | +€1,249 | **2.8%** |
| Best AlphaI *lift* | `btc50_res50_trail10` | gate vs off | **+€1,141** | **−6.0pp DD** |

## How to read it

1. **Ungated residual_full** won the short tape by riding **NEAR** early (not an AlphaI pick at 07:00 on entry day). Highest PnL, mid DD (~11%).
2. **AlphaI gate on residual_full hurt** here (−€1,004): it blocked that early NEAR chase and sat cash / late entries.
3. **AlphaI gate on 50/50 (or clip→BTC fallback) helped**: it refused a toxic alt (ARB) and kept the book on BTC → same +€1,249 / 2.8% DD as pure BTC, vs +€108 / 8.9% DD ungated 50/50.
4. There is **no pack that is both #1 PnL and #1 low-DD** on this window. Pareto front is residual_full/off (PnL) vs BTC / AlphaI-gated mixes (DD).

## Practical recommendation (given you want AlphaI)

- If priority is **least drawdown + AlphaI discipline**: `btc50_res50` or `clip_20_80` with **AlphaI gate** (and trail 10%). On this overlap that is the robust low-DD corner.
- If priority is **max PnL** and you accept RS chase risk: ungated `residual_full` — but that is exactly the path that can buy non-pick names like FET.
- Do **not** treat this 13-day overlap as a multi-year proof. It is the only fair AlphaI-conditioned sample we have on disk.

## Top packs (by short-window calmar; see JSON for full grid)

- `live_residual_full__ai_off`  +€6948  dd 10.7%
- `live_clip_20_80__ai_off`  +€5812  dd 9.2%
- `btc50_res50_trail10_reb1__ai_off`  +€2742  dd 6.3%
- `live_clip_20_80__ai_gate`  +€1249  dd 2.8%
- `btc50_res50_trail10__ai_gate`  +€1249  dd 2.8%

Reproduce:

```bash
.venv/bin/python -m bot.research.alphai_pack_compare --refresh
```

# Weekly setup lab — performance overview

Wet next-open Bitvavo taker, book **€20,000**, last bar **2026-10-02**.
Grid: 15 entry filters × 5 books × AlphaI modes (off / proxy / real on overlap).

**Calmar on ≤12w windows is annualized noise** — prefer raw PnL + maxDD there.
AlphaI proxy = causal short-excess×liquidity membership (not headlines).
Real AlphaI = `pick_outcomes` (~6 calendar days only).

## Verdict for the coming week

Recent regime (12w / 90d) rewards **forward filters** (RSI cap / extension cap /
setup score) plus **AlphaI intersect**, not naked RS#1 chase.

| Lever | Prefer | Avoid |
|---|---|---|
| Entry | `rsi_70`, `ext_sma_8`/`12`, `setup_score` | naked `rs_excess` after a vertical week |
| Book | `res100_trail10` or `res100_hold`; `res100_sma_trail10` still strong | — |
| AlphaI | `intersect` (real when available, else proxy) | hard `gate` that blanks the sleeve |

**As-of 2026-10-02 picks under those rules**

| Recipe | Pick | Notes |
|---|---|---|
| RS chase (baseline) | **NEAR** | +96% 20d excess, RSI 66, +21% above SMA20 — already extended |
| `ext_sma_8` / `setup_pb8_rsi65_ext10` | **UNI** | room under local high, RSI 54, +7% vs SMA20 |
| `rsi_70` ∩ real AlphaI | **SOL** | residual survivors ∩ AlphaI picks |
| `ext_sma_8` ∩ real AlphaI | **DOGE** | only liquid residual that clears both |

Recommended sleeve for the coming week: **UNI** on a residual book with trail,
or **SOL/DOGE** if AlphaI intersect is required. Do **not** chase NEAR on RS alone.

---

## fair_2y (2024-03-16 → 2026-10-02) — 225 packs

Long tape still likes the old champion: residual full + SMA50 + 10% trail + RS excess, AlphaI off.

| AlphaI | Best pack | PnL | Calmar | maxDD |
|---|---|---:|---:|---:|
| off | `res100_sma_trail10__rs_excess` | **+€410,891** | 7.55 | 30.9% |
| proxy_gate | `res100_sma_trail10__rs_excess` | +€265,920 | 5.00 | 36.8% |
| proxy_intersect | `res100_sma_trail10__rs_excess` | +€305,667 | 4.99 | 39.8% |

AlphaI lift on this window is **negative** for RS chase, but **positive** when the
entry is already a setup filter (`setup_score` + proxy_intersect: Δpnl +€107k).

Top packs: RS excess dominates; `setup_score` + proxy_intersect is the best forward variant (~+€161k, calmar 4.49).

## last_12w (2026-07-10 → 2026-10-02) — 225 packs

Forward filters + AlphaI proxy win.

| AlphaI | Best pack | PnL | Calmar | maxDD |
|---|---|---:|---:|---:|
| proxy_intersect | `res100_trail10__rsi_70` | **+€40,189** | 830* | 13.6% |
| proxy_gate | `res100_hold__ext_sma_12` | +€30,495 | 729* | 7.2% |
| off | `res100_hold__ext_sma_12` | +€34,196 | 322* | 22.2% |

\*short-window annualized Calmar — use PnL/DD.

Largest AlphaI lifts vs same entry×book off: `rsi_70` +proxy_intersect Δpnl **+€30.6k**;
`setup_score` on `res100_sma_trail10` Δpnl **+€25.4k**.

## last_90d (2026-07-04 → 2026-10-02) — 225 packs

| AlphaI | Best pack | PnL | Calmar | maxDD |
|---|---|---:|---:|---:|
| proxy_intersect | `res100_hold__ext_sma_8` | **+€32,731** | 499* | 9.6% |
| proxy_gate | `res100_hold__ext_sma_12` | +€29,156 | 458* | 7.8% |
| off | `res100_sma_trail10__rs_excess` | +€25,417 | 241* | 10.8% |

Same story: extension/RSI filters beat RS chase recently; proxy intersect adds PnL.

## alphai_overlap (2026-09-27 → 2026-10-02) — real AlphaI, 375 packs

Tiny sample (one weekly decision). Real intersect + RSI/ext filters tops Calmar
via lower DD (+€630, DD 0.4%) vs off (+€637, DD 1.4%).

| AlphaI | Best pack | PnL | maxDD |
|---|---|---:|---:|
| real_intersect | `res100_hold__rsi_70` (also ext_sma_*) | +€630 | 0.4% |
| off / proxy / real_gate | pullback / rs variants | +€637 | 1.4% |

---

## Focus head-to-head (entry × AlphaI, best book by PnL)

See `RESULTS.json` → `focus_compare` for the full matrix on
`{rs_excess, setup_score, pullback_8, rsi_70, ext_sma_8/12, setup_pb*}` ×
`{res100_*, clip20_80_trail10}` × `{off, proxy_intersect, real_intersect}`.

## How to reproduce

```bash
.venv/bin/python -m bot.research.weekly_setup_lab --refresh
```

Not armed live.

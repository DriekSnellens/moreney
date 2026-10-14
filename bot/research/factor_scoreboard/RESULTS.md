# Factor scoreboard — residual_full

asof `2026-10-14`. Wet Bitvavo next-open fills, book €20,000, desk 16, 2024-03-16 → 2026-10-14.
Baseline is the armed pack: lookback 10 skip 1, floor 3.5%, SMA50, cash when no alt, 7-day clock,
near-high 0.25, trail 10%, lock +12%/+6%. IS = through 2025-12-31 on the compounded book, OOS = a fresh
€20k book from 2026-01-01. Every overlay reads data through the signal close only.

Reproduce: `.venv/bin/python -m bot.research.factor_scoreboard`
(Fear & Greed from alternative.me, funding from Binance USDT perps; cached in `data/research/external/`).

## What the literature says, and what this tape says

| Claim (source) | Overlay tested here | Result vs baseline |
|---|---|---|
| Vol-managed momentum cuts crashes (Springer FMPM 2025; Nova SBE GARCH study) | realized-vol target 60–120% | PnL down at every target; DD only improves at 60% where PnL is less than half |
| Vol targeting on long/cash TSMOM is null (Howden & Andreev, SSRN 2026) | same grid | agrees: de-levers the payoff |
| De-risk ×0.5 when market >15% off peak (Howden & Andreev) | BTC 15/20/25% off 100d peak, half or cash | all lower PnL; 15% gate −70% |
| Cross-sectional dispersion predicts momentum breakdowns (Makgolo & Zhang) | causal 180d percentile ≥80/90, half or cash | all lower; cash at p90 turns OOS negative |
| Momentum only in greed, BTC in fear (Tickfloor 2017–2025, not significant after correction) | F&G ≥25/40/50 gate, soft 0.5→1 scale 20–60 | gates −40% to −85%; soft scale −24% PnL, DD 31→25 |
| Funding / OI signals fail after costs (Tickfloor; Stratmill SOL study) | pick funding z ≥1.5/2.0, abs ≥0.03%/8h, BTC funding z | neutral to −5%; BTC funding gate −85% |
| Large coins trend, small coins reverse (Xu & Wu) | 80-name Bitvavo pool at €1M–€10M qvol | near zero or negative at every floor; the 16-name desk is the only set that trends |
| Time-series momentum beats cross-sectional (Vilnius 2025) | own N-day return > 0 on the pick | 25–30d +10–23%, 15–20d −20%, 45+d −10% to −55%: a sharp parameter, three trades decide it |
| Donchian ensembles across lookbacks (Zarattini et al.) | mean excess over 5/10/20, 10/20, 10/20/40 | all lower, −28% to −75% |

## Full table

| Variant | Family | Full PnL | Full DD | IS PnL | IS DD | OOS PnL | OOS DD |
|---|---|---:|---:|---:|---:|---:|---:|
| baseline | | +393,195 | 31.2% | +151,798 | 24.8% | +36,575 | 26.7% |
| fng_gate25 | sentiment | +242,326 | 38.1% | +151,798 | 24.8% | +13,421 | 26.6% |
| fng_gate40 | sentiment | +63,669 | 32.1% | +36,341 | 24.8% | +10,040 | 24.0% |
| fng_gate50 | sentiment | +46,735 | 39.3% | +19,707 | 39.3% | +13,813 | 24.1% |
| fng_soft20_60 | sentiment | +300,340 | 24.8% | +128,132 | 24.8% | +26,749 | 24.1% |
| fund_z1.5 | positioning | +390,136 | 31.2% | +151,798 | 24.8% | +36,627 | 26.7% |
| fund_z2.0 | positioning | +393,195 | 31.2% | +151,798 | 24.8% | +36,575 | 26.7% |
| fund_abs0.03 | positioning | +372,510 | 31.0% | +142,091 | 24.3% | +36,575 | 26.7% |
| btcfund_z1.5 | positioning | +56,199 | 40.0% | +34,010 | 40.0% | +9,572 | 30.4% |
| vol_t60 | sizing | +170,935 | 23.0% | +60,911 | 18.0% | +30,504 | 22.0% |
| vol_t80 | sizing | +279,182 | 28.5% | +95,058 | 21.1% | +38,322 | 26.7% |
| vol_t100 | sizing | +351,537 | 28.9% | +124,707 | 23.2% | +39,177 | 26.8% |
| vol_t120 | sizing | +374,211 | 30.8% | +142,318 | 24.6% | +36,751 | 26.7% |
| dd15_half | regime | +283,666 | 26.6% | +103,165 | 26.6% | +32,897 | 24.1% |
| dd20_half | regime | +355,830 | 29.0% | +151,798 | 24.8% | +29,002 | 24.1% |
| dd15_gate | regime | +116,971 | 41.9% | +70,577 | 38.3% | +10,951 | 24.0% |
| dd25_gate | regime | +382,010 | 29.1% | +151,798 | 24.8% | +32,471 | 26.7% |
| disp_gate90 | regime | +95,504 | 37.4% | +120,950 | 24.8% | −1,705 | 30.9% |
| disp_gate80 | regime | +162,446 | 26.7% | +77,693 | 24.8% | +20,296 | 23.8% |
| disp_half90 | regime | +332,576 | 25.7% | +115,485 | 24.8% | +38,735 | 24.1% |
| breadth40 | regime | +102,776 | 34.8% | +91,085 | 23.7% | +3,986 | 30.4% |
| breadth50 | regime | +107,942 | 35.0% | +96,120 | 23.8% | +3,986 | 30.4% |
| tsmom15 | trend | +306,602 | 39.9% | +116,843 | 35.6% | +34,023 | 30.0% |
| tsmom20 | trend | +312,293 | 40.0% | +119,488 | 35.6% | +34,023 | 30.0% |
| tsmom25 | trend | +485,476 | 34.1% | +203,387 | 25.1% | +34,023 | 30.0% |
| tsmom30 | trend | +435,118 | 31.6% | +168,434 | 26.5% | +36,897 | 26.7% |
| tsmom35 | trend | +384,105 | 31.1% | +144,790 | 30.9% | +36,897 | 26.7% |
| tsmom40 | trend | +389,253 | 31.2% | +149,941 | 30.9% | +36,575 | 26.7% |
| tsmom45 | trend | +345,433 | 35.0% | +149,941 | 30.9% | +28,818 | 26.6% |
| tsmom50 | trend | +229,168 | 43.5% | +83,083 | 40.3% | +32,008 | 26.7% |
| tsmom60 | trend | +214,081 | 37.1% | +111,278 | 37.1% | +18,088 | 24.1% |
| tsmom90 | trend | +177,712 | 51.2% | +96,458 | 45.7% | +15,711 | 24.1% |
| cooldown1 | clock | +236,153 | 46.2% | +116,990 | 32.6% | +22,961 | 34.1% |
| cooldown2 | clock | +31,566 | 60.7% | +15,796 | 49.3% | +9,814 | 34.6% |
| cooldown3 | clock | +71,890 | 59.6% | +2,456 | 56.0% | +62,485 | 23.5% |
| cooldown5 | clock | +58,556 | 38.1% | +41,751 | 35.2% | +7,264 | 29.6% |
| btc_fallback | clock | +310,735 | 37.7% | +127,076 | 29.0% | +31,896 | 26.7% |
| ens5_10_20 | rank | +148,383 | 35.9% | +37,468 | 35.9% | +42,207 | 26.7% |
| ens10_20 | rank | +283,187 | 30.7% | +125,935 | 27.2% | +27,903 | 26.7% |
| ens10_20_40 | rank | +198,118 | 30.1% | +81,191 | 29.0% | +27,903 | 26.7% |

Universe (start 2024-06-20 so the 80-name pool has SMA50 history; desk baseline on that window +367,649 / 31.0%):

| Universe | Full PnL | Full DD |
|---|---:|---:|
| desk 16, qvol ≥ €80k | +367,649 | 31.0% |
| desk 16, qvol ≥ €5M | −12,796 | 71.4% |
| 80-name pool, qvol ≥ €1M | +19,670 | 76.1% |
| 80-name pool, qvol ≥ €2M | +5,762 | 59.5% |
| 80-name pool, qvol ≥ €5M | −11,439 | 62.8% |
| 80-name pool, qvol ≥ €10M | −734 | 63.0% |
| only the 64 non-desk names, qvol ≥ €5M | −9,911 | 49.8% |

Leave-one-out on the desk (same window): dropping any single name moves the full path between +69k
(without SUI) and +432k (without ARB). The compounded headline rests on a handful of rides: SUI, NEAR,
DOGE, FET, XRP. That concentration is the property of a 100%-one-name compounding book, not of a
specific coin; it is why entry filters that touch two or three trades swing the total by hundreds of
thousands and why none of them is taken as evidence.

## Verdict

1. No sentiment, positioning, regime, sizing, breadth, ensemble, re-entry or universe overlay beats
   the armed pack on both the 2024–25 book and the fresh 2026 book. Nothing from this grid is armed.
2. `tsmom25/30` is the only variant above baseline on the full path. It is a sharp parameter (15–20 and
   45+ are far worse) and its whole gain is three skipped entries. Not armed. Re-test when the tape has
   another year.
3. The only robust levers remain the ones already live: the excess floor, own SMA50, the 7-day clock,
   the 10% trail, the +12%/+6% lock, and the near-high rank. Shorter re-entry after an exit is the
   worst family in the grid; keep the week in cash.
4. Fear & Greed and funding are available as causal daily series now (`external.py`). They did not
   earn a place in the residual sleeve. They remain candidates for the news/momentum sleeve once that
   sleeve has a long enough pick history to test against.

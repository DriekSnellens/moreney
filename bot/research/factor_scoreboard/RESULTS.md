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

## Participation grid (2026-10-14): can the desk take more of the market?

Question: the market shows more opportunities than the desk takes; does holding more names,
re-ranking faster or lowering the floor capture them? Same windows and fill model as above
(full 2024-03-16 → 2026-10-14, IS → 2025-12-31, OOS 2026). Baseline = the live pack.

| Variant | Full PnL | Full DD | IS PnL | OOS PnL | trades |
|---|---:|---:|---:|---:|---:|
| **baseline (1 alt, 7d clock, floor 0.035)** | **+393,195** | 31.2% | +151,798 | +36,575 | 98 |
| 2 alts (half size each) | +30,795 | 41.8% | +14,830 | +9,777 | 166 |
| 3 alts | +20,551 | 34.4% | +12,027 | +5,697 | 196 |
| 4 alts | +17,326 | 31.8% | +9,803 | +5,346 | 220 |
| 2 alts, floor 0.02 / 0.05 | +22,105 / +19,055 | 44–47% | +9.8k / +7.2k | +8.7k / +9.1k | 178 / 158 |
| 3 alts, floor 0.05 | −1,510 | 46.1% | +4,818 | −5,003 | 192 |
| clock 1d | +21,154 | 79.9% | −3,201 | +27,871 | 337 |
| clock 2d | +8,569 | 67.1% | −2,690 | +12,588 | 226 |
| clock 3d | +23,512 | 61.9% | −4,146 | +33,838 | 180 |
| clock 5d | +102,754 | 34.7% | +22,745 | +40,544 | 119 |
| clock 10d | −2,178 | 52.4% | −3,695 | +1,745 | 78 |
| clock 14d | +32,876 | 43.8% | +19,800 | +7,089 | 59 |
| 2 alts, 5d / 3 alts, 5d | +34,376 / +61,236 | 37% | +6.4k / +26.7k | +21.7k / +16.4k | 196 / 235 |
| floor 0.00 / 0.02 / 0.05 (1 alt) | +288k / +323k / +381k | 33–40% | +132k / +152k / +152k | +26.8k / +26.8k / +34.0k | 106 / 102 / 96 |

Reading: every way of "taking more" (more names, faster clock, lower floor) takes less. Splitting
the book over two names cuts the full path by 92%; a daily or 3-day clock turns the 2024–25 book
negative with 60–80% drawdowns. Concentration in one name on a weekly clock *is* the edge: the
return comes from riding the single strongest name for weeks, and every dilution or extra re-rank
sells it early. The clock is also sharp (5d +103k, 7d +393k, 10d −2k on the full path), which is
the known path-dependence of a one-name compounding book; on the fresh 2026 book clocks 1–7d are
all positive (+28k…+41k) and 10–14d are the weak ones. Nothing here is armed; the 7-day clock stays.

The capacity that is left on the table is not in the ranking. On 2026-10-14 the live desk ranked
ATOM first (excess +13.5% vs BTC over 10d, score 0.143 vs floor 0.035) with €0 EUR available; the
modelled 2026 book is +€36.6k while live realized is −€1.1k. Funding the desk on every signal is
the lever; widening the net is not.

## Leverage ladder (2026-10-14): is the armed pack "optimal" in a bull, and what does leverage add?

Reproduce: `.venv/bin/python -m bot.research.factor_scoreboard.leverage`. The baseline's 49 round
trips (wet next-open fills) are re-priced as a USDT-margined perp long at leverage L: 0.05% fee +
0.05% slip per side instead of the 0.25% spot fee, funding paid on the full notional (Binance
daily-mean proxy, 3 settlements/day), cross-margin liquidation when the worst intraday low of a
hold breaches 1/L − 2%. Exits are unchanged (10% trail, +12%/+6% lock), so this measures leverage
on the pack *as armed*, not a re-optimised leveraged pack. The spot 1x reconstruction (+€398k)
matches the engine (+€393k).

Compounded €20k from 2024-03-16:

| variant | total | maxDD | 2024 | 2025 | 2026 | liq | worst intraday | funding | fees |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| spot 1x (live) | +398,025 | 31.2% | +134,279 | +19,038 | +244,708 | 0 | −25.9% | 0 | 34,213 |
| perp 1x | +440,172 | 29.7% | +137,842 | +24,033 | +278,297 | 0 | −25.9% | 5,145 | 14,473 |
| perp 1.25x | +769,255 | 35.8% | +215,331 | +39,387 | +514,538 | 0 | −25.9% | 9,382 | 27,475 |
| perp 1.5x | +1,243,853 | 41.5% | +320,062 | +58,723 | +865,068 | 0 | −25.9% | 15,971 | 48,333 |
| perp 2x | +2,692,097 | 52.0% | +636,855 | +101,327 | +1,953,915 | 0 | −25.9% | 39,116 | 125,371 |
| perp 3x | +6,820,475 | 69.1% | +1,903,207 | +24,829 | +4,892,439 | 0 | −25.9% | 137,796 | 499,398 |
| perp 5x | −19,269 | 99.9% | +330,520 | −342,836 | −6,954 | 3 | −25.9% | | |

Fresh €20k each calendar year (positions straddling a year boundary are skipped, so 2025 reads
+2.5k here vs +4.2k in the full engine):

| variant | 2024 | DD | 2025 | DD | 2026 | DD |
|---|---:|---:|---:|---:|---:|---:|
| spot 1x | +134,279 | 24.9% | +2,468 | 24.5% | +28,238 | 31.2% |
| perp 1x | +137,842 | 24.9% | +3,045 | 23.6% | +30,603 | 29.7% |
| perp 1.25x | +215,331 | 30.0% | +3,347 | 28.8% | +37,459 | 35.8% |
| perp 1.5x | +320,062 | 34.8% | +3,454 | 33.7% | +43,385 | 41.5% |
| perp 2x | +636,855 | 43.4% | +3,085 | 42.9% | +51,542 | 52.0% |
| perp 3x | +1,903,207 | 57.6% | +258 | 58.9% | +50,229 | 69.1% |

Reading:

1. **Perps at 1x already beat spot**: the long leg costs 0.10% a side instead of 0.25% and the
   funding paid (~€5k over 2.6y) is a third of the fee saving (~€20k). +€440k vs +€398k, lower DD.
   This is a venue question (OKX USDT-margined, EUR/USDT exposure, different depth), not a
   strategy question.
2. **Leverage is a bull-only multiplier.** 1.5x turns 2024 from +€134k into +€320k and lifts DD
   from 25% to 35%; in 2025 it adds nothing (+€3.5k vs +€2.5k) while DD goes 24% → 34%. 2x–3x
   compound to numbers that the wet fill model cannot support (clips of €1M+ in desk alts) and
   carry 43–69% drawdowns. 5x is liquidated three times.
3. The worst intraday excursion in any hold was −25.9%; the liquidation line is −31% at 3x and
   −23% at 4x. The 10% close-to-close trail does not protect against gaps, so anything above 2x
   is one bad night from zero.
4. On "optimal in a bull": among ~1,400 packs and ~45 overlays no setting beats the armed pack
   robustly; the untested dimensions were venue (perps) and leverage, and both are now measured.
   If anything is ever changed for the bull case it is 1.25–1.5x on perps with the same exits,
   gated by a maximum intraday-gap budget — after a live paper period on OKX. Not armed.

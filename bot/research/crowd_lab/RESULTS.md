# Crowd lab: can the growth of look-alike (AI-built) bots be traded against?

Run: `.venv/bin/python -m bot.research.crowd_lab` (2026-10-14). Bitvavo 4h bars
2024-07-01 to 2026-10-14 on the desk universe (BTC + 16 alts) and 1d bars
2024-03-16 to 2026-10-13 on every EUR market with median turnover >= EUR 200k (the
"liquid" filter, no per-coin lists). EUR 20k book, fixed notional `book / max_pos`
per position, no compounding. Costs: spot 0.25% fee + 0.10% slippage per side;
perp 0.05% + 0.05% per side plus actual Binance 8h funding (six majors; other
bases use the cross-sectional mean). Signal on bar close, fill at next open,
stops and targets intrabar. IS = through 2025-12-31, OOS = 2026.

A `maxDD` above 100% means the summed losses on fixed notional exceeded the book:
that strategy would have been wiped out before its later gains.

## 1. What the crowd actually runs (sources, 2026-10-14)

| Fact | Source |
| --- | --- |
| 60-80% of major-CEX volume is automated (industry estimate, not a market-wide study); 55.96% of tracked Base DEX volume in Aug 2026 was MEV/arbitrage; two bot clusters made 65% of Avalanche DEX volume in Sep/Oct 2026 | Traders Union / ClearTrace; Bitquery; Neutralis (HyroTrader) |
| Retail bot defaults: RSI-14 with 30/70, MACD 12/26/9, EMA/SMA crosses, Bollinger, grid 10 levels / 5% range, DCA ladders; "90% of bots use `stoploss = -0.10`" | Origami Tech presets, DennTech, backtest-kit "second-order chaos" |
| LLM agents on shared retrieval herd: pairwise action correlation 0.04 -> 0.41 as information overlap rises; synchronized orders deplete depth and fatten tails; a live 21-day on-chain deployment showed attention and exit cascades even with one model | ICLR 2026 (RAG herding); Yang 2026 SSRN; DX Terminal Pro paper 2604.26091 |
| Liquidation-cascade fades: alt overshoot is mostly BTC beta (regression alpha p = 0.18); historical liquidation feeds are too short to validate; sub-1% fades are fee-destroyed (commission 121% of gross) | tigroblanc (Medium); Stratmill reviews; Curupira (SOL/ETH survived walk-forward, BTC did not) |
| Funding extremes: negative funding is a coin flip on 6 majors (789 days); top-decile funding preceded weakness only on BTC/SOL and more upside on alts; cross-sectional carry OOS Sharpe 0.39; extreme-negative-funding reversal is conditional on an up day (+1.88% vs -1.74%) | markettrace.ai; Alpha Research paper 2; Shek 2026 SSRN; AIJMR 2026 |
| Clock effects: 16-17 UTC activity peak, 21 UTC depth trough, Monday 10 UTC bid imbalance with zero return correlation; quarter-hour algorithmic burst worth ~0.5 bp; funding-settlement drift unstable over 180 days; no robust weekday seasonality | RQFA 2025 "tea time"; Amberdata; arXiv 2607.09426; Kaleb0719 event study; Mueller 2024 FRL |

Reading: the crowd is real and homogeneous (same indicators, same stops, same
clocks), and the academic work says homogeneity produces synchronized flow, exit
cascades and fatter tails. That is the raw material. Whether it is harvestable on
Bitvavo EUR bars is the question below.

## 2. PnL table (EUR 20k, fixed notional, net of costs)

### 2a. What the crowd's default bots earn on this tape

| Strategy | 2024 | 2025 | 2026 | Full | maxDD | n | win | IS | OOS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RSI-14 30/70, -10% stop, 4h | -4,969 | -8,076 | -5,701 | **-18,746** | 135% | 262 | 40% | -13,045 | -5,701 |
| EMA 9/21 cross, -10% stop, 4h | 11,846 | -24,527 | 2,891 | **-9,790** | 206% | 612 | 25% | -12,681 | 2,891 |
| EMA 20/50 cross, -10% stop, 4h | 20,955 | -23,958 | -7,017 | **-10,021** | 223% | 300 | 21% | -3,003 | -7,017 |
| RSI-14 30/70, -10% stop, 1d | 2,264 | -11,039 | 3,026 | **-5,749** | 82% | 105 | 20% | -8,775 | 3,026 |
| EMA 20/50 cross, -10% stop, 1d | -5,834 | 14,735 | 8,177 | **17,078** | 105% | 59 | 22% | 8,901 | 8,177 |
| Golden cross SMA50/200, liquid EUR markets, 1d | 0 | -228 | 1,433 | **1,204** | 183% | 33 | 24% | -228 | 1,433 |
| Grid bot BTC 10 x 1%, EUR 1k/level, 4h | -2,520 | -4,204 | -2,453 | **-9,177** | 48% | 1,452 fills, 43 resets | | | |
| Grid bot ETH 10 x 1% | -2,977 | -11,048 | -4,810 | **-18,835** | 94% | 2,216 fills, 77 resets | | | |
| Grid bot SOL 10 x 1% | -6,198 | -13,129 | -4,581 | **-23,925** | 123% | 2,713 fills, 93 resets | | | |

The crowd loses. Every default preset is negative or ruined on the way; the one
positive line (daily EMA 20/50) gives back more than the book in 2024 before its
2025/2026 trend run. Fees alone are EUR 5-15k per strategy. This is the first
answer: the growth of look-alike bots is a transfer *from* them, provided one is
not running the same presets.

### 2b. Second-order plays: trading the crowd's footprints

| Strategy | Mechanism | 2024 | 2025 | 2026 | Full | maxDD | n | win | IS | OOS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Flush fade, alt bar <= -6% on volume z >= 2, hold 18 x 4h, stop 6% | buy forced selling / exit cascades | 2,777 | 1,115 | 3,535 | **7,427** | 29% | 131 | 44% | 3,892 | 3,535 |
| ... hold 24 bars | | 5,257 | -2,756 | 6,731 | 9,232 | 40% | 129 | 43% | 2,501 | 6,731 |
| ... stop 4% | | 4,930 | 1,750 | 4,127 | 10,807 | 28% | 133 | 40% | 6,680 | 4,127 |
| ... volume z >= 3 | | 3,602 | 2,138 | 2,163 | 7,903 | 21% | 90 | 47% | 5,740 | 2,163 |
| ... drop 5% instead of 6% | | 2,370 | -5,731 | -268 | **-3,629** | 77% | 205 | 37% | -3,361 | -268 |
| ... drop 7% / 8% | | -135 / 525 | 694 / -423 | 927 / 813 | 1,487 / 915 | 36% / 22% | 85 / 59 | | | |
| ... with BTC > SMA50 gate | | -1,557 | -1,261 | 5,331 | 2,514 | 24% | 47 | 45% | -2,817 | 5,331 |
| Flush fade BTC only, -3% bar | | -123 | -388 | -455 | -966 | 7% | 22 | 36% | -511 | -455 |
| RSI<30 buy, plain 18-bar time exit, spot 4h | the crowd's entry without its exit | -3,565 | 2,606 | 2,495 | 1,537 | 52% | 330 | 48% | -958 | 2,495 |
| RSI<30 buy, 18-bar time exit, perp 4h | | -2,180 | 6,052 | 5,663 | **9,535** | 40% | 330 | 49% | 3,872 | 5,663 |
| RSI<30 buy, 42-bar time exit, perp 4h | | -1,300 | -7,869 | -5,784 | -14,953 | 105% | 223 | 38% | -9,169 | -5,784 |
| Short the first green bar after RSI<30 below SMA50 (perp, 6 bars) | fade the bot bid | -1,982 | -3,713 | -1,719 | -7,414 | 50% | 665 | 47% | -5,695 | -1,719 |
| RSI reclaim 30 + prior-high break, spot 4h / 1d | confirmed oversold | -6,554 / 957 | -9,841 / -2,673 | -9,358 / -495 | -25,754 / -2,211 | 158% / 37% | 312 / 83 | 26% / 25% | | |
| Golden cross long, desk, 20-day hold, spot | ride the crowd's cross | 13,065 | -460 | 1,150 | 13,755 | 34% | 32 | 47% | 12,604 | 1,150 |
| Death cross SMA50/200 short, desk, 20-day hold, perp | follow the crowd's exit | 30 | 6,009 | 603 | 6,642 | 20% | 27 | 67% | 6,039 | 603 |
| Death cross SMA20/50 short, desk, 10-day hold, perp | | 2,056 | 4,180 | 5,416 | **11,653** | 42% | 118 | 52% | 6,236 | 5,416 |
| Stop-run buy: fresh -10..-16% break from 20d high above SMA50, liquid 1d | buy where the -10% stops fire | 9,854 | -10,442 | -10,944 | -11,531 | 194% | 393 | 23% | -587 | -10,944 |
| ... -8..-12% band, 5-day hold | | -3,294 | -8,846 | -2,785 | -14,925 | 133% | 465 | 32% | -12,140 | -2,785 |
| Pump fade: short the day after >= +15% on volume z >= 3, 3-day hold, perp | fade the pile-in | -2,060 | 4,331 | 290 | 2,561 | 16% | 56 | 57% | 2,271 | 290 |
| ... >= +10%, z >= 2, 2-day hold | | -7,575 | 4,746 | -1,441 | -4,271 | 49% | 146 | 51% | -2,829 | -1,441 |
| Cascade: median alt <= -5%, long 3 hardest hit, 2 days | post-cascade bounce | -6,338 | -24,427 | -4,467 | -35,233 | 196% | 210 | 40% | -30,765 | -4,467 |
| ... BTC on the same days (beta control) | | -3,858 | -4,478 | -2,552 | -10,889 | 62% | 70 | 40% | -8,336 | -2,552 |
| ... median alt <= -8% | | -5,027 | -7,259 | 3,676 | -8,609 | 82% | 90 | 43% | -12,286 | 3,676 |
| Funding top decile -> short 6 / 18 bars, perp | fade crowded longs | -7,562 / -4,266 | -2,423 / -4,784 | -3,466 / 840 | -13,452 / -8,210 | 75% / 49% | 346 / 184 | | | |
| Funding bottom decile -> long 6 / 18 bars, perp | squeeze the crowded shorts | -1,801 / -771 | -14,014 / -11,871 | -6,941 / -9,251 | -22,756 / -21,893 | 130% / 124% | 763 / 415 | | | |
| Funding top decile -> long 18 bars, perp | go *with* the crowded longs | 6,787 | 2,180 | -2,302 | 6,665 | 40% | 179 | 41% | 8,967 | -2,302 |
| 00:00 UTC bar long every day (desk) | daily-close bot clock | -15,862 | -55,760 | -41,213 | -112,836 | 568% | 14,153 | 34% | | fees 83k |
| Monday long every week (desk) | weekly rebalancing bid | -9,931 | -17,685 | -4,215 | -31,831 | 161% | 2,294 | 41% | | fees 13k |

### 2c. Event studies (gross, next-open entry; the "any bar" row is the baseline)

| Event | n | +6 bars (1d) | +18 bars (3d) | +42 bars (7d) |
| --- | ---: | ---: | ---: | ---: |
| any 4h bar, desk | 84,393 | +0.03% | +0.09% | +0.27% |
| RSI-14 < 30 | 1,296 | +0.13% | **+1.16%** (hit 56%, t 4.8) | +0.35% |
| RSI-14 > 70 | 1,332 | +0.79% | **+1.55%** (t 5.7) | +1.48% |
| funding top decile | 547 | +0.49% | **+1.85%** (t 4.9) | |
| funding bottom decile | 1,184 | +0.03% | -0.06% | |

| Event (1d, liquid EUR markets) | n | +5d | +10d | +20d |
| --- | ---: | ---: | ---: | ---: |
| any day | 69,915 | -0.87% | -1.86% | -3.68% |
| golden cross 50/200 | 220 | +1.46% | +1.09% | -0.99% |
| death cross 50/200 | 137 | -1.36% | -5.00% | **-9.71%** (hit 23%, t -5.9) |

Gross mean return per 4h slot (desk): 00 UTC +0.03%, 04 +0.00%, 08 -0.01%,
12 -0.03%, 16 -0.02%, 20 +0.02%; none clears a single side of spot costs.
Weekday (desk, 1d): Tue -0.45% and Thu -0.48% (t about -5), Wed/Fri/Sat +0.3%;
the t-stats are inflated by 16 correlated names and 2.5 years, and the literature
finds no stable weekday effect, so this is noted, not traded.

## 3. Reading

1. **The crowd's own presets lose on this tape.** RSI 30/70 with a -10% stop,
   EMA crosses and grids are -EUR 6k to -24k each over 2.3 years on a 20k book,
   mostly fees plus the -10% stop firing in normal alt noise. The simplest way to
   "profit from the bots" is to not be one: the live residual pack earns on the
   same bars (+EUR 393k compounded, +EUR 152k IS / +37k OOS on 20k) because its
   parameters come from a search over ~1,400 packs, not from a tutorial.
2. **Oversold is not a reversal, overbought is not a top.** After RSI<30 the
   desk returns +1.2% over three days gross, but after RSI>70 it returns +1.6%,
   and after top-decile funding +1.9%. Momentum continuation dominates; every
   "fade the crowd" variant (short the RSI bounce, short crowded funding, short
   pumps at 10%) loses. Going *with* the crowded tail is positive in-sample and
   negative out-of-sample (funding-top long: +9.0k IS, -2.3k OOS).
3. **Forced flushes are the only footprint that paid, and it is thin.** Buying
   a -6% alt bar on abnormal volume and holding 3 days made +EUR 7.4k (IS +3.9k,
   OOS +3.5k, positive each year, maxDD 29%), robust to the volume threshold
   (z 1-3 all positive), stop (4-10%) and hold (12-36 bars). But the drop
   threshold is a cliff: -5% bars turn it into -3.6k and -7% bars into +1.5k,
   so the money sits in a one-percentage-point band of 46 trades. That is a
   sample, not an edge. Cascade buying of the hardest-hit names is pure beta
   (the BTC control loses on the same days), as the external study found.
4. **Death-cross shorts are the best-behaved line (+EUR 11.7k, positive every
   year, IS +6.2k / OOS +5.4k, 118 trades)**, but it is trend following with the
   crowd's exit, not against it, and it needs OKX perps. On the desk it earns
   about EUR 4-5k a year on 20k with a 42% fixed-notional drawdown; the bear
   sleeve lab already showed outright alt shorts are phase-fragile.
5. **Clocks give nothing.** The 00:00 UTC slot (where daily-close bots, this
   desk included, act) is +3 bp gross; one-bar holds burn EUR 83k in fees.
6. **What the survey says works is not backtestable here.** Being the
   counterparty to forced flow needs a liquidation feed and sub-minute
   execution (Curupira's cascade fade survives walk-forward only on SOL/ETH, and
   only with that feed); market making profits from bot taker flow but needs
   book data and inventory risk management. Both are daytrader-stack work
   (`factor_scoreboard/RESULTS.md`, tech-stack section), not a sleeve on daily
   bars.

## 4. Verdict

- No second-order "crowd" strategy comes near the live pack. Best candidates on
  EUR 20k: death-cross short +EUR 4-5k/yr (perps), flush fade +EUR 3k/yr, RSI<30
  3-day perp hold +EUR 4k/yr but negative in 2024 and ruined at a 7-day hold.
  Each has a cliff in one parameter; none is armed.
- The actionable way to "play" the bot wave is defensive and generic: keep
  parameters that are not the tutorial defaults (already the case), keep exits
  on daily closes with no resting stops (nothing to hunt), measure live fill
  quality against the replay (decay monitor), and let the cash rule absorb the
  synchronized exit cascades the herding literature predicts.
- If a satellite is ever wanted, the flush fade is the one worth a paper book:
  it is the only line positive in 2024, 2025 and 2026 and in both IS and OOS
  without leverage or shorts. It should earn its place on a forward sample
  before any live sizing.

Sources: Traders Union / ClearTrace (Aug 2026); Bitquery Avalanche investigation
(Oct 2026); Neutralis "Crypto markets: 60-80% algorithmic"; backtest-kit
"second-order chaos"; Origami Tech grid presets; ICLR 2026 "Information
Homogenization Induces Herding in RAG LLM Agent Markets"; Yang, "Correlated
Beliefs, Correlated Trades" (SSRN 7389159); arXiv 2604.26091 (DX Terminal Pro);
arXiv 2604.18602 "Machine Spirits"; tigroblanc, "Chasing liquidation cascade
alpha"; Curupira "Fading liquidation cascades"; Stratmill strategy reviews;
markettrace.ai "What happens after extreme funding rates"; Alpha Research
"Crypto Carry: the funding-rate cross-section"; Shek, "Conditional Arbitrage
Capacity" (SSRN 7520320); AIJMR 2026 extreme funding and BTC returns; RQFA 2025
"The crypto world trades at tea time"; Amberdata "The Rhythm of Liquidity";
arXiv 2607.09426 "The Quarter-Hour Effect"; Mueller 2024 "Revisiting
seasonality in cryptocurrencies".

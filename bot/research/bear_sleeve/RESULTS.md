# Bear-market sleeve lab (2026-10-14)

Question from the desk: 2025 was a terrible year for the alt book. The RS sleeve
(`residual_full`) is a long-only trend sleeve that sits in cash when nothing beats BTC
by the floor. Is there a *second* sleeve that is structurally profitable when the market
falls, so the book earns in both regimes?

Reproduce: `.venv/bin/python -m bot.research.bear_sleeve` (writes
`artifacts/bear_sleeve_lab.json`). Universe = the 16 desk alts + BTC, EUR 20k book,
daily bars, 2024-03-16 → 2026-10-14. Spot legs pay 0.25% + 0.10% slip per side, perp
legs 0.05% + 0.05% plus funding (Binance daily-mean proxy, three settlements a day;
positive funding is income for the short). Stops act on the next open.

## Regimes

| year | BTC | desk-alt median | BTC < SMA50 | RS sleeve (fresh €20k) |
|---|---|---|---|---|
| 2024 (from 03-16) | +50.7% | +9.2% | 107 / 291 d | +€132,986 (DD 24.8%) |
| 2025 | −18.3% | −68.2% | 212 / 365 d | +€4,180 (DD 22%) |
| 2026 (to 10-14) | −2.6% | −14.1% | 123 / 287 d | +€36,575 |

The RS sleeve already did its bear-market job in 2025: +21% while the median desk alt
lost two thirds. Whatever a bear sleeve adds has to be judged against "cash".

## Family 1: outright shorts (perps; not possible on Bitvavo spot)

Single clock, weekly rebalance, 10% hard stop, book €20k:

| spec | 2024 | 2025 | 2026 | total | maxDD |
|---|---|---|---|---|---|
| short BTC when BTC < SMA50 | −3,136 | +1,301 | +1,473 | −361 | 30.5% |
| short BTC when BTC < SMA20 | −5,395 | −532 | +1,527 | −4,400 | 39.8% |
| short weakest 1, gate SMA20, L15 | −10,156 | +5,467 | +8,742 | +4,052 | 36.3% |
| short weakest 1, gate SMA20, L10 | −6,962 | +10,920 | +7,945 | +11,903 | 32.2% |
| short weakest 2, gate SMA20, L15 | −6,052 | +4,007 | +5,110 | +3,066 | 33.1% |
| short weakest 2, gate SMA20, half size | −2,808 | +3,520 | +3,292 | +4,004 | 19.4% |
| short weakest 2, gate SMA50, L15 | −8,651 | −2,903 | +2,135 | −9,419 | 69.8% |
| short weakest 2, gate SMA100, L15 | −8,815 | +6,216 | +13,137 | +10,538 | 31.5% |
| short weakest 3, always on, L30, reb14 | +1,647 | +2,556 | −4,921 | −718 | 63.2% |

Reading: shorting the weakest alts does earn in the bear years (+€4–13k in 2025 and
2026) but every gated variant loses €6–10k in 2024, because a BTC-below-SMA gate fires
often enough inside a bull year and the weakest alts then squeeze 20–40% in days. The
regime gate does not separate the years; the drawdowns (32–70%) are RS-sleeve sized for
a quarter of the return. Funding is a wash to slightly negative for alt shorts (the
weakest names often trade at negative funding, so the short pays). Not armable.

## Family 2: dollar-neutral pair (long BTC spot / short weakest alts perps)

Half the gross long BTC, half short the N weakest desk alts by lookback return, no
regime gate, 10% hard stop per leg.

### Single clock is a lottery on the start day

`pair3_L30_reb14` from 2024-03-16 looks excellent: +4,720 / +10,658 / +3,765, total
+€19,142, DD 17.4%, positive every year. Shift the start day only:

| start | 2024 | 2025 | 2026 | total | maxDD |
|---|---|---|---|---|---|
| 03-16 | +4,720 | +10,658 | +3,765 | +19,142 | 17.4% |
| 03-17 | +7,393 | +15,465 | +3,678 | +26,536 | 16.3% |
| 03-19 | +2,019 | +9,722 | +1,598 | +13,340 | 15.6% |
| 03-21 | −623 | +4,117 | −881 | +2,613 | 23.7% |
| 03-23 | +3,886 | −1,577 | −2,156 | +153 | 27.7% |
| 03-25 | +2,025 | −4,506 | −1,253 | −3,733 | 37.6% |
| 03-26 | +1,616 | −6,094 | −1,404 | −5,882 | 43.8% |
| 03-29 | +7,201 | +1,267 | +2,292 | +10,760 | 24.6% |

14 phases: range −€5.9k … +€26.5k, median ≈ +€3.7k, positive in all three years in 4 of
14. The rebalance-day sweep shows the same thing (reb8 +25.8k, reb10 −0.9k, reb14
+19.1k, reb15 +8.4k). A single weekly-or-longer clock on this pair is mostly phase
luck; any "best cell" here is overfit.

### Staggered tranches (phase-averaged) is the honest number

Book split into `rebalance_days` tranches, each on its own phase (averaged curves):

| spec | 2024 | 2025 | 2026 | total | maxDD | trades |
|---|---|---|---|---|---|---|
| pair3 L20 reb7 | +3,276 | +6,112 | +746 | +10,134 | 15.2% | 466 |
| pair3 L30 reb7 | +4,072 | +4,404 | +1,663 | +10,140 | 18.0% | 388 |
| pair5 L30 reb7 | +3,534 | +8,242 | −1,227 | +10,549 | 14.0% | 537 |
| pair3 L20 reb10 | +3,284 | +5,089 | +1,578 | +9,951 | 14.2% | 374 |
| pair5 L30 reb10 | +3,545 | +7,065 | −1,022 | +9,587 | 13.4% | 449 |
| pair3 L20 reb14 | +3,827 | +3,761 | +1,831 | +9,419 | 15.2% | 300 |
| pair5 L30 reb14 | +3,384 | +6,026 | −476 | +8,934 | 12.9% | 381 |
| pair3 L20 reb21 | +2,760 | +3,461 | +2,162 | +8,384 | 15.1% | 231 |
| pair5 L30 reb21 | +2,652 | +3,874 | −782 | +5,744 | 14.1% | 314 |
| pair3 L30 reb14, only BTC > SMA50 | +7,798 | +4,470 | −1,355 | +10,913 | 13.6% | 347 |
| pair3 L30 reb14, only BTC < SMA50 | −6,108 | −1,587 | −63 | −7,757 | 48.2% | 326 |

Every ungated phase-averaged cell lands at +€8–10.5k over 2.6 years (≈ +€3.5–4k/yr,
15–20%/yr on €20k gross) with DD 13–18%, positive in 2024 and 2025 and flat (±€2k) in
2026. The surface is smooth in N, lookback and clock — that is the robust part.

The two gated rows are the key insight: the pair makes **all** its money while BTC is
above its SMA50 and *loses* when BTC is below it. "Short the weakest alts in a bear" is
wrong even dollar-neutral; what works is "BTC dominance while BTC trends up", i.e. the
alts that lag BTC in a BTC uptrend keep lagging. The 2025 profit came from the 153 days
BTC traded above SMA50, not from the 212 bear days.

Diversification against the RS sleeve: daily-return correlation −0.18 (2024 −0.08,
2025 −0.24, 2026 −0.31). Across the five deepest RS drawdowns the pair returned
+€483, +€1,019, +€2,910, −€1,589 and +€3,722 — a cushion, not a hedge. On separate
€20k books the sum per year is 2024 +€135.8k, 2025 +€26.3k, 2026 +€241.5k
(compounded RS curve), i.e. the pair adds a few percent in good years and roughly
doubles the thin 2025.

### Venue constraint

Bitvavo is spot-only. The short leg needs perps (OKX is the only short venue the desk
has credentials for; the paper short sleeve `bot/live/momentum_short_weakest.py`
already models it and "never live-orders"). Margin: €10k short notional at 3× needs
~€3.3k collateral on OKX plus €10k BTC spot on Bitvavo, so a €20k-gross pair ties up
roughly €13–14k of EUR that the RS sleeve cannot use — a real cost in 2024/2026 where
the RS sleeve earned 4–10× more per euro.

## Family 3: long-spot capitulation bounces (feasible on Bitvavo)

| spec | 2024 | 2025 | 2026 | total | maxDD | trades |
|---|---|---|---|---|---|---|
| BTC −12% in 3d, hold 5, tp 8% | +2,667 | +2,769 | +2,446 | +7,883 | 4.8% | 8 |
| BTC −12% in 3d, hold 10, tp 10% | +2,667 | +2,769 | +2,960 | +8,397 | 4.8% | 8 |
| BTC −7% in 1d, hold 3, tp 5% | +4,555 | +218 | +2,805 | +7,578 | 1.4% | 8 |
| BTC −10% in 3d, hold 5, tp 8% | +800 | +3,292 | −5,211 | −1,120 | 24.2% | 12 |
| BTC −12% in 5d, hold 7, tp 10% | +2,511 | +4,126 | −6,564 | +73 | 26.7% | 14 |
| strongest alt after BTC −12%/3d | +4,384 | −2,374 | −2,059 | −48 | 28.6% | 10 |
| median desk alt −12%/3d, hold 5 | +65 | −365 | −4,639 | −4,940 | 30.5% | 48 |
| median desk alt −15%/3d, hold 5 | +1,885 | −558 | −4,353 | −3,026 | 27.0% | 24 |
| median desk alt −20%/5d, hold 7 | +1,958 | −2,619 | +2,190 | +1,529 | 17.9% | 14 |

BTC capitulation longs look clean (+€7.6–8.4k, DD < 5%) but rest on 8 events in 2.6
years; one parameter step (−10% instead of −12%, or 5-day window) flips 2026 to −€5–6k.
Alt-side triggers are negative. Not a strategy, at most a rule to not be scared out of
the RS sleeve's BTC leg after a flush.

## What the rest of the market did in the bear (survey, 2026-10-14)

Follow-up question: "there must be a tactic that still earns in a bear". External
evidence, checked against our data where possible:

| tactic | reported result | our check / feasibility |
|---|---|---|
| Quant / market-neutral funds | 2025: quant avg +0.4%, median +3.2%, 15/26 positive; long-only −15.3%; best named neutral fund +11.3% ([CFR](https://cryptofundresearch.com/crypto-hedge-fund-performance/), [CFR neutral](https://cryptofundresearch.com/market-neutral-crypto-funds/)) | RS sleeve 2025 +21% on a fresh €20k; the professionals' bear answer was "cut exposure, hedge, arb", not a bear-alpha engine |
| Cash-and-carry (long spot / short perp, collect funding) | 2024-era decks 15–30%; independent backtest 5.9%/yr over 2y, <2% last 12m, fees ≈30% of that ([StratProof](https://stratproof.com/blog/basis-trade-works-but-not-as-much-as-they-say)); BTC basis 4.46% by Dec 2025 ([tv-hub](https://www.tv-hub.org/guide/market-neutral-strategy-crypto)) | Binance funding proxy, gross on notional: BTC 2024 12.0%, 2025 5.1%, 2026 2.3%; ETH 13.0 / 4.9 / 1.5; SOL 13.7 / 0.4 / −0.7. A "hold only when trailing funding > 0" filter is worse (flip costs). One round trip costs 0.90%. On €20k capital (spot + margin) ≈ 2–3% in 2025 |
| Perp-to-perp funding spread (OKX vs Binance/Bybit) | Non-VIP retail: gross 0.0043% vs cost 0.294% per event, 0% win rate ([research repo](https://github.com/Kaleb0719/retail-funding-arbitrage-research)); practitioners 5–15% only with maker fills and capital on both venues ([LMEX](https://lmex.ai/blog/cross-exchange-arbitrage-honest/)) | Not viable at our fee tier; skipped |
| Mean reversion / RSI oversold bounce | 2022 bear: −29% (60% win rate), naked MR averages −40.6% in bear regimes vs +16.3% in bull ([Coinquant](https://www.coinquant.ai/blog/bear-market-crypto-strategy-what-actually-backtests-well-when-bitcoin-falls), [78 backtests](https://www.coinquant.ai/blog/building-a-mean-reversion-strategy-in-cryptocurrency-markets-evidence-from-78-backtests)); RSI 30/70 on BTC 2024–26 −27% long-only ([StrategyVerdict](https://strategyverdict.com/rsi-strategy-backtest-bitcoin/)) | Matches family 3 above: BTC-only capitulation longs look fine on 8 events, alt-side triggers negative |
| Selling volatility (Deribit strangles, covered calls) | Weekend strangle +45% APR Sep 24–Apr 25 but tail weeks of −63%/−39% in 2026; BTC 20-delta covered call +5.5% net on held BTC, negative over the full 2021–26 period unfiltered ([Deribit](https://insights.deribit.com/education/option-backtest-selling-weekend-vol-revisited/), [Anchorage](https://www.anchorage.com/insights/synthetic-yield-on-bitcoin-implementation-discipline-and-performance-boundaries-of-systematic-covered-call-writing)) | No options venue in the stack; only adds yield on BTC you already hold |
| Idle-cash yield | Bitvavo EURC lending 4% (Germany only, Oct 2026); OKX USDC flexible 1.0–3.4%; EURC on Aave ~2.2% ([Bitvavo](https://www.bitvavo.com/en/news/flex-fixed-staking-lending-earn-rates-oct-2026), [OKX](https://www.okx.com/en-eu/learn/crypto-earn)) | The RS sleeve's cash sits idle; a 2–4% floor on it is the one free improvement, with issuer/platform risk |
| Grid trading | Expected value ≈ 0 under simple assumptions ([arXiv 2506.11921](https://arxiv.gg/abs/2506.11921)) | Skipped |
| Outright shorts | Quant funds "shifted net short" into the Nov 2025 selloff | Family 1: works in 2025/26, loses €6–10k in 2024 even gated |

Bottom line of the survey: the tactics that reliably earn in a bear return low single
digits (carry, lending); the ones that promise more either lose in bull years (shorts) or
carry fat tails (vol selling, mean reversion). The only thing in this lab that beats
both in 2024 *and* 2025 is the staggered BTC-dominance pair, and it is a trend trade in
disguise.

## Verdict

1. There is no structurally profitable *bear* sleeve in this data. Every strategy that
   is short alts loses when BTC is below its SMA50 — outright or dollar-neutral — because
   the weakest names squeeze hardest exactly there. Cash is the proven bear tactic and
   the RS sleeve already executes it (2025 +21%).
2. The one robust positive line is the **phase-averaged dollar-neutral pair** (long BTC
   / short 3–5 weakest desk alts, 20–30-day lookback, 7–14-day staggered clock):
   +€8–10k over 2.6 years on €20k gross, DD 13–15%, positive 2024 and 2025, flat 2026.
   It is a BTC-dominance trade, not a bear trade; it earns while BTC trends up and alts
   lag, and would lose in a genuine alt-season. It needs OKX perps for the short leg and
   ties up EUR the RS sleeve uses far better in bull years.
3. If a second sleeve is ever armed, this is the only candidate: staggered tranches
   (never a single clock), gross ≤ €10k, OKX perps for the short leg, and only after a
   paper period on the existing short runner shows fills and funding match the model.
   Not armed by this lab.

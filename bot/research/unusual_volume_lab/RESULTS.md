# Unusual-volume momentum system — causal replay on Bitvavo

Question asked: does the 3-step "momentum system" (unusual volume → EMA20/EMA50 + MACD
confluence on 4h → RSI/trailing-stop exits) make money on the Bitvavo EUR tape, net of
fees and impact? And what did ZEUS look like right before its spurt?

Data: every Bitvavo EUR market (427 listed, 415 with enough 4h history), daily candles from
2023-01 and 4h candles from 2024-03, cached under `data/research/bitvavo_1d_all` and
`data/research/bitvavo_4h`. Replay: `python -m bot.research.unusual_volume_lab`
(`--refresh` pulls the candles, `--full-grid` runs every variant on every scan).

Periods: IS 2024-06-01 → 2025-12-31, OOS 2026-01-01 → 2026-10-14. Book €2,000 per slot
(the MoonShot sleeve size), taker 0.25 % each way plus impact `0.02 × notional / 20d ADV`
capped at 3 %. Entries at the next 4h open after the confirming close; exits at the next
4h open after the trigger. No per-coin branches anywhere.

## Step 1 — the scanner alone (daily bars)

Signal: day quote-volume ≥ 3 × mean of the prior 30 days, day volume ≥ €50k.
16,052 signals (8,965 IS, 7,057 OOS) ≈ 18.7 per day across the universe. Forward audit
from the next day's open:

| horizon | close mean IS / OOS | close median IS / OOS | % positive IS / OOS | max-high mean IS / OOS | max-high median | ≥ +20 % high | ≥ +50 % high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1d | −0.3 % / −0.6 % | −1.1 % / −1.4 % | 42 % / 40 % | +7.5 % / +8.7 % | +4.1 % | 7–10 % | 1–2 % |
| 3d | −0.5 % / −1.1 % | −1.5 % / −2.7 % | 43 % / 37 % | +12.3 % / +14.5 % | +6.9 % | 16–18 % | 4–5 % |
| 5d | −0.6 % / −1.6 % | −2.3 % / −3.3 % | 42 % / 37 % | +15.3 % / +18.0 % | +8.7 % | 22–23 % | 5–6 % |
| 10d | −1.3 % / −2.4 % | −4.3 % / −4.8 % | 39 % / 36 % | +20.7 % / +24.0 % | +12 % | 31–32 % | 9 % |

IS and OOS agree (OOS is slightly worse); "quiet" spikes (|day return| ≤ 5 %) and up-day
spikes are both negative close-to-close. The only thing a volume spike reliably buys you
is a bigger wick, not a higher close: the median name touches +8.7 % within five days and
closes −2 % to −3 % below the entry. Everything therefore hinges on whether step 3 can
sell into the wick.

## Steps 2 + 3 — the full system, 4h bars

Base = the system exactly as described: confluence within 6 bars after the signal day
(close > EMA20 > EMA50 and MACD line > signal), RSI14 ≥ 75 → sell, trail under the
highest confirmed higher-low (3-bar pivots), hard stop 8 %, time stop 30 bars, 1 slot.

| variant | IS PnL | IS maxDD | IS trades / win | OOS PnL | OOS maxDD | OOS trades / win |
| --- | --- | --- | --- | --- | --- | --- |
| **base RSI75 + swing trail** | **−€4,406** | €7,192 | 247 / 35 % | **−€6,914** | €8,067 | 127 / 26 % |
| RSI80 + swing | −€5,232 | €7,709 | 222 / 28 % | −€5,143 | €6,820 | 118 / 25 % |
| RSI70 + swing | +€939 | €6,256 | 301 / 41 % | −€8,413 | €10,011 | 136 / 31 % |
| no RSI, swing trail | −€8,360 | €10,539 | 188 / 24 % | −€2,396 | €4,421 | 117 / 21 % |
| RSI75 + 10 % trail | −€2,023 | €5,576 | 297 / 40 % | +€1,983 | €3,015 | 132 / 39 % |
| no RSI, 10 % trail | −€1,014 | €5,765 | 253 / 33 % | +€4,091 | €2,109 | 124 / 40 % |
| no RSI, 15 % trail, stop 10 % | −€2,260 | €5,340 | 187 / 34 % | +€199 | €3,889 | 110 / 31 % |
| scale-out 25 % @ +20 %, 25 % @ +40 % | −€3,399 | €7,175 | 247 / 35 % | −€6,692 | €7,876 | 127 / 27 % |
| EMA only | −€2,977 | €5,593 | 266 / 34 % | −€4,321 | €5,009 | 149 / 28 % |
| MACD only | +€132 | €3,702 | 238 / 40 % | −€4,572 | €6,221 | 119 / 25 % |
| fresh MACD cross (≤3 bars) | −€5,475 | €5,587 | 131 / 30 % | −€2,935 | €4,344 | 91 / 25 % |
| no confluence (buy next open) | +€7,072 | €2,693 | 241 / 39 % | −€2,910 | €5,248 | 119 / 24 % |
| confluence window 12 bars | −€5,692 | €7,485 | 250 / 35 % | −€3,990 | €4,903 | 117 / 29 % |
| 3 slots | −€10,657 | €12,556 | 663 / 35 % | −€11,737 | €14,250 | 409 / 28 % |
| 3 slots, no RSI, 10 % trail | −€4,433 | €11,806 | 655 / 34 % | −€7,529 | €12,393 | 404 / 31 % |
| time stop 12 bars | −€6,423 | €10,228 | 311 / 36 % | −€10,002 | €12,264 | 197 / 32 % |
| time stop 60 bars | −€5,248 | €7,731 | 238 / 34 % | −€6,571 | €7,485 | 113 / 23 % |
| hard stop 5 % | −€4,933 | €7,858 | 313 / 30 % | −€9,867 | €11,825 | 186 / 24 % |
| hard stop 15 % | −€1,796 | €4,640 | 203 / 40 % | −€235 | €4,533 | 107 / 30 % |

Scanner variants (base / no-RSI-10 % / 3 slots): day volume ≥ €1M: −€4.0k / +€1.4k /
−€12.5k IS and −€9.9k / €0 / −€8.7k OOS. 5× instead of 3×: −€5.9k / −€4.5k / −€8.5k IS,
−€4.4k / +€0.4k / −€17.7k OOS. Quiet spikes: −€1.9k / +€1.2k / −€5.3k IS, −€3.2k / −€2.4k /
−€1.7k OOS. Up-day spikes (≥ +5 %): −€10.6k / −€5.1k / −€9.2k IS, −€9.4k / −€1.9k / −€16.0k OOS.

Intraday version (signal on a 4h bar ≥ 10× or 20× the 30-day 4h mean, so the entry is
the same day instead of the next): every variant is negative OOS (−€5.6k … −€30.9k on a
€2k slot); 20× looks fine IS (+€3.3k … +€4.8k) and loses −€9k … −€11k OOS.

### What the table says

- Not a single variant is positive in both periods. The system as described loses
  about 2–3 × its slot per year; win rate 25–35 %.
- The RSI-75 exit is the worst part: in a real parabolic move RSI crosses 75 on the
  first impulse bar and stays there, so it sells at the start of the run, never near
  the top. Removing it and using a plain 10 % trail is the least-bad exit (−€1.0k IS,
  +€4.1k OOS) but that is one OOS period, not a system.
- The EMA/MACD confluence does not add selection value: "no confluence" is the best IS
  variant and about equal OOS. By the time EMA20 > EMA50 and MACD > signal on 4h, the
  wick is mostly spent.
- More slots lose more: the per-trade expectancy is negative, so trading more of the
  signals scales the loss.
- The hard stop fires on 35–50 % of trades, i.e. most volume spikes are followed by a
  −8 % move within the holding window — consistent with the daily audit.

## Volume band around €200k ("everything near €200k is worth entering")

Tested three readings of the observation, all causal, same periods.

(a) Spike days (≥ 3× 30d) bucketed by the day's own quote volume, forward from the next open:

| day qvol band | n IS / OOS | 5d close mean IS / OOS | 5d % positive | 5d max-high mean | 10d close mean IS / OOS |
| --- | --- | --- | --- | --- | --- |
| €50k–100k | 891 / 1,099 | +0.4 % / −0.8 % | 49 % / 41 % | +11 % / +13 % | −0.6 % / −1.1 % |
| €100k–150k | 663 / 776 | −0.1 % / −0.1 % | 46 % / 39 % | +11 % / +13 % | −0.7 % / −0.4 % |
| **€150k–250k** | 1,080 / 1,098 | **−0.2 % / −1.0 %** | 46 % / 40 % | +13 % / +16 % | −1.1 % / −0.5 % |
| €100k–400k | 2,824 / 2,829 | −0.2 % / −0.9 % | 45 % / 40 % | +13 % / +15 % | −0.8 % / −1.0 % |
| €200k–500k | 2,115 / 1,857 | −0.5 % / −1.8 % | 43 % / 38 % | +14 % / +16 % | −1.0 % / −2.2 % |
| €500k–1M | 1,498 / 1,141 | −1.2 % / −1.1 % | 38 % / 34 % | +15 % / +23 % | −2.2 % / −1.9 % |
| €1M–3M | 1,819 / 1,062 | −1.0 % / −3.4 % | 38 % / 30 % | +18 % / +24 % | −1.6 % / −5.9 % |
| > €3M | 1,383 / 504 | −1.1 % / −3.6 % | 40 % / 35 % | +20 % / +25 % | −1.3 % / −5.2 % |

(b) Spike days bucketed by the *coin's* 30-day average volume: ADV €150k–250k gives
−1.1 % IS / −2.3 % OOS at 5 days (39 % / 35 % positive) — worse than (a), not better.

(c) Every day on which a market printed €150k–250k, spike or not (13,556 / 7,415 days):
−0.8 % IS / −0.2 % OOS at 5 days, 42–43 % positive; identical to every other band.
Restricting to calm days (volume ≤ 1.5× normal) changes nothing.

System on band-filtered spikes (IS / OOS, €2k slot):

| band | RSI75 + swing | no RSI, 10 % trail | no confluence, 10 % trail | no confluence, 10 % trail, 3 slots |
| --- | --- | --- | --- | --- |
| €100k–300k | −€688 / +€1,326 | −€1,620 / +€2,197 | −€169 / −€2,000 | −€4,998 / −€1,151 |
| €100k–400k | −€1,471 / +€682 | +€289 / +€1,182 | +€2,703 / −€1,156 | −€2,455 / −€6,815 |
| €150k–250k | −€550 / −€1,509 | −€1,963 / +€134 | −€1,143 / +€1,985 | **+€343 / +€2,590** |
| €200k–500k | +€3,673 / −€5,156 | +€420 / −€1,845 | +€385 / −€1,801 | −€2,619 / −€7,557 |

What holds up: the band is a **real filter against the worst spikes**. Spikes printing
> €1M lose 3–4× more over 5–10 days than spikes in the €100k–€400k band, and the
€100k–€400k band is the least negative region (−0.2 % IS, −0.9 % OOS) with the highest
share of positive outcomes (45 % / 40 %). What does not hold up: the band is not an edge
on its own. Close-to-close returns stay below zero in every band and every period, the
only cell positive in both periods (€150k–250k, no confluence, 10 % trail, 3 slots:
+€2.9k over 2.4 years on €6k deployed, max DD €4.3k) has negative neighbours on both
sides (€100k–300k −€5.0k / −€1.2k, €200k–500k −€2.6k / −€7.6k), which is a sharp
parameter, not a system. The impression that "everything around €200k works" matches
the data in one respect: those names deliver the +13–16 % average 5-day wick with fewer
−8 % stops than the big-volume names, so discretionary entries there *feel* better. The
median outcome is still −2 % after five days.

### PnL with the full €20k book on the band

"Trade that way" with the whole book, mechanically: every spike in the band is bought at
the next open and the €20k is either all-in (1 slot) or split over 2/4/10 slots. Taker
0.25 % per side plus impact `0.02 × notional / 20d ADV` capped at 3 % per side — for a
€20k clip in a name whose normal day is €20k–€80k the cap binds, so the real fill would
be worse than modelled.

Band €150k–250k, net PnL IS (19 months) / OOS (9.5 months):

| sizing | sell at close +1d | +3d | +5d | +10d | 4h, 10 % trail / 8 % stop | system (EMA/MACD + RSI75) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 × €20k | −€196k / −€123k | −€90k / −€38k | −€60k / −€53k | −€49k / −€44k | −€28k / −€26k | −€30k / −€62k |
| 2 × €10k | −€84k / −€45k | −€43k / −€32k | −€37k / −€28k | −€45k / −€24k | −€5k / −€11k | — |
| 4 × €5k | −€31k / −€13k | −€23k / −€22k | −€21k / −€16k | −€31k / −€5k | −€2.9k / +€3.5k | −€5k / −€26k |
| 10 × €2k | −€10k / −€2.7k | −€5.8k / −€7.5k | −€7.8k / −€6.6k | −€20k / −€4.6k | +€1.0k / −€4.3k | — |

Gross (before costs) is itself around zero or negative in every row (e.g. 1 × €20k,
+5d: +€3.2k IS / −€21k OOS gross; costs €63k / €32k). Band €100k–€400k is worse in every
cell (1 × €20k, +1d: −€305k / −€218k; 10 × €2k, 4h trail: −€6.3k / −€8.8k). The only
cells near break-even are the small clips (€2k–€5k) with a percent trail, and they flip
sign between periods. Scaling the stake up scales the loss faster than linearly because
the impact on €200k-a-day names grows with clip size.

Kept generically: if a volume-spike entry is ever traded, cap the day volume
(≈ €100k–€400k) rather than only flooring it — the floor keeps out the illiquid tail, the
cap keeps out the crowded tail where adverse selection is worst. Not armed as a trigger.

## The ZEUS example (Bitvavo ZEUS-EUR, October 2026)

Daily bars (qvol = volume × close, multiple vs the prior 30-day mean):

| day | open | high | close | qvol | × avg | day return |
| --- | --- | --- | --- | --- | --- | --- |
| 10-01 … 10-04 | 0.00202–0.00205 | ≤ 0.00214 | 0.00200–0.00205 | €13k–€30k | 0.3–0.8× | ±1 % |
| 10-05 | 0.00202 | 0.00500 | 0.00309 | €1.80M | 48.9× | +52.7 % |
| 10-06 | 0.00312 | 0.00660 | 0.00551 | €3.03M | 32.0× | +76.8 % |
| 10-07 | 0.00556 | 0.01700 | 0.00970 | €6.71M | 35.5× | +74.4 % |
| 10-08 | 0.00976 | 0.01500 | 0.00871 | €2.91M | 7.1× | −10.7 % |
| 10-09 | 0.00876 | 0.01013 | 0.00695 | €1.45M | 2.9× | −20.6 % |
| 10-10 | 0.00696 | 0.00770 | 0.00573 | €0.58M | 1.0× | −17.7 % |
| 10-11 | 0.00573 | 0.01150 | 0.00910 | €3.53M | 6.2× | +58.9 % |

4h bars around the start (EMA20/EMA50, MACD line/signal, RSI14 on 4h closes):

| 4h bar (UTC) | close | 4h qvol | EMA20 vs EMA50 | MACD | RSI | confluence |
| --- | --- | --- | --- | --- | --- | --- |
| 10-04 20:00 | 0.00205 | €4k | 0.00203 < 0.00205 | −0.000008 / −0.000010 | 52.5 | MACD only |
| 10-05 00:00 | 0.00202 | €0.04k | below | −0.000008 / −0.000010 | 48.1 | MACD only |
| 10-05 04:00 | 0.00219 | €9k | below | +0.000006 / −0.000007 | 66.0 | MACD only |
| 10-05 08:00 | 0.00219 | €413k | equal | +0.000017 / −0.000002 | 66.0 | MACD only |
| 10-05 12:00 | 0.00236 | €175k | 0.00209 > 0.00207 | +0.000038 / +0.000006 | **75.6** | **first EMA + MACD** |
| 10-05 16:00 | 0.00373 | €874k | above | +0.000164 / +0.000038 | 93.0 | yes |
| 10-05 20:00 | 0.00309 | €254k | above | +0.000210 / +0.000072 | 68.4 | yes |
| 10-06 04:00 | 0.00413 | €236k | above | +0.000369 / +0.000163 | 78.7 | yes |
| 10-07 08:00 | 0.01360 | €3.86M | above | +0.001577 / +0.000786 | 95.1 | yes |

What this shows:

- There was **no precursor**. Daily volume in the four days before was €13k–€30k
  (0.3–0.8× normal), EMA20 was below EMA50, MACD was slightly negative, RSI 42–52. The
  first abnormal 4h volume (€413k at 08:00, ~40× a normal 4h bar) arrived in the same
  bar as the first +8 % and the daily signal only exists at the 10-05 close, after +53 %.
- The first bar with full EMA + MACD confluence (10-05 12:00, close 0.00236) already had
  RSI 75.6. Following the rules literally you would have been told to sell at the moment
  you were allowed to buy. The next bar went to 0.00373 (+58 % in 4h).
- RSI stayed ≥ 75 for most of 10-05 12:00 → 10-08 04:00. An RSI-75 exit sells the run at
  the start; the pct trail holds it.

System replay on ZEUS alone (daily signal 10-05 → earliest confluence 10-06 00:00 →
entry 10-06 04:00 open at 0.00341):

| exit rule | trades | PnL on a €2k slot | notes |
| --- | --- | --- | --- |
| RSI75 + swing trail (as described) | 5 | +€866 | sells one bar after each entry (RSI), re-enters on each daily signal |
| RSI80 + swing trail | 5 | +€652 | |
| no RSI, 10 % trail | 5 | **+€3,805** | 0.00341→0.00514 (+€1,001), 0.00526→0.01370 (+€3,190), then two −€200 stops |
| scale-out 25 % @ +20/40 % | 5 | +€866 | RSI fires before any scale-out level |

Your three manual ZEUS round trips in the live ledger: +€306.82 (0.004291 → 0.0049655),
+€2,157.40 (0.006658 → 0.0095618), −€3,442.40 (0.012665 → 0.010461 on €19.5k notional)
→ **net −€978**. The mechanical version would have entered at 0.00341 and 0.00526, not at
0.0127, and would not have put €19.5k on one 4h bar. The difference is sizing and entry
discipline, not the indicators.

## Verdict

- Not armed. The scanner's close-to-close expectancy is negative in every cut, and no
  exit rule turns the +16 % average 5-day wick into realized PnL after fees and the −8 %
  stops that precede it on most names. ZEUS is the survivor in a universe where ~19
  spikes a day fire and ~65 % of them close lower after five days.
- What we keep from the exercise, generically: (1) RSI-overbought exits are harmful on
  parabolic coins — the live sleeves already use trail + profit-lock, not RSI; (2) if a
  spike is traded at all, a fixed slot (€2k) and a percent trail, never a €19.5k
  discretionary add at 6× the breakout price; (3) a volume multiple is useful as a
  *filter* (we already use quote-volume floors), not as an *entry trigger*.
- The MoonShot `news_momo` sleeve (`bot/research/moonshot_preimage`) was the closer cousin
  of this idea and its causal packs were also negative; the two results agree.

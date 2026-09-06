# Architectuur- & PnL-autopsie — 2026-09-06

**Scope:** Live micro desk (`maker_inventory` / Capital Velocity Desk), Bitvavo + OKX, pocket €2 000  
**Bronnen:** `data/dashboard_pnl_cache.json` (exchange FIFO), live bridge/session state, `docs/POST_CVD_VELOCITY_DESK.md`, research diagnoses, CoinGecko EUR 7d charts (opgehaald 2026-09-06 ~11:50 UTC)

---

## 1. Verdict in één zin

De dashboardverliezen zijn **echt** (week FIFO **−€59**, dag **−€11**); ze komen niet uit een kapotte KPI, maar uit een strategie die **verliezen crystalliseert bij unlocks** terwijl **util laag blijft** en **passieve majors** in dezelfde week **+€30…+€40** op €2 000 zouden hebben gemaakt — met UNI als extreme outlier (**+38%**).

---

## 2. Wat de rode cijfers wél en niet meten

| KPI (live ~11:47 UTC) | Waarde | Wat het is |
|----------------------|-------:|------------|
| Geïnd vandaag (FIFO) | **−€10,74** | Gesloten sells vandaag (OKX SOL −6,46; Bitvavo UNI −4,03; XRP −1,41; ARB +1,15) |
| Geïnd week (FIFO) | **−€59,13** | Zelfde methode, week start zo 30 aug 22:00 UTC |
| Sessie realized Δ | **≈ −€4,94** | `realized_trade_pnl` 5,64 − `session_start_realized` 10,57 |
| Open MTM | **≈ +€0,8** | Bijna flat op open bags |
| Free quote | **≈ €3 682** | Cash idle |
| Locked notional | **≈ €373** | ~**19%** van €2 000-budget |
| Blocked sells | **~142 k** | **Skip-tellers**, geen euroverlies (`sell_below_break_even` alleen al ~119 k) |

**Conclusie:** week −€59 is geen UI-bug. Het is wel **dicht tegen de product-kill** (weekly FIFO ≤ **−€75** in `POST_CVD_VELOCITY_DESK.md`) — nog **€16** marge.

---

## 3. Huidige architectuur (wat er écht draait)

```
┌─────────────────────────────────────────────────────────────┐
│              CAPITAL VELOCITY DESK (live)                     │
│  Alpha: maker inventory recycle + AlphaI sleeve hints         │
│  Niet: CVD / mid-arb (abandoned; shadow was negatief)         │
├─────────────────────────────────────────────────────────────┤
│  Buys: focus ring, momentum/quality gates, pocket budget      │
│  Sells (volgorde): early cut → UW recycle → hard cut ~2,5%     │
│                 → BE+/trail/harvest                            │
│  Harde remmen: fee-aware BE, trusted cost, sleeve loss-cap,   │
│                 would-buy nursing, rising-hold                  │
│  Hybrid unlocks: mid_flat / lag_time_partial / deadlock_*     │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Waarom dit structureel slecht presteert in *deze* tape

1. **Never-loss + fee-aware BE** blokkeert de bulk van exits → bags blijven liggen of worden alleen via **bewuste UW-tiers** verkocht → FIFO-verlies (UNI/SOL/XRP vandaag).
2. **Lage util** (~€373 locked vs €3,6 k+ free) → te weinig round-trips om de geclaimde maker-edge te verdienen.
3. **Idealized velocity math faalt live:** €55 × 0,5% netto ≈ €0,28/exit → **€20/dag** vraagt ~**73** full exits; live haalt dat niet terwijl policy remt.
4. **Expectatie-mismatch:** paper **+€3,4 k** was CVD-inject; research **+€212 k** was mid-fill=1. Live is een **andere** alpha. Dat staat al in `live_underperformance_diagnosis.json`.
5. **Adverse selection op alts:** wanneer UW-unlock wél vuurt, is het vaak ná een dump — en soms vlak vóór een rebound (UNI-week is het schoolvoorbeeld).

### 3.2 Wat géén bug is

Sleeve-loss-cap, rising-hold, reserves, mild-UW remmen: dat is **productpolicy**. Ze beschermen tegen blind dumpen, maar kosten **throughput** en soms **opportunity cost** als mid_flat/lag_time te laat of te vroeg vuurt.

---

## 4. Markbenchmark zelfde ~7 dagen (CoinGecko EUR)

| Asset | Start→eind (≈7d) | Return | PnL op €2 000 passief |
|------:|-----------------:|-------:|----------------------:|
| BTC | 67 472 → 68 838 | **+2,02%** | **+€40** |
| ETH | 2 123 → 2 153 | **+1,43%** | **+€29** |
| SOL | 90,7 → 91,7 | **+1,08%** | **+€22** |
| XRP | 1,20 → 1,22 | **+1,84%** | **+€37** |
| UNI | 4,40 → 6,07 | **+37,82%** | **+€756** (outlier) |
| BTC/ETH 50/50 | — | ~+1,73% | **+€35** |
| Equal BTC/ETH/SOL/XRP | — | ~+1,59% | **+€32** |

### Opportunity cost vs desk week −€59

| Alternatief (passief €2 000, 7d) | Alt-PnL | Desk vs alt |
|----------------------------------|--------:|------------:|
| BTC hold | +€40 | **−€100** |
| ETH hold | +€29 | **−€88** |
| XRP hold | +€37 | **−€96** |
| BTC/ETH 50/50 | +€35 | **−€94** |
| UNI hold (lottery) | +€756 | **−€815** |

**UNI-micro counterfactual:** FIFO-sell −€4,03 vandaag. Had een ~€200-bag aangehouden door de +38%-rally, dan MTM **≈ +€76** i.p.v. −€4 → **≈ €80** verschil op die ene positie alleen. Dat is **geen** voorspelling dat UNI elke week +38% doet; het toont wel hoe pijnlijk crystalliseren + missen van de rebound is.

---

## 5. Alternatieve strategieën — terugblik én vooruitblik

Cijfers hieronder zijn **modelmatig**. Terugblik = “als we dit pad hadden gevolgd op dezelfde €2 000 /zelfde week”. Vooruitblik = **eerlijke banden** (p25 / p50 / p75) over **~30 kalenderdagen**, geen fake precisie. UNI-lottery wordt **niet** in de p50 van majors gestopt.

### A) Huidige Velocity Desk + hybrid mild-UW (status quo / net getuned)

| Venster | Schatting |
|---------|-----------|
| Terugblik deze week | **−€59** (gemeten) |
| Vooruit 30d p25/p50/p75 | **−€120 / −€30 / +€40** |
| Waarom | Unlocks blijven kleine FIFO-verliezen schrijven; util blijft structureel laag tot mid_flat/lag_time *fills* (niet alleen plans) bewijzen |

**Kill-risico:** bij ~−€10/dag FIFO raakt −€75/week binnen **~1–2 dagen** opnieuw in zicht.

### B) Cash / vault-only (geen nieuwe ring-buys)

| Venster | Schatting |
|---------|-----------|
| Terugblik | **≈ €0** trading-FIFO (geen nieuwe crystallisaties); MTM op restbags neutraal/klein |
| vs desk | **≈ +€59** vermeden trading-drag |
| Vooruit 30d | **−€20 / €0 / +€20** trading; opportunity cost vs BTC ~ **−€40…−€160**/maand afhankelijk van tape |
| Wanneer | Als kill-gate nadert: stop nieuwe risk, harvest alleen BE+ |

### C) Buy & hold BTC/ETH 50/50 (geen desk)

| Venster | Schatting |
|---------|-----------|
| Terugblik 7d | **+€35** |
| vs desk | **+€94** |
| Vooruit 30d p25/p50/p75 | **−€100 / +€40 / +€160** |
| Risico | Drawdowns van −10…−20% op crypto-maanden blijven normaal |

### D) Liquid-only maker MM (BTC/ETH/XRP, geen thin alts, strakke spreads)

| Venster | Schatting |
|---------|-----------|
| Terugblik (counterfactual) | **−€5 … +€15** i.p.v. −€59 — minder adverse selection, minder UNI/SOL UW-unlocks |
| Vooruit 30d | **−€40 / +€25 / +€90** |
| Voorwaarde | Echte fill-rate + fee-aware edge ≥ ~3–5 bps netto na adverse; lab-maker was historisch ~**€0** |

### E) Strict trend-follow majors (entry alleen rising, hard SL **2,5%**, **geen** never-loss hold)

| Venster | Schatting |
|---------|-----------|
| Terugblik | Waarschijnlijk **−€10 … +€30**: snellere cuts verlagen bag-drag, missen UNI-lottery grotendeels tenzij long vóór de spike |
| Vooruit 30d | **−€80 / +€30 / +€140** |
| Trade-off | Meer gerealiseerde cuts in chop; minder kapitaal-deadlock |

### F) Momentum / AlphaI sleeve **zonder** never-loss religie (hard cut + time stop)

| Venster | Schatting |
|---------|-----------|
| Terugblik | Breed: **−€40 … +€200** afhankelijk van of UNI-achtige winners werden aangehouden |
| Vooruit 30d | **−€150 / +€10 / +€200** |
| Eerlijk | Hoge variantie; niet “beter” tenzij regime-filter + positie-sizing hard zijn |

### G) CVD / cross-venue mid-arb opnieuw live

| Venster | Schatting |
|---------|-----------|
| Evidence | Shadow LIVE_SHADOW **−€416** tot **−€13,6 k**; ~99% live reject |
| Vooruit | **Negatieve expectancy** onder TOB — **niet heractiveren** |

### H) “Ideale” velocity desk (ring vol, 20–40 exits/dag, 0,4–0,5% netto)

| Venster | Schatting |
|---------|-----------|
| Theoretisch | 20 × €55 × 0,45% ≈ **€5/dag**; 40 exits ≈ **€10/dag** → **€100–€200/maand** |
| Realiteit | Vereist util ≫ huidige ~19% **én** positieve edge na fees — **niet bewezen**. Docs noemen €20–50/dag expliciet aspirational |

---

## 6. Wat had de *grootste* winst kunnen zijn deze week?

Gerangschikt op **gemeten/credible** counterfactual (niet op wishful research):

1. **UNI buy&hold €2 000** → **+€756** (one-off lottery; niet herhaalbaar als forecast).
2. **UNI-bag ~€200 niet verkopen** → ~**+€76 MTM** i.p.v. −€4 FIFO (**≈ €80** beter op die positie).
3. **BTC of XRP hold €2 000** → **+€37…+€40** vs desk **−€59** (**≈ €95–€100** beter).
4. **Cash-only** → **€0** i.p.v. **−€59** (**€59** beter, zonder market beta).
5. **Huidige desk “perfect getuned”** had deze week **niet** +€50/dag kunnen printen zonder een andere alpha — de tape gaf majors ~2%, niet maker-spread.

---

## 7. Accurate vooruitblik (30 dagen) — samenvattingstabel

| Strategie | p25 | p50 | p75 | Aanbeveling |
|-----------|----:|----:|----:|-------------|
| A Status-quo velocity + mild UW | −€120 | −€30 | +€40 | Alleen aanhouden met **strikte kill** (−€75/week) |
| B Cash/vault-only | −€20 | €0 | +€20 | Tijdelijk als FIFO blijft rood |
| C BTC/ETH 50/50 | −€100 | +€40 | +€160 | Eenvoudigste “beta > desk”-pad |
| D Liquid-only MM | −€40 | +€25 | +€90 | Beste *trading*-alternatief als fills bewezen worden |
| E Trend-follow + hard SL | −€80 | +€30 | +€140 | Als never-loss thesis wordt verlaten |
| F Momentum no-NL | −€150 | +€10 | +€200 | Alleen met harde risk budget |
| G CVD live | n.v.t. | **neg** | n.v.t. | **Verboden** door evidence |
| H Ideal velocity | €0 | +€100 | +€200 | **Niet** als forecast tot stage-4 gates groen zijn |

---

## 8. Productconclusie

1. **Rode cijfers zijn reëel** — week −€59 FIFO, dag −€11, sessie Δ ≈ −€5.
2. **Architectuur faalt op expectancy, niet alleen op deadlock:** zelfs met free cash blijft de desk **negatief** t.o.v. triviale holds.
3. **Grootste structurele lek deze week:** UW-crystallisatie (SOL/UNI/XRP) + missen van beta (vooral UNI-rebound).
4. **€20–50/dag maker** blijft **onbewezen**; kill-ladder uit de velocity-docs is bijna getriggerd.
5. **Beste eerlijke keuzes nu:** (i) hybrid blijven met **harde weekly kill**, of (ii) **cash + BTC/ETH beta**, of (iii) **liquid-only MM** experiment met meetbare NET/hour — niet CVD, niet “meer unlocks zonder edge”.

---

## 9. Meetplan als je A of D nog 48–72u test

- Sleeve NET/hour, fills/hour, ring notional (niet scan emits).
- FIFO week vs −€75 kill.
- Aandeel PnL uit `mid_flat` / `lag_time_partial` vs trail harvest.
- Util: locked / €2 000 (doel ≥ 40% voordat velocity-claims).
- Benchmark parallel: paper BTC/ETH 50/50 op dezelfde clock.

*Einde autopsie.*

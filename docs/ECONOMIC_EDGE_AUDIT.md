# Economic edge audit (paper-only)

Generated from `data/paper_25000live.json` after the post-reset session
(~28 minutes runtime, 15 completed round-trips, realized NET ≈ **−€59.71**).

## A. What was actually wrong

1. **Gross opportunity evaporates on fill.** Fees match expected exactly; the
   loss is price. Expected gross ≈ +€65 across fills; after fees/slip the
   residual adverse ≈ **−€87**. Realized NET ≈ −€60.
2. **`EV = p_fill × NET` is invalid here.** Fleet config is
   `TRADE_THROUGH=1.0`, `QUEUE_FILL=0` → every fill is trade-through
   conditioned. Unconditional quote-time NET is not independent of the fill
   event.
3. **Adverse buffer too small vs markout.** Buffer ≈ 1 + 4 = 5 bps. Observed
   5s markout: median ≈ 11 bps, mean ≈ 21 bps; 8/20 bucket samples
   `very_toxic` (avg ≈ 52 bps).
4. **Calibration could not stop Bitvavo→Bitvavo.** Shrinkage to 1.0 with
   `prior_strength=40` keeps shrunk capture **positive** even at n=12 with
   raw capture ≈ −2.4. Classic hard gate needs ~20 samples; session ended at 12.
5. **Attribution bug.** `realized_adverse = expected − realized` was the EV
   gap, not waterfall adverse. Fixed (reporting).
6. **Markout ceiling 15 bps** could clip the gate when median toxicity rises
   above 15 (mean already did). Raised to 40 (more conservative).

## B. What was actually improved

| Change | Before | After | Evidence |
|--------|--------|-------|----------|
| Maker EV | `p_fill × NET` | `P(fill)×E(NET\|fill)` with extra conditional adverse | Unit tests + economics |
| Route stop | shrunk≤0 @ n≥20 | early raw stop @ n≥8, capture≤−0.25, loss≥€5 | Bitvavo counterfactual |
| Adverse ceiling | 15 bps | 40 bps | Config + fleet env |
| Waterfall | EV-gap as “adverse” | Identity: gross−fees−slip−adverse=NET | 15/15 identity_ok |
| Dashboard | NET KPIs only | Edge decomposition by route | `/paper/dashboard` |

### Bitvavo→Bitvavo counterfactual (observed → estimated)

| Metric | Value | Kind |
|--------|-------|------|
| Trades | 12 | observed |
| Expected NET | +€21.60 | observed |
| Realized NET | −€51.93 | observed |
| Early stop would fire at n | **8** | counterfactual |
| Classic shrunk gate at n | never (n&lt;20) | counterfactual |
| Loss avoided if early stop | **≈ €29.43** | counterfactual |
| Good trades blocked | **€0** | counterfactual |

## C. Opportunities now found that were missed earlier

Not yet proven on a fresh period. Conditional EV will *reject* more toxic
trade-through quotes (negative `E(NET|fill)`), which is prevention, not new
alpha discovery. Missed-winner / capacity experiments remain **hypotheses**.

## D. Bad trades now prevented

Going forward, with calibrator state restored from disk:

- `bitvavo→bitvavo` already has n=12, raw capture ≪ −0.25 → **early hard gate**.
- New toxic routes stop at n≥8 instead of bleeding to n=20+.

## E. Metric deltas (this session, observed)

| Metric | Value |
|--------|-------|
| NET €/fill | ≈ −€3.98 |
| EV capture | ≈ −2.22 (`sum(real)/sum(exp)`) |
| 5s markout (mean) | ≈ +21.5 bps adverse |
| Fees/fill | ≈ €2.20 |
| Drawdown driver | adverse selection, not fee model error |

Before/after on an **unseen** period is required before calling NET/fill
improvement proven (see G).

## F. Unproven hypotheses

1. Fair-value alternatives (microprice, depth-weighted, predictive) beat median mid on OOS markout.
2. Quote-width optimum for `P(fill)×E(NET|fill)` per regime/venue.
3. Capital velocity should use measured lock duration, not `quote_max_age_ms`.
4. Buy side is structurally more toxic than sell on Bitvavo (n small).
5. Regime-specific disable (high vol) improves NET/fill.
6. Caps (venue/strategy/correlation) are sub-optimal vs risk — only simulate, do not remove.
7. Stale quotes (`MAX_AGE_MS=60000`) are a primary toxicity driver.

## G. Next experiments (ranked by information × PnL impact)

1. **Walk-forward paper period** with early-stop + conditional EV frozen — measure NET€/fill vs prior code on matched fills.
2. **Stale-quote ablation**: `MAX_AGE_MS` 60s → 4s (execution realism; expect fewer toxic fills).
3. **Markout-conditioned sizing**: require NET buffer ≥ rolling p75 5s adverse per venue×side.
4. **Fair-value OOS harness** on recorded books (1s/5s/30s/60s markout).
5. **Asymmetric bid/ask edge** once n≥30 per side.

---

### Why expect better NET/fill on a new unseen period?

Because the previous model systematically **overstated EV on the exact fills
the simulator produces** (trade-through-only), and **refused to stop** a route
with raw capture &lt; −2 until ~20 samples. Fixing those is accounting/EV
correctness under the current simulator — not looser gates, not easier fills,
not redefined PnL.

Until a fresh walk-forward confirms higher NET€/fill, treat live ranking
changes as **experimentally justified**, not fully proven out-of-sample.

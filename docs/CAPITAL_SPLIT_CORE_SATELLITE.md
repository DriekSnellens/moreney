# Capital split: core vault + trend satellite

**Date:** 2026-09-06  
**Status:** Live session default

## Allocation (pocket €2 000)

| Sleeve | Fraction | € | Role |
|--------|---------:|--:|------|
| **Core** | 65% | €1 300 | Cash vault (default). Optional `btc_eth` marks BTC/ETH long-hold. |
| **Satellite** | 35% | €700 | Trend/momentum recycle; hard cut **2.5%**, early cut **1%**; daily sleeve loss cap ~**€21**. |

Per-venue active ring = satellite / N venues (e.g. €350 with Bitvavo+OKX).

## Why

Last ~3 days: majors were soft/negative while the full-pocket velocity desk stayed red. Split keeps most capital idle (or light beta) and only risk ~35% on a hard-SL satellite.

## Knobs

- `live_micro_capital_split_enabled` (default **true**)
- `live_micro_core_fraction` / `live_micro_satellite_fraction`
- `live_micro_core_mode`: `cash` | `btc_eth`
- Enforced in bridge: buys reject with `SLEEVE_SIZE_FULL` when micro locked ≥ satellite sleeve

## AlphaI daytrader (default on with split)

`live_micro_alphai_daytrader_enabled` (default **true** when capital split is on) turns the satellite into a sharp same-day rotator:

1. **Satellite only** takes risk (~35%); core stays cash.
2. **Entries**: AlphaI bullish + price-confirm (≥0.50, sleeve ≥0.40) + intraday gate; rising tape required for non-sleeve.
3. **Exits**: time/urgency-first for non-picks and weak/failed picks; hard cut **2.5%** + sleeve daily loss cap remain.
4. **Always-on non-pick rotate** under daytrader+split (not only FLAT / waiting targets) — still coin-agnostic (`not in bullish set`).

| Knob | Default | Role |
|------|--------:|------|
| `live_micro_daytrader_non_alphai_min_age_sec` | 120 | Free non-pick UW bags |
| `live_micro_daytrader_non_alphai_below_be_pct` | 0.5% | Mild UW depth for non-picks |
| `live_micro_daytrader_near_min_age_sec` | 90 | Near-BE recycle |
| `live_micro_daytrader_weak_alphai_min_age_sec` | 480 | Failed/weak pick recycle |
| `live_micro_daytrader_lag_time_min_age_sec` | 600 | Lag-time partial while targets wait |
| `live_micro_daytrader_provisional_be_exit_min_age_sec` | 300 | Provisional-cost BE+ unlock |

Measure: satellite NET/hour, pick hit-rate, non-pick inventory EUR → 0 while confirmed picks exist.

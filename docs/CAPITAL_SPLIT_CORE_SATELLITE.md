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

## Path to AlphaI daytrader

1. **Satellite only** takes risk (~35%); core stays cash.
2. **Entries**: only AlphaI bullish + price-confirm + intraday gate.
3. **Exits**: AlphaI urgency / trail / hard 2.5% cut; non-pick bags recycle first.
4. **FLAT daytrade rotate**: when split is on and FLAT (or unheld sleeve targets wait), non-AlphaI bags free faster (age/depth + lower BE-harvest floor) — still coin-agnostic (`not in bullish set`).

Measure: satellite NET/hour, pick hit-rate, non-pick inventory EUR → 0 while confirmed picks exist.

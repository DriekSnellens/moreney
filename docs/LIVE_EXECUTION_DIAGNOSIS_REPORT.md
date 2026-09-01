# Live Execution Diagnosis — ExchangeError & Buy Fill Gap

**Generated:** 2026-09-01T16:34:01.504702+00:00

Research-only analysis of `data/live_audit.jsonl`. No live trading logic changed.

## 1. Executive Summary

- **6143** `micro_order_exception` events in audit.
- Top error class: **OKX_CLORDID_REJECTED** (99.1%).
- Buy fills in audit: **0** vs sell fills: **90**.
- Bitvavo buys stuck at submitted: **575** (resting maker quotes).

## 2. ExchangeError Breakdown

| Category | Count | % | Sample |
|----------|------:|--:|--------|
| OKX_CLORDID_REJECTED | 6088 | 99.1 | okx {"code":"1","data":[{"clOrdId":"micro-00e4b9ead3ae4ae7","ordId":"","sCode":" |
| BITVAVO_CLIENT_ORDER_ID_INVALID | 36 | 0.59 | bitvavo {"errorCode":205,"error":"clientOrderId parameter is invalid."} |
| TRANSIENT_RETRY_EXHAUSTED | 18 | 0.29 | bitvavo.place_order failed after 5 attempts: ExchangeError |
| RATE_LIMIT_BAN | 1 | 0.02 | bitvavo {"errorCode":105,"error":"Your IP or API key has been banned for not res |

### Hourly spikes

| Hour | Exceptions | Submits | Ratio |
|------|----------:|--------:|------:|
| 2026-08-22T22 | 1537 | 1537 | 1.0 |
| 2026-08-22T23 | 1512 | 1512 | 1.0 |
| 2026-08-22T21 | 1493 | 1493 | 1.0 |
| 2026-08-22T16 | 664 | 666 | 0.997 |
| 2026-08-22T20 | 430 | 430 | 1.0 |
| 2026-08-22T17 | 211 | 211 | 1.0 |
| 2026-08-22T18 | 128 | 128 | 1.0 |
| 2026-08-23T00 | 73 | 99 | 0.737 |

**OKX clOrdId samples from rejections:** `micro-00e4b9ead3ae4ae7`, `micro-24516847cbe14eeb`, `micro-0f27e256f18647ce`, `micro-e6ca557dc55742fc`, `micro-274452ab933c4baf`

> OKX rejections dominate; audit messages show hyphenated clOrdId values (e.g. micro-<hex>), which violates OKX alphanumeric-only rules. Current ccxt_adapter.sanitize_okx_client_order_id strips hyphens, but this audit window predates or bypasses that path for bulk SOLEUR submits.

## 3. Buy Fill Gap Analysis

- Buy: submitted=575, filled=0, pending=348, cancelled=30
- Sell: submitted=488, filled=90, pending=234, cancelled=152
- Filled sell notional (audit): €4536.115563668261708697971958
- Submitted buy notional (audit): €27081.64393730788049010000000
- Bridge live_fill_count: 152
- Bridge backfill_mirrored_count: 153
- live_maker: True

### Venue × side × status

| Venue | Side | Status | Count |
|-------|------|--------|------:|
| bitvavo | buy | submitted | 575 |
| bitvavo | sell | submitted | 488 |
| okx | buy | pending | 348 |
| okx | sell | pending | 234 |
| bitvavo | sell | cancelled | 152 |
| bitvavo | sell | filled | 90 |
| bitvavo | buy | cancelled | 30 |

### Filled sell symbols

- SOLEUR: 17
- ARBEUR: 14
- TAOEUR: 10
- ATOMEUR: 8
- APTEUR: 8
- NEAREUR: 7
- UNIEUR: 6
- DOTEUR: 6
- ADAEUR: 2
- FETEUR: 2
- POLEUR: 2
- OPEUR: 2
- AAVEEUR: 2
- LTCEUR: 1
- SUIEUR: 1

### order_blocked (top)

- max open orders reached: 521
- max open orders reached for venue: 392
- Live order blocked: max open orders reached: 19
- notional 321.21158806159916490 exceeds max 150.0: 5
- notional 321.24886023473232795 exceeds max 150.0: 4
- notional 301.5163702307460 exceeds max 150.0: 3
- notional 301.240390136312 exceeds max 150.0: 3
- notional 301.5845535481944 exceeds max 150.0: 3
- notional 320.9134106765338605 exceeds max 150.0: 3
- notional 320.76432198400120830 exceeds max 150.0: 3

### Bridge skip counters (top)

- time_stop_below_be: 13217
- trail_dust: 2835
- focus_base_required: 2820
- trail_no_trusted_cost: 2517
- buy_quality_pause: 2380
- trail_hold_rising: 387
- momentum_block: 299
- corr_sector_momentum_block: 243
- exit_quote_mark_below_maker_be: 200
- trail_mark_spike: 62
- underwater_cross_venue_block: 56
- trail_peak_rewound: 30

### Root causes

- **[HIGH] MAKER_BUY_RESTING:** 575 Bitvavo buys logged as submitted (resting maker), 0 buy fills in micro_order_result audit; 90 sells filled.
- **[HIGH] OKX_BUY_STUCK_PENDING:** 348 OKX buy results stuck in pending — OKX path not producing exchange fills in this window.
- **[MEDIUM] MAX_OPEN_ORDERS:** Hundreds of order_blocked events (max open orders) — failed OKX submits and resting buys consume capacity.
- **[MEDIUM] INVENTORY_FROM_BACKFILL:** Bridge shows 152 live fills with 153 backfill-mirrored — sells likely exit pre-session / backfilled inventory, not session buys.

> micro_order_result logs the initial micro_engine response; resting buy fills mirrored via manage_resting_orders are not re-audited as filled buys.

> With live_maker=true, buys are post-only limits (resting); sells cross when bid >= break-even or on cut-loss taker path.

> Paper portfolio (live_micro_fullbot_state) may show €0 realized because _mirror_exchange_trade updates bridge FIFO only, not paper _fills.apply.

## 4. Interpretation

1. **OKX was effectively down** for this session window: ~99% of exceptions are `OKX_CLORDID_REJECTED`, concentrated in SOLEUR submit bursts (Aug 22).
2. **Buys and sells use different execution economics:** with `live_maker=true`, buys rest on the book (`submitted`); sells cross when profitable vs break-even.
3. **Zero buy fills in audit does not prove zero buy fills on exchange** — async resting fills are mirrored in bridge state without a second `micro_order_result`.
4. **Session PnL is driven by sell-down of existing inventory** (backfill-mirrored cost basis), not a balanced round-trip like research CVD.
5. **Paper vs live divergence** is amplified by strategy mismatch (CVD vs maker_inventory) and accounting split (bridge FIFO vs paper portfolio).


# Live readiness runbook (phases 0–5)

Moreney stays **fail-closed**. Real orders require multiple independent unlocks.
Withdrawals are never automatic.

## Phase overview

| Phase | Name | Orders? | Endpoint |
|------:|------|---------|----------|
| 0 | Go/no-go checklist | No | `GET /live/readiness` → `phase0_go_no_go` |
| 1 | Live observe | No | `GET /live/observe` |
| 2 | Execution scaffolding | No | readiness → `phase2_scaffolding` |
| 3 | Micro-live allowlist | Only if unlocked | readiness → `phase3_micro` |
| 4 | Alerts / rebalance advice | No auto-transfer | `GET /live/alerts` |
| 5 | Hardening / audit | N/A | `GET /live/audit` |

Also: `GET /live/status`, `GET /live/readiness` (full report).

## Phase 1 — Live observe setup

1. Create API keys on Bitvavo (+ optional Kraken/Binance/OKX) with **withdraw disabled**.
2. Copy `docs/live-observe.example.env` into your systemd/env file and fill secrets.
3. Restart the API process.
4. Check:
   - `GET /live/credentials` — which keys are present
   - `GET /live/credentials?probe=true` — read-only health/auth
   - `GET /live/observe` — balances (empty until keys work)
5. Keep `LIVE_*` unlock flags **false**.

## Phase 0 on paper / fleet

- `GET /paper/status` → `live_readiness.go_no_go_ready`
- `GET /fleet/api` → `live_readiness`
- Dashboard panel **Live readiness**

## Micro-live engine (wired, fail-closed)

Separate from PaperRunner:

| Step | Endpoint |
|------|----------|
| Status | `GET /live/micro/engine` |
| Unlock checklist | `GET /live/micro/unlock-checklist` |
| Dry-run | `POST /live/micro/dry-run` |
| Arm process | `POST /live/micro/arm` |
| Disarm | `POST /live/micro/disarm` |
| **Real order** | `POST /live/micro/orders` with `"confirm": true` |

Env unlocks (all required):

```ini
LIVE_TRADING_ENABLED=true
LIVE_MICRO_ENABLED=true
LIVE_ORDERS_UNLOCKED=true
LIVE_ALLOW_WITHOUT_RESEARCH_UNLOCK=true
AUTOMATIC_WITHDRAWALS_ENABLED=false
LIVE_MICRO_VENUES=bitvavo
LIVE_MICRO_SYMBOLS=BTCEUR,ETHEUR
LIVE_MICRO_MAX_NOTIONAL_EUR=50
```

Then restart the API, `POST /live/micro/arm`, then e.g.:

```json
{"venue":"bitvavo","symbol":"BTCEUR","side":"buy","notional_eur":25,"confirm":true}
```

PaperRunner is never coupled to this path.

## Safety flags (all must be true to place a live order)

```ini
LIVE_TRADING_ENABLED=true
LIVE_MICRO_ENABLED=true
LIVE_ORDERS_UNLOCKED=true
LIVE_ALLOW_WITHOUT_RESEARCH_UNLOCK=true   # only for controlled micro while research lock is on
AUTOMATIC_WITHDRAWALS_ENABLED=false       # must stay false
```

Research protocol still has `PRODUCTION_EXECUTION_ENABLED = false` in code.
PaperRunner never uses `MultiVenueLiveExecutor`.

## Funding path (unchanged)

Bank → SEPA → Bitvavo (main venue) → **manual** transfers to other venues → bot trades.
Use `/rebalancing/recommendations` and exchange UIs. Moreney does not withdraw.

## Incident actions

1. Emergency stop: `POST /risk/kill-switch/emergency-stop`
2. Set `LIVE_TRADING_ENABLED=false` / `LIVE_ORDERS_UNLOCKED=false` and restart
3. Withdraw cash via the exchange UI only
4. Inspect `GET /live/audit` for recent events

## Micro-live limits (defaults)

- Venues: `bitvavo,kraken`
- Symbols: `BTCEUR,ETHEUR`
- Max notional: €50 / order
- Max daily loss: €25
- Max open orders: 1

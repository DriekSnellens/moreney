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

## Micro unlock (after Phase 0+1)

1. `GET /live/micro/unlock-checklist` — see missing flags
2. `POST /live/micro/dry-run` with `{"venue":"bitvavo","symbol":"BTCEUR","notional_eur":25}`
3. Only if dry-run `policy_allows` and you accept risk: set unlocks in env (never via API)
4. PaperRunner still does not place live orders until wired separately

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

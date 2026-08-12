# Architecture

## Goals

Moreney separates **signal generation** from **costing**, **risk**, and **execution** so that:

- Strategies remain pure and testable (no exchange I/O).
- Expected edge is measured as **NET** profit after real-world costs.
- Risk is a hard gate — not an advisory check.
- Paper and live execution share one interface.
- Withdrawals are impossible by omission (no APIs, models, or routes).

## Data flow

```
┌─────────────┐     ┌────────────┐     ┌──────────────────┐
│ Market Data │────▶│ Strategies │────▶│ TradeOpportunity │
└─────────────┘     └────────────┘     └────────┬─────────┘
       ▲                                        │
       │ (via ExchangeClient)                   ▼
┌──────┴──────┐                        ┌────────────────┐
│  Exchanges  │                        │ Profitability  │
└──────┬──────┘                        │ (NET profit)   │
       │                               └────────┬───────┘
       │                                        │
       │                                        ▼
       │                               ┌────────────────┐
       │                               │  Risk Engine   │
       │                               │ (approve/deny) │
       │                               └────────┬───────┘
       │                                        │ approved only
       │                                        ▼
       └──────────────────────────────▶┌────────────────┐
                                       │   Execution    │
                                       │ Paper | Live   │
                                       └────────────────┘
```

`bot.engine.TradingEngine` wires this pipeline and **never** forwards rejected opportunities to an executor.

## Modules

### `bot.core`

- **Models**: `TradeOpportunity`, `ProfitabilityResult`, `RiskDecision`, `OrderRequest`, `ExecutionResult`, `MarketSnapshot`, `PortfolioSnapshot`
- **Interfaces**: `Protocol` / `ABC` contracts (`MarketDataProvider`, `Strategy`, `ProfitabilityEngine`, `RiskEngine`, `Executor`, `ExchangeClient`)
- **Config**: `Settings` via pydantic-settings (env-only secrets)
- **Enums / exceptions**: shared vocabulary

### `bot.market_data`

Provides normalized `MarketSnapshot` objects. May use `ExchangeClient` internally. Strategies depend on this layer’s interface, not on exchange SDKs.

### `bot.exchanges`

Adapters that speak to venues. Credentials from `EXCHANGE_API_KEY` / `EXCHANGE_API_SECRET` / etc.  
`ExchangeClient` exposes ticker, place order, and balances only — **no withdraw**.

### `bot.strategies`

Consume `MarketSnapshot` → emit `list[TradeOpportunity]`.  
`StubStrategy` is a scaffold that reacts to bid/ask spread.

`CrossExchangeArbitrageStrategy` consumes multi-exchange order books via
`evaluate_markets`, prices with depth VWAP (not tickers), runs candidates through
the profitability engine, and emits opportunities only when NET profit clears
configured EUR / percentage thresholds. Liquidity and latency checks apply;
rejections are logged. The strategy never executes trades.

### `bot.profitability`

`DefaultProfitabilityEngine` evaluates every opportunity via:

- `fee_calculator` — buy/sell fees with maker/taker roles
- `slippage` — base bps + order-book depth / market impact (+ thin-book penalty)
- `net_profit` — funding, execution-risk buffer, min absolute profit, min % return

```
NET = gross − buy_fee − sell_fee − slippage − funding − execution_buffer
trade_allowed ⇔ NET meets thresholds (never gross spread alone)
```

`ProfitEstimate` carries the full breakdown (`gross_profit`, fees, slippage,
`funding_cost`, `execution_buffer`, `net_profit`, `net_return`, `trade_allowed`).

Rates and gates come from settings (`PROFITABILITY_*`).

### `bot.risk`

Mandatory gate between profitability and execution:

```
Market Data → Strategy → Profitability → RiskEngine → Executor
```

- `RiskEngine` evaluates position size, portfolio %, total exposure, daily loss,
  drawdown, open positions, trades/minute, exchange health, liquidity, slippage,
  latency, stale data, and abnormal price moves.
- `KillSwitch` states: `RUNNING` | `WARNING` | `PAUSED` | `EMERGENCY_STOP`.
  `PAUSED` / `EMERGENCY_STOP` reject all new orders, log the reason, persist a
  `RiskEvent`, and expose state via `/risk/kill-switch`. No automatic resume.
- Never modifies/hides losing trades, never uses leverage, never withdrawals.
- Completely independent of exchange-specific SDKs (consumes `RiskContext`).

### `bot.execution` / `bot.portfolio`

Paper trading stack (isolated from live):

```
TradingEngine → RiskEngine → PaperExecutor → FillTracker → Portfolio → Accounting
```

- `PaperExecutor` simulates market/limit orders from order-book depth (or fixed %).
- Portfolio updates **only** from fills (idempotent via fill IDs).
- Starting capital: `PAPER_STARTING_EUR` (default €200).
- `LiveExecutor` remains disabled; paper path never uses credentials or places real orders.
- Persist orders, fills, portfolio snapshots, daily stats via SQLAlchemy models.

### `bot.portfolio`

Portfolio snapshots for risk evaluation (`InMemoryPortfolioService` for now).

### `bot.engine`

`TradingEngine.run_once(symbol)` runs the full cycle.

### `backtesting`

Replays historical snapshots through strategy → profitability → risk without live orders.

### `database`

Async SQLAlchemy base, session factory, and scaffold ORM models for opportunities / executions.

### `bot.main`

FastAPI app with `/health` and `/status`. Explicitly reports `withdrawals_supported: false`.

## Security notes

- Secrets only via environment variables / `.env` (gitignored).
- Live mode requires credentials and still keeps `LiveExecutor` disabled until adapters are production-ready.
- No withdrawal endpoints, models, or client methods.

## Extending

1. Add a real `ExchangeClient` subclass under `bot/exchanges/`.
2. Implement strategies under `bot/strategies/` (still no exchange imports).
3. Persist opportunities/executions via `database` models + Alembic.
4. Add Redis pub/sub or streams for market-data fan-out.
5. Build the Next.js dashboard against FastAPI.

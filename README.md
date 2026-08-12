# Moreney Trading System

Production-oriented cryptocurrency trading architecture (Python 3.12).

This repository now includes a complete **paper-trading runtime** with
WebSocket market data, risk-gated paper execution, persistence, and an
operator dashboard.

## Pipeline

```
Market Data → Strategy → TradeOpportunity → Profitability → Risk → Execution
                                                              (Paper | Live)
```

Architectural invariants:

1. **Strategies never call exchange APIs** — they only consume `MarketSnapshot` and emit `TradeOpportunity`.
2. **Profitability** computes expected **NET** profit after fees, slippage, funding, and execution buffer.
3. **Risk must approve every trade** before execution.
4. **No withdrawal functionality** exists anywhere in the application.
5. **Exchange credentials** come only from environment variables.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for layer details.

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| API | FastAPI |
| DB | PostgreSQL + SQLAlchemy (async) |
| Cache | Redis |
| Validation | Pydantic / pydantic-settings |
| Tests | pytest + pytest-asyncio |
| Orchestration | Docker Compose |

## Quick start

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
docker compose up --build
```

API health: `GET http://localhost:8000/health`

Paper operator views:

- Dashboard: `GET http://localhost:8000/paper/dashboard`
- Mobile dashboard: `GET http://localhost:8000/paper/dashboard-lite`
- Current overview: `GET http://localhost:8000/paper/overview`
- Status: `GET http://localhost:8000/paper/status`

## Package layout

```
bot/core          Shared models, interfaces, config, exceptions
bot/market_data   Normalized market snapshots for strategies
bot/exchanges     Exchange adapters (credentials from env; no withdrawals)
bot/strategies    Emit TradeOpportunity only
bot/profitability Expected NET profit engine
bot/risk          Mandatory pre-trade approval gate
bot/execution     PaperExecutor + LiveExecutor
bot/portfolio     Portfolio / balance abstractions
bot/engine        Pipeline orchestrator
backtesting       Historical replay scaffolding
database          SQLAlchemy models & sessions
tests             Unit tests for every core component
```

## Configuration

Copy `.env.example` to `.env`. Required variables are documented there. Never commit secrets.

## Safety boundaries

- Execution is paper-only by default (`EXECUTION_MODE=paper`)
- No withdrawals or transfer-out features
- No leverage support
- Risk approval is mandatory before every paper execution

Optional dashboard access control:

- `DASHBOARD_BASIC_AUTH_ENABLED=true`
- `DASHBOARD_BASIC_AUTH_USERNAME=<user>`
- `DASHBOARD_BASIC_AUTH_PASSWORD=<strong-password>`

## Deployment

See `DEPLOY.md` for VPS deployment with `systemd` and paper-only safeguards.

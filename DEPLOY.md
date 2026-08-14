# Deploy Moreney (Paper-Only) on VPS

This runbook deploys Moreney as a 24/7 **paper-trading-only** service.

## 1) Provision host

- Ubuntu 22.04+ (or similar Linux)
- 1 vCPU / 1-2 GB RAM is enough for paper runtime
- Open port `8000` only if you want public access (prefer reverse proxy + auth)

## 2) Create service user

```bash
sudo useradd -m -s /bin/bash moreney
sudo mkdir -p /opt/moreney
sudo chown -R moreney:moreney /opt/moreney
```

## 3) Install app

```bash
sudo -u moreney git clone <your-repo-url> /opt/moreney
cd /opt/moreney
sudo -u moreney python3 -m venv .venv
sudo -u moreney .venv/bin/pip install -e ".[dev]"
```

## 4) Configure paper-only environment

```bash
cd /opt/moreney
sudo -u moreney cp .env.example .env
```

Set these minimum values in `/opt/moreney/.env`:

```ini
EXECUTION_MODE=paper
PAPER_TRADING_ENABLED=true
PAPER_AUTO_START=true
PAPER_STARTING_EUR=200
```

Optional but recommended:

```ini
APP_DEBUG=false
LOG_LEVEL=INFO
API_HOST=0.0.0.0
API_PORT=8000
```

## 5) Install systemd services

Paper instances consume a **shared market-data publisher** over Redis
(`MARKET_DATA_MODE=shared`). Start Redis + publisher before the paper units.

```bash
sudo apt-get install -y redis-server
sudo systemctl enable --now redis-server

sudo cp deploy/systemd/moreney-marketdata.service /etc/systemd/system/
sudo cp deploy/systemd/moreney-paper@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now moreney-marketdata
sudo systemctl enable --now moreney-paper@200 moreney-paper@500 moreney-paper@1000 moreney-paper@5000 moreney-paper@25000 moreney-paper@25000live
```

Legacy single-instance unit `moreney-paper.service` remains available for local mode.

## 6) Verify service

```bash
sudo systemctl status moreney-paper
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/paper/status
curl -s http://127.0.0.1:8000/paper/overview
```

Dashboard:

- `http://<server>:8000/paper/dashboard`
- `http://<server>:8000/paper/dashboard-lite`

Optional dashboard auth (recommended for public exposure), add in `.env`:

```ini
DASHBOARD_BASIC_AUTH_ENABLED=true
DASHBOARD_BASIC_AUTH_USERNAME=operator
DASHBOARD_BASIC_AUTH_PASSWORD=<strong-password>
```

## 7) Operations

```bash
sudo systemctl restart moreney-paper
sudo systemctl stop moreney-paper
sudo journalctl -u moreney-paper -f
```

## 8) Update / rollback

Update:

```bash
cd /opt/moreney
sudo -u moreney git pull
sudo -u moreney .venv/bin/pip install -e ".[dev]"
sudo systemctl restart moreney-paper
```

Rollback:

```bash
cd /opt/moreney
sudo -u moreney git checkout <previous-tag-or-commit>
sudo -u moreney .venv/bin/pip install -e ".[dev]"
sudo systemctl restart moreney-paper
```

## Safety checklist

- `EXECUTION_MODE=paper`
- `PAPER_TRADING_ENABLED=true`
- `live_trading_enabled=false` in `GET /status`
- `withdrawals_supported=false` in `GET /status`
- `leverage_supported=false` in `GET /status`
- Dashboard endpoints protected when exposed publicly

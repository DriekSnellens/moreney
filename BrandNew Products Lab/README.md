# BrandNew Products Lab

Private lab for one operator. It finds European product hypotheses, prices them with a full cost stack, and keeps a product in research until actual net profit says otherwise.

This folder is the project. The Moreney trading bot in the rest of the repository is separate and is not started by these scripts.

## Run

```bash
cd "BrandNew Products Lab"
npm install
npm run dev
```

Open http://localhost:3000. With `DATA_MODE` unset, the workspace is demo data and says so. Nothing is published and no ad spend is sent.

```bash
npm test
npm run typecheck
npm run lint
npm run test:python
```

Apply the database when you have Postgres:

```bash
psql "$DATABASE_URL" -f db/migrations/001_init.sql
# optional, only if pgvector is installed
psql "$DATABASE_URL" -f db/migrations/002_pgvector.sql
```

Copy `.env.example` to `.env.local` before adding keys.

## First path

Discover → open the car-hair opportunity → read the profit model → save assumptions → Test this product.

The seeded test is intentionally unprofitable on net profit while ROAS can look acceptable. That is the point of the screen.

## Python worker

`workers/python/trendmath.py` matches `services/trend-math.ts`. The FastAPI app is optional:

```bash
pip install -r workers/python/requirements.txt
uvicorn app:app --app-dir workers/python
```

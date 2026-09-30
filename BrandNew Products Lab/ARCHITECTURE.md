# Architecture

BrandNew Products Lab is a private modular monolith for one operator. It lives in this folder and does not touch the Moreney trading bot.

The loop the software is built around:

Find → Validate → Launch → Advertise → Measure → Optimize → Scale → Repeat

Success is positive net profit after product cost, shipping, VAT, payment fees, ads, refunds, returns, discounts, supplier fees, operational cost, and an explicit execution buffer. A trend is not a winning product.

## Boundaries

- `app/` — Next.js App Router pages, server actions, webhooks.
- `components/` — shell, forms, and display primitives.
- `services/` — profit, opportunity scoring, tests, creatives, brand drafts, analyst. Pure where they can be tested without a server.
- `providers/` — adapters for AI, video, Shopify, ads, trends, and suppliers. Missing credentials return “Provider not configured”.
- `workflows/` — an in-process runner for jobs that must not depend on the browser. Temporal is a named runner, not wired, so the business steps do not need a rewrite later.
- `db/migrations/` — PostgreSQL schema, including an optional pgvector migration.
- `workers/python/` — the same trend slope used by the TypeScript service, exposed later through FastAPI.
- `lib/` — environment, demo workspace, file repository.

Organization, user, brand, store, product, and campaign are separate records so a tenant boundary can be added later. There is no billing, team management, or public signup.

## Data modes

`DATA_MODE=demo` (the default) loads a fictional workspace, labeled Demo data on screen. Edits persist in `.data/demo-state.json`.

`DATA_MODE=live` does not load that workspace. Providers without credentials stay empty. `DATABASE_URL` is the intended Postgres connection; until a process is pointed at it, live edits are in memory for that process only.

Demo records and live records are not merged.

## Money

`services/profit.ts` is the only profit formula. Assumptions are stored with the opportunity and the test. Actual revenue, ad spend, and product cost replace the matching estimates. Other lines stay estimates and say so.

A product test cannot move to Profitable until actual revenue, ad spend, and product cost exist, net profit is positive, and at least 30 orders are recorded. That threshold is an operator gate, not a significance test.

## Compliance

Products with missing documents or an explicit review flag cannot move toward a paid test or a Shopify draft. “No flags in stored data” is not a legal clearance.

## What is deliberately not automatic

- Products are not published. Shopify receives `DRAFT` only after an approval checkbox, and only in live mode when credentials exist.
- Ad platforms are not called when a campaign is approved. Meta writes additionally require `META_ALLOW_WRITE`.
- Video providers are not called from the demo renderer. A demo job reaches Ready without a media file.
- The analyst does not claim causes.

## Workflows

Long work (video job progress, later discovery and sync) goes through `workflows/loop.ts`, started from `instrumentation.ts` inside the Node server. The tick route can advance jobs without a browser staying open. Setting `WORKFLOW_RUNNER=temporal` is reserved; the definitions stay in the service layer.

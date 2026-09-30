# TODO

## Phase 1 — Foundation

- [x] Next.js, TypeScript, Tailwind, App Router
- [x] PostgreSQL schema and a migration test
- [x] Operator password gate
- [x] Navigation, command palette, light and dark theme
- [x] Environment example
- [x] Provider interfaces and an explicit unconfigured state
- [x] Demo workspace, labeled, separate from live mode

## Phase 2 — Product intelligence

- [x] Products, niches, suppliers, trend series
- [x] Opportunity score with a written reason on every factor
- [x] Evidence stages that do not call a trend a profitable product
- [x] Profit calculator with stored assumptions and actual overrides
- [x] Opportunity report and the test-this-product step

## Phase 3 — Brand and Shopify

- [x] Brand draft without fake reviews or urgency
- [x] Product page draft
- [x] Shopify GraphQL draft adapter
- [x] Approval before a draft is stored or sent
- [x] Signed webhook for orders/create
- [ ] Persist live mode through the relational tables (schema is ready; the running path is the demo file or an in-memory live shell)

## Phase 4 — Creative and video

- [x] Hooks, angles, scripts, headlines, CTAs
- [x] Video concept before a render
- [x] HeyGen and Creatify adapters
- [x] Async job states: queued, generating, ready, failed
- [ ] Store rendered files in Cloudflare R2 when a paid render is intentionally enabled

## Phase 5 — Advertising

- [x] Meta and TikTok provider shells
- [x] Draft campaign, ad set, ad, and UTM identifiers
- [x] Local approval that does not spend
- [x] Metric fields for spend, clicks, purchases, revenue, and ROAS beside net profit
- [ ] Scheduled insight import once an operator unlocks a paused Meta write

## Phase 6 — Optimization

- [x] Experiment record with an uncertainty note
- [x] Rules analyst that refuses causal language
- [x] Product test state machine
- [x] Profit page
- [ ] Live model rewrite of the analyst when an AI key is set and the operator asks

## Not in scope until the lab makes money

Billing, teams, public registration, a customer API, and a marketing site.

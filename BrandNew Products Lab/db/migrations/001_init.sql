-- BrandNew Products Lab. Single-operator schema with organization_id
-- so a later tenant boundary does not require a rewrite.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS organizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  email text NOT NULL UNIQUE,
  name text NOT NULL,
  role text NOT NULL DEFAULT 'operator',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS niches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  target_audience text NOT NULL DEFAULT '',
  core_problem text NOT NULL DEFAULT '',
  buying_motivation text NOT NULL DEFAULT '',
  price_range_min double precision,
  price_range_max double precision,
  currency text NOT NULL DEFAULT 'EUR',
  seasonality text NOT NULL DEFAULT '',
  geographic_relevance text NOT NULL DEFAULT '',
  why_interesting text NOT NULL DEFAULT '',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  niche_id uuid REFERENCES niches (id),
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  compliance_status text NOT NULL,
  compliance_notes text NOT NULL DEFAULT '',
  product_documents text NOT NULL DEFAULT '',
  safety_information text NOT NULL DEFAULT '',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product_variants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id uuid NOT NULL REFERENCES products (id),
  sku text NOT NULL,
  name text NOT NULL,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS suppliers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  name text NOT NULL,
  country text NOT NULL DEFAULT '',
  warehouse_country text NOT NULL DEFAULT '',
  shipping_days_min integer,
  shipping_days_max integer,
  return_location text NOT NULL DEFAULT '',
  vat_information text NOT NULL DEFAULT '',
  verification_status text NOT NULL DEFAULT 'unverified',
  product_documents text NOT NULL DEFAULT '',
  safety_information text NOT NULL DEFAULT '',
  provenance text NOT NULL,
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS supplier_products (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  supplier_id uuid NOT NULL REFERENCES suppliers (id),
  product_id uuid NOT NULL REFERENCES products (id),
  supplier_sku text NOT NULL DEFAULT '',
  product_cost double precision NOT NULL,
  shipping_cost double precision NOT NULL,
  currency text NOT NULL DEFAULT 'EUR',
  moq integer,
  eu_warehouse boolean NOT NULL DEFAULT false,
  lead_time_days integer,
  url text NOT NULL DEFAULT '',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trend_signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  niche_id uuid REFERENCES niches (id),
  product_id uuid REFERENCES products (id),
  source text NOT NULL,
  query text NOT NULL,
  series jsonb NOT NULL DEFAULT '[]'::jsonb,
  series_note text NOT NULL DEFAULT '',
  observed_at timestamptz NOT NULL,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  product_id uuid NOT NULL REFERENCES products (id),
  source text NOT NULL,
  metric text NOT NULL,
  value text NOT NULL,
  note text NOT NULL DEFAULT '',
  is_estimate boolean NOT NULL DEFAULT true,
  observed_at timestamptz NOT NULL,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS competition_signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  product_id uuid NOT NULL REFERENCES products (id),
  intensity_score double precision NOT NULL,
  note text NOT NULL,
  is_estimate boolean NOT NULL DEFAULT true,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS opportunities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  product_id uuid NOT NULL REFERENCES products (id),
  niche_id uuid NOT NULL REFERENCES niches (id),
  supplier_product_id uuid REFERENCES supplier_products (id),
  stage text NOT NULL,
  assumptions jsonb NOT NULL,
  actuals jsonb,
  inputs jsonb NOT NULL,
  positioning text NOT NULL DEFAULT '',
  positioning_kind text NOT NULL DEFAULT 'estimate',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS brands (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  opportunity_id uuid NOT NULL REFERENCES opportunities (id),
  name text NOT NULL,
  name_note text NOT NULL DEFAULT '',
  positioning text NOT NULL DEFAULT '',
  target_audience text NOT NULL DEFAULT '',
  personality text NOT NULL DEFAULT '',
  color_direction text NOT NULL DEFAULT '',
  typography_direction text NOT NULL DEFAULT '',
  tagline text NOT NULL DEFAULT '',
  homepage_concept text NOT NULL DEFAULT '',
  product_page_structure jsonb NOT NULL DEFAULT '[]'::jsonb,
  faq jsonb NOT NULL DEFAULT '[]'::jsonb,
  trust_messaging jsonb NOT NULL DEFAULT '[]'::jsonb,
  bundle_ideas jsonb NOT NULL DEFAULT '[]'::jsonb,
  upsell_ideas jsonb NOT NULL DEFAULT '[]'::jsonb,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stores (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  platform text NOT NULL,
  name text NOT NULL,
  external_domain text NOT NULL DEFAULT '',
  status text NOT NULL,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS store_products (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  store_id uuid NOT NULL REFERENCES stores (id),
  product_id uuid NOT NULL REFERENCES products (id),
  brand_id uuid REFERENCES brands (id),
  external_id text,
  status text NOT NULL,
  seo_title text NOT NULL DEFAULT '',
  seo_description text NOT NULL DEFAULT '',
  body_html text NOT NULL DEFAULT '',
  approved_at timestamptz,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product_tests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  product_id uuid NOT NULL REFERENCES products (id),
  niche_id uuid NOT NULL REFERENCES niches (id),
  brand_id uuid REFERENCES brands (id),
  opportunity_id uuid NOT NULL REFERENCES opportunities (id),
  landing_path text NOT NULL,
  test_budget double precision NOT NULL,
  currency text NOT NULL DEFAULT 'EUR',
  starts_on date NOT NULL,
  ends_on date NOT NULL,
  status text NOT NULL,
  kpis jsonb NOT NULL DEFAULT '[]'::jsonb,
  result_summary text NOT NULL DEFAULT '',
  actuals jsonb,
  assumptions jsonb NOT NULL,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS creative_concepts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  product_id uuid NOT NULL REFERENCES products (id),
  product_test_id uuid REFERENCES product_tests (id),
  angle text NOT NULL,
  hook text NOT NULL,
  script text NOT NULL,
  primary_text text NOT NULL,
  headline text NOT NULL,
  cta text NOT NULL,
  disclaimer text NOT NULL DEFAULT '',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS creative_assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  concept_id uuid NOT NULL REFERENCES creative_concepts (id),
  kind text NOT NULL,
  r2_key text,
  url text,
  mime_type text,
  note text NOT NULL DEFAULT '',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS video_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  product_id uuid NOT NULL REFERENCES products (id),
  provider text NOT NULL,
  status text NOT NULL,
  concept jsonb NOT NULL,
  error text,
  asset_url text,
  note text NOT NULL DEFAULT '',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ad_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  provider text NOT NULL,
  external_id text NOT NULL DEFAULT '',
  name text NOT NULL,
  currency text NOT NULL DEFAULT 'EUR',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS campaigns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  ad_account_id uuid REFERENCES ad_accounts (id),
  product_id uuid NOT NULL REFERENCES products (id),
  product_test_id uuid REFERENCES product_tests (id),
  creative_concept_id uuid REFERENCES creative_concepts (id),
  name text NOT NULL,
  status text NOT NULL,
  budget double precision NOT NULL,
  currency text NOT NULL DEFAULT 'EUR',
  landing_url text NOT NULL DEFAULT '',
  tracking jsonb NOT NULL DEFAULT '{}'::jsonb,
  approved_at timestamptz,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ad_sets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id uuid NOT NULL REFERENCES campaigns (id),
  name text NOT NULL,
  audience text NOT NULL DEFAULT '',
  budget double precision NOT NULL,
  status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ad_set_id uuid NOT NULL REFERENCES ad_sets (id),
  creative_concept_id uuid REFERENCES creative_concepts (id),
  name text NOT NULL,
  status text NOT NULL,
  tracking jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ad_metrics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ad_id uuid NOT NULL REFERENCES ads (id),
  day date NOT NULL,
  spend double precision NOT NULL,
  impressions integer NOT NULL,
  clicks integer NOT NULL,
  add_to_carts integer NOT NULL,
  purchases integer NOT NULL,
  revenue double precision NOT NULL,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS experiments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  product_test_id uuid NOT NULL REFERENCES product_tests (id),
  name text NOT NULL,
  hypothesis text NOT NULL,
  status text NOT NULL,
  budget double precision NOT NULL,
  start_date date,
  end_date date,
  result text NOT NULL DEFAULT '',
  uncertainty_note text NOT NULL DEFAULT '',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS experiment_variants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id uuid NOT NULL REFERENCES experiments (id),
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  impressions integer NOT NULL DEFAULT 0,
  clicks integer NOT NULL DEFAULT 0,
  purchases integer NOT NULL DEFAULT 0,
  spend double precision NOT NULL DEFAULT 0,
  revenue double precision NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  store_id uuid REFERENCES stores (id),
  external_id text,
  currency text NOT NULL DEFAULT 'EUR',
  gross_total double precision NOT NULL,
  discount_total double precision NOT NULL DEFAULT 0,
  vat_total double precision NOT NULL DEFAULT 0,
  status text NOT NULL,
  ordered_at timestamptz NOT NULL,
  campaign_id text,
  creative_id text,
  product_id uuid REFERENCES products (id),
  experiment_id text,
  utm jsonb NOT NULL DEFAULT '{}'::jsonb,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id uuid NOT NULL REFERENCES orders (id),
  product_id uuid NOT NULL REFERENCES products (id),
  variant_id uuid REFERENCES product_variants (id),
  quantity integer NOT NULL,
  unit_price double precision NOT NULL,
  unit_cogs double precision NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS refunds (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id uuid NOT NULL REFERENCES orders (id),
  amount double precision NOT NULL,
  reason text NOT NULL DEFAULT '',
  refunded_at timestamptz NOT NULL,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS profit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  product_id uuid NOT NULL REFERENCES products (id),
  product_test_id uuid REFERENCES product_tests (id),
  kind text NOT NULL,
  net_profit double precision NOT NULL,
  occurred_on date NOT NULL,
  note text NOT NULL DEFAULT '',
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  purpose text NOT NULL,
  provider text NOT NULL,
  input_summary text NOT NULL DEFAULT '',
  output jsonb NOT NULL,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  name text NOT NULL,
  status text NOT NULL,
  input jsonb NOT NULL DEFAULT '{}'::jsonb,
  output jsonb,
  error text,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS daily_metrics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations (id),
  day date NOT NULL,
  product_id uuid REFERENCES products (id),
  spend double precision NOT NULL DEFAULT 0,
  revenue double precision NOT NULL DEFAULT 0,
  net_profit double precision NOT NULL DEFAULT 0,
  orders integer NOT NULL DEFAULT 0,
  opportunities_discovered integer NOT NULL DEFAULT 0,
  provenance text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS niches_org_idx ON niches (organization_id);
CREATE INDEX IF NOT EXISTS products_org_idx ON products (organization_id);
CREATE INDEX IF NOT EXISTS products_niche_idx ON products (niche_id);
CREATE INDEX IF NOT EXISTS opportunities_product_idx ON opportunities (product_id);
CREATE INDEX IF NOT EXISTS product_tests_status_idx ON product_tests (status);
CREATE INDEX IF NOT EXISTS ad_metrics_day_idx ON ad_metrics (ad_id, day);
CREATE INDEX IF NOT EXISTS orders_ordered_idx ON orders (ordered_at);
CREATE INDEX IF NOT EXISTS daily_metrics_day_idx ON daily_metrics (organization_id, day);
CREATE INDEX IF NOT EXISTS video_jobs_status_idx ON video_jobs (status);

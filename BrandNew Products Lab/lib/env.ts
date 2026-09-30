export type DataMode = "demo" | "live";

export interface LabEnv {
  dataMode: DataMode;
  databaseUrl: string | null;
  operatorPassword: string | null;
  operatorSessionSecret: string | null;
  openaiApiKey: string | null;
  anthropicApiKey: string | null;
  openaiModel: string;
  anthropicModel: string;
  supabaseUrl: string | null;
  shopifyStoreUrl: string | null;
  shopifyAccessToken: string | null;
  shopifyApiVersion: string;
  shopifyWebhookSecret: string | null;
  creatifyApiKey: string | null;
  creatifyApiId: string | null;
  heygenApiKey: string | null;
  heygenAvatarId: string | null;
  heygenVoiceId: string | null;
  metaAccessToken: string | null;
  metaAdAccountId: string | null;
  tiktokAccessToken: string | null;
  r2Endpoint: string | null;
  r2AccessKey: string | null;
  r2SecretKey: string | null;
  r2Bucket: string | null;
  posthogKey: string | null;
  temporalAddress: string | null;
  trendsApiUrl: string | null;
  supplierFeedUrl: string | null;
  workflowRunner: "in-process" | "temporal";
}

function read(name: string): string | null {
  const value = process.env[name];
  if (!value || !value.trim()) return null;
  return value.trim();
}

export function getLabEnv(): LabEnv {
  const requested = read("DATA_MODE");
  const dataMode: DataMode = requested === "live" ? "live" : "demo";
  return {
    dataMode,
    databaseUrl: read("DATABASE_URL"),
    operatorPassword: read("OPERATOR_PASSWORD"),
    operatorSessionSecret: read("OPERATOR_SESSION_SECRET"),
    openaiApiKey: read("OPENAI_API_KEY"),
    anthropicApiKey: read("ANTHROPIC_API_KEY"),
    openaiModel: read("OPENAI_MODEL") ?? "gpt-4o-mini",
    anthropicModel: read("ANTHROPIC_MODEL") ?? "claude-sonnet-4-5",
    supabaseUrl: read("SUPABASE_URL"),
    shopifyStoreUrl: read("SHOPIFY_STORE_URL"),
    shopifyAccessToken: read("SHOPIFY_ACCESS_TOKEN"),
    shopifyApiVersion: read("SHOPIFY_API_VERSION") ?? "2025-01",
    shopifyWebhookSecret: read("SHOPIFY_WEBHOOK_SECRET"),
    creatifyApiKey: read("CREATIFY_API_KEY"),
    creatifyApiId: read("CREATIFY_API_ID"),
    heygenApiKey: read("HEYGEN_API_KEY"),
    heygenAvatarId: read("HEYGEN_AVATAR_ID"),
    heygenVoiceId: read("HEYGEN_VOICE_ID"),
    metaAccessToken: read("META_ACCESS_TOKEN"),
    metaAdAccountId: read("META_AD_ACCOUNT_ID"),
    tiktokAccessToken: read("TIKTOK_ACCESS_TOKEN"),
    r2Endpoint: read("CLOUDFLARE_R2_ENDPOINT"),
    r2AccessKey: read("CLOUDFLARE_R2_ACCESS_KEY"),
    r2SecretKey: read("CLOUDFLARE_R2_SECRET_KEY"),
    r2Bucket: read("CLOUDFLARE_R2_BUCKET"),
    posthogKey: read("POSTHOG_KEY"),
    temporalAddress: read("TEMPORAL_ADDRESS"),
    trendsApiUrl: read("TRENDS_API_URL"),
    supplierFeedUrl: read("SUPPLIER_FEED_URL"),
    workflowRunner: read("WORKFLOW_RUNNER") === "temporal" ? "temporal" : "in-process",
  };
}

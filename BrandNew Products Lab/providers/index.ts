import type { LabEnv } from "@/lib/env";
import type { ProviderStatus, TrendSignal, Unavailable } from "@/types/domain";

export interface TrendQuery {
  query: string;
}

export interface TrendProvider {
  status(): ProviderStatus;
  search(input: TrendQuery): Promise<TrendSignal[] | Unavailable>;
}

export interface SupplierRecord {
  name: string;
  country: string;
  warehouseCountry: string;
  note: string;
}

export interface SupplierProvider {
  status(): ProviderStatus;
  list(): Promise<SupplierRecord[] | Unavailable>;
}

export interface MarketProvider {
  status(): ProviderStatus;
  lookup(query: string): Promise<{ note: string } | Unavailable>;
}

export interface AiProvider {
  id: string;
  status(): ProviderStatus;
  complete(input: { system: string; prompt: string }): Promise<{ text: string; provider: string } | Unavailable>;
}

export interface VideoCreateInput {
  script: string;
  aspectRatio: string;
  language: string;
}

export interface VideoProvider {
  id: "mock" | "creatify" | "heygen";
  status(): ProviderStatus;
  create(input: VideoCreateInput): Promise<{ externalId: string; status: "QUEUED" } | Unavailable>;
}

export interface StoreDraftInput {
  title: string;
  descriptionHtml: string;
  seoTitle: string;
  seoDescription: string;
  approvedByOperator: boolean;
}

export interface StoreProvider {
  status(): ProviderStatus;
  createDraft(input: StoreDraftInput): Promise<{ externalId: string; status: "DRAFT" } | Unavailable | { ok: false; message: string }>;
}

export interface AdDraftInput {
  name: string;
  dailyBudget: number;
  approvedByOperator: boolean;
}

export interface AdProvider {
  id: string;
  status(): ProviderStatus;
  createPausedCampaign(input: AdDraftInput): Promise<{ externalId: string } | Unavailable | { ok: false; message: string }>;
}

function unconfigured(id: string, name: string): ProviderStatus {
  return {
    id,
    name,
    configured: false,
    mode: "unconfigured",
    message: "Provider not configured",
  };
}

function unavailable(providerId: string): Unavailable {
  return { ok: false, message: "Provider not configured", providerId };
}

export class UnconfiguredTrendProvider implements TrendProvider {
  status(): ProviderStatus {
    return unconfigured("trends", "Trends");
  }
  async search(input: TrendQuery): Promise<Unavailable> {
    return unavailable(input.query ? "trends" : "trends");
  }
}

export class UnconfiguredSupplierProvider implements SupplierProvider {
  status(): ProviderStatus {
    return unconfigured("suppliers", "Suppliers");
  }
  async list(): Promise<Unavailable> {
    return unavailable("suppliers");
  }
}

export class UnconfiguredMarketProvider implements MarketProvider {
  status(): ProviderStatus {
    return unconfigured("market", "Market data");
  }
  async lookup(): Promise<Unavailable> {
    return unavailable("market");
  }
}

export class HttpTrendProvider implements TrendProvider {
  constructor(private readonly url: string) {}
  status(): ProviderStatus {
    return { id: "trends", name: "Trends feed", configured: true, mode: "live", message: "TRENDS_API_URL is set." };
  }
  async search(input: TrendQuery): Promise<TrendSignal[] | Unavailable> {
    const response = await fetch(this.url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: input.query }),
    });
    if (!response.ok) {
      throw new Error(`Trends feed returned ${response.status}.`);
    }
    const body = (await response.json()) as TrendSignal[];
    return body;
  }
}

export class HttpSupplierProvider implements SupplierProvider {
  constructor(private readonly url: string) {}
  status(): ProviderStatus {
    return {
      id: "suppliers",
      name: "Supplier feed",
      configured: true,
      mode: "live",
      message: "SUPPLIER_FEED_URL is set.",
    };
  }
  async list(): Promise<SupplierRecord[] | Unavailable> {
    const response = await fetch(this.url);
    if (!response.ok) throw new Error(`Supplier feed returned ${response.status}.`);
    return (await response.json()) as SupplierRecord[];
  }
}

export class OpenAiProvider implements AiProvider {
  id = "openai";
  constructor(
    private readonly apiKey: string,
    private readonly model: string,
  ) {}
  status(): ProviderStatus {
    return { id: "openai", name: "OpenAI", configured: true, mode: "live", message: "OPENAI_API_KEY is set." };
  }
  async complete(input: { system: string; prompt: string }) {
    const { generateText } = await import("ai");
    const { createOpenAI } = await import("@ai-sdk/openai");
    const provider = createOpenAI({ apiKey: this.apiKey });
    const result = await generateText({
      model: provider(this.model),
      system: input.system,
      prompt: input.prompt,
    });
    return { text: result.text, provider: "openai" };
  }
}

export class AnthropicProvider implements AiProvider {
  id = "anthropic";
  constructor(
    private readonly apiKey: string,
    private readonly model: string,
  ) {}
  status(): ProviderStatus {
    return {
      id: "anthropic",
      name: "Anthropic",
      configured: true,
      mode: "live",
      message: "ANTHROPIC_API_KEY is set.",
    };
  }
  async complete(input: { system: string; prompt: string }) {
    const { generateText } = await import("ai");
    const { createAnthropic } = await import("@ai-sdk/anthropic");
    const provider = createAnthropic({ apiKey: this.apiKey });
    const result = await generateText({
      model: provider(this.model),
      system: input.system,
      prompt: input.prompt,
    });
    return { text: result.text, provider: "anthropic" };
  }
}

export class UnconfiguredAiProvider implements AiProvider {
  id = "ai";
  status(): ProviderStatus {
    return unconfigured("ai", "Language model");
  }
  async complete(): Promise<Unavailable> {
    return unavailable("ai");
  }
}

export class HeyGenVideoProvider implements VideoProvider {
  id = "heygen" as const;
  constructor(
    private readonly apiKey: string,
    private readonly avatarId: string | null,
    private readonly voiceId: string | null,
  ) {}
  status(): ProviderStatus {
    const ready = Boolean(this.avatarId && this.voiceId);
    return {
      id: "heygen",
      name: "HeyGen",
      configured: ready,
      mode: ready ? "live" : "unconfigured",
      message: ready ? "HeyGen key, avatar, and voice are set." : "Provider not configured",
    };
  }
  async create(input: VideoCreateInput) {
    if (!this.avatarId || !this.voiceId) return unavailable("heygen");
    const [width, height] = dimensions(input.aspectRatio);
    const response = await fetch("https://api.heygen.com/v2/video/generate", {
      method: "POST",
      headers: { "content-type": "application/json", "x-api-key": this.apiKey },
      body: JSON.stringify({
        video_inputs: [
          {
            character: { type: "avatar", avatar_id: this.avatarId, avatar_style: "normal" },
            voice: { type: "text", input_text: input.script, voice_id: this.voiceId },
          },
        ],
        dimension: { width, height },
      }),
    });
    if (!response.ok) {
      throw new Error(`HeyGen returned ${response.status}.`);
    }
    const body = (await response.json()) as { data?: { video_id?: string } };
    const externalId = body.data?.video_id;
    if (!externalId) throw new Error("HeyGen did not return a video id.");
    return { externalId, status: "QUEUED" as const };
  }
}

export class CreatifyVideoProvider implements VideoProvider {
  id = "creatify" as const;
  constructor(
    private readonly apiId: string,
    private readonly apiKey: string,
  ) {}
  status(): ProviderStatus {
    return { id: "creatify", name: "Creatify", configured: true, mode: "live", message: "Creatify credentials are set." };
  }
  async create(input: VideoCreateInput) {
    const response = await fetch("https://api.creatify.ai/api/ai_scripts/", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-id": this.apiId,
        "x-api-key": this.apiKey,
      },
      body: JSON.stringify({
        prompt: input.script,
        language: input.language,
        aspect_ratio: input.aspectRatio,
      }),
    });
    if (!response.ok) throw new Error(`Creatify returned ${response.status}.`);
    const body = (await response.json()) as { id?: string };
    if (!body.id) throw new Error("Creatify did not return an id.");
    return { externalId: body.id, status: "QUEUED" as const };
  }
}

export class ShopifyStoreProvider implements StoreProvider {
  constructor(
    private readonly storeUrl: string,
    private readonly token: string,
    private readonly apiVersion: string,
  ) {}
  status(): ProviderStatus {
    return { id: "shopify", name: "Shopify", configured: true, mode: "live", message: "Shopify credentials are set." };
  }
  async createDraft(input: StoreDraftInput) {
    if (!input.approvedByOperator) {
      return { ok: false as const, message: "A draft is not sent until the operator approves it." };
    }
    const endpoint = `https://${this.storeUrl.replace(/^https?:\/\//, "")}/admin/api/${this.apiVersion}/graphql.json`;
    const query = `
      mutation CreateDraft($product: ProductCreateInput!) {
        productCreate(product: $product) {
          product { id status }
          userErrors { field message }
        }
      }
    `;
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-shopify-access-token": this.token,
      },
      body: JSON.stringify({
        query,
        variables: {
          product: {
            title: input.title,
            descriptionHtml: input.descriptionHtml,
            status: "DRAFT",
            seo: { title: input.seoTitle, description: input.seoDescription },
          },
        },
      }),
    });
    if (!response.ok) throw new Error(`Shopify returned ${response.status}.`);
    const body = (await response.json()) as {
      data?: { productCreate?: { product?: { id: string }; userErrors?: { message: string }[] } };
    };
    const errors = body.data?.productCreate?.userErrors ?? [];
    if (errors.length > 0) {
      return { ok: false as const, message: errors.map((error) => error.message).join(" ") };
    }
    const externalId = body.data?.productCreate?.product?.id;
    if (!externalId) return { ok: false as const, message: "Shopify did not return a product id." };
    return { externalId, status: "DRAFT" as const };
  }
}

export class UnconfiguredStoreProvider implements StoreProvider {
  status(): ProviderStatus {
    return unconfigured("shopify", "Shopify");
  }
  async createDraft(): Promise<Unavailable> {
    return unavailable("shopify");
  }
}

export class MetaAdProvider implements AdProvider {
  id = "meta";
  constructor(
    private readonly token: string | null,
    private readonly accountId: string | null,
  ) {}
  status(): ProviderStatus {
    const configured = Boolean(this.token && this.accountId);
    return {
      id: "meta",
      name: "Meta Ads",
      configured,
      mode: configured ? "live" : "unconfigured",
      message: configured ? "Meta credentials are set. Launch stays paused." : "Provider not configured",
    };
  }
  async createPausedCampaign(input: AdDraftInput) {
    if (!this.token || !this.accountId) return unavailable("meta");
    if (!input.approvedByOperator) {
      return { ok: false as const, message: "Campaigns stay as drafts until the operator approves them." };
    }
    if (process.env.META_ALLOW_WRITE !== "true") {
      return {
        ok: false as const,
        message: "Meta writes are locked. Set META_ALLOW_WRITE only when you intend to create a paused campaign.",
      };
    }
    const response = await fetch(`https://graph.facebook.com/v21.0/act_${this.accountId}/campaigns`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: input.name,
        objective: "OUTCOME_SALES",
        status: "PAUSED",
        special_ad_categories: [],
        access_token: this.token,
      }),
    });
    if (!response.ok) throw new Error(`Meta returned ${response.status}.`);
    const body = (await response.json()) as { id?: string };
    if (!body.id) return { ok: false as const, message: "Meta did not return a campaign id." };
    return { externalId: body.id };
  }
}

export class TikTokAdProvider implements AdProvider {
  id = "tiktok";
  constructor(private readonly token: string | null) {}
  status(): ProviderStatus {
    return this.token
      ? { id: "tiktok", name: "TikTok Ads", configured: true, mode: "live", message: "Token is set. No spend calls are made." }
      : unconfigured("tiktok", "TikTok Ads");
  }
  async createPausedCampaign(): Promise<Unavailable | { ok: false; message: string }> {
    if (!this.token) return unavailable("tiktok");
    return {
      ok: false,
      message: "TikTok campaign creation is not enabled. Drafts stay inside the lab.",
    };
  }
}

export function selectAiProvider(env: LabEnv): AiProvider {
  if (env.anthropicApiKey) return new AnthropicProvider(env.anthropicApiKey, env.anthropicModel);
  if (env.openaiApiKey) return new OpenAiProvider(env.openaiApiKey, env.openaiModel);
  return new UnconfiguredAiProvider();
}

export function providerDirectory(env: LabEnv): ProviderStatus[] {
  const demo = env.dataMode === "demo";
  return [
    demo
      ? { id: "trends", name: "Trends", configured: true, mode: "demo", message: "Demo trend series. Not live search interest." }
      : env.trendsApiUrl
        ? { id: "trends", name: "Trends", configured: true, mode: "live", message: "TRENDS_API_URL is set." }
        : unconfigured("trends", "Trends"),
    demo
      ? { id: "suppliers", name: "Suppliers", configured: true, mode: "demo", message: "Demo supplier records. Not verified." }
      : env.supplierFeedUrl
        ? { id: "suppliers", name: "Suppliers", configured: true, mode: "live", message: "SUPPLIER_FEED_URL is set." }
        : unconfigured("suppliers", "Suppliers"),
    demo
      ? { id: "market", name: "Market data", configured: true, mode: "demo", message: "No live market feed. Estimates are labeled." }
      : unconfigured("market", "Market data"),
    selectAiProvider(env).status(),
    env.heygenApiKey
      ? new HeyGenVideoProvider(env.heygenApiKey, env.heygenAvatarId, env.heygenVoiceId).status()
      : unconfigured("heygen", "HeyGen"),
    env.creatifyApiKey && env.creatifyApiId
      ? { id: "creatify", name: "Creatify", configured: true, mode: "live" as const, message: "Creatify credentials are set." }
      : unconfigured("creatify", "Creatify"),
    env.shopifyStoreUrl && env.shopifyAccessToken
      ? { id: "shopify", name: "Shopify", configured: true, mode: "live" as const, message: "Shopify credentials are set. Publishing stays manual." }
      : unconfigured("shopify", "Shopify"),
    new MetaAdProvider(env.metaAccessToken, env.metaAdAccountId).status(),
    new TikTokAdProvider(env.tiktokAccessToken).status(),
    env.r2Endpoint && env.r2AccessKey && env.r2SecretKey && env.r2Bucket
      ? { id: "r2", name: "Cloudflare R2", configured: true, mode: "live" as const, message: "R2 is set for media." }
      : unconfigured("r2", "Cloudflare R2"),
    env.posthogKey
      ? { id: "posthog", name: "PostHog", configured: true, mode: "live" as const, message: "PostHog key is set." }
      : unconfigured("posthog", "PostHog"),
    env.temporalAddress
      ? { id: "temporal", name: "Temporal", configured: true, mode: "live" as const, message: "Address is set. The in-process runner is still the default." }
      : unconfigured("temporal", "Temporal"),
  ];
}

function dimensions(aspectRatio: string): [number, number] {
  switch (aspectRatio) {
    case "1:1":
      return [1080, 1080];
    case "4:5":
      return [1080, 1350];
    case "16:9":
      return [1920, 1080];
    default:
      return [1080, 1920];
  }
}

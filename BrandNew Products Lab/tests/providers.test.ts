import { describe, expect, it } from "vitest";
import { UnconfiguredTrendProvider, providerDirectory } from "@/providers";
import type { LabEnv } from "@/lib/env";

const blank: LabEnv = {
  dataMode: "live",
  databaseUrl: null,
  operatorPassword: null,
  operatorSessionSecret: null,
  openaiApiKey: null,
  anthropicApiKey: null,
  openaiModel: "gpt-4o-mini",
  anthropicModel: "claude-sonnet-4-5",
  supabaseUrl: null,
  shopifyStoreUrl: null,
  shopifyAccessToken: null,
  shopifyApiVersion: "2025-01",
  shopifyWebhookSecret: null,
  creatifyApiKey: null,
  creatifyApiId: null,
  heygenApiKey: null,
  heygenAvatarId: null,
  heygenVoiceId: null,
  metaAccessToken: null,
  metaAdAccountId: null,
  tiktokAccessToken: null,
  r2Endpoint: null,
  r2AccessKey: null,
  r2SecretKey: null,
  r2Bucket: null,
  posthogKey: null,
  temporalAddress: null,
  trendsApiUrl: null,
  supplierFeedUrl: null,
  workflowRunner: "in-process",
};

describe("providers", () => {
  it("says when a live provider is not configured", async () => {
    const provider = new UnconfiguredTrendProvider();
    expect(provider.status().message).toBe("Provider not configured");
    const result = await provider.search({ query: "dog hair" });
    expect(result).toEqual({ ok: false, message: "Provider not configured", providerId: "trends" });
  });

  it("does not describe live mode as demo data", () => {
    const directory = providerDirectory(blank);
    expect(directory.find((item) => item.id === "trends")?.mode).toBe("unconfigured");
    expect(directory.some((item) => item.mode === "demo")).toBe(false);
  });

  it("marks demo mode explicitly", () => {
    const directory = providerDirectory({ ...blank, dataMode: "demo" });
    expect(directory.find((item) => item.id === "trends")?.message).toMatch(/Demo/);
  });
});

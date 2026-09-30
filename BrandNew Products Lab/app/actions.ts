"use server";

import { randomUUID } from "node:crypto";
import { revalidatePath } from "next/cache";
import { z } from "zod";
import { getLabEnv } from "@/lib/env";
import { getRepository } from "@/lib/repository";
import { ShopifyStoreProvider } from "@/providers";
import { draftBrand } from "@/services/brand";
import { complianceBlocksProgress } from "@/services/product-tests";
import { draftCreatives } from "@/services/creatives";
import { EU_OPERATOR_DEFAULTS, ProfitInputError, assertAssumptions } from "@/services/profit";
import { transitionTest } from "@/services/product-tests";
import { draftStorePage } from "@/services/store-page";
import { withTracking } from "@/services/tracking";
import { draftVideoConcepts } from "@/services/videos";
import { analyzePerformance } from "@/services/analyst";
import { calculateProfit } from "@/services/profit";
import { describeUncertainty } from "@/services/analyst";
import type { AspectRatio, ProfitAssumptions, TestStatus, VideoConcept } from "@/types/domain";

export interface ActionState {
  error?: string;
  ok?: boolean;
}

function nowIso() {
  return new Date().toISOString();
}

function readNumber(formData: FormData, key: keyof ProfitAssumptions, current: number): number {
  const raw = formData.get(String(key));
  if (typeof raw !== "string" || raw.trim() === "") return current;
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new ProfitInputError(`${key} is not a number.`);
  }
  return value;
}

export async function saveAssumptions(_prev: ActionState | null, formData: FormData): Promise<ActionState> {
  try {
    const id = String(formData.get("opportunityId") ?? "");
    const repo = getRepository();
    let error: string | undefined;
    await repo.update((state) => {
      const opportunity = state.opportunities.find((item) => item.id === id);
      if (!opportunity) {
        error = "Opportunity not found.";
        return;
      }
      const next: ProfitAssumptions = { ...opportunity.assumptions };
      (Object.keys(next) as (keyof ProfitAssumptions)[]).forEach((key) => {
        if (key === "currency") return;
        next[key] = readNumber(formData, key, next[key]) as never;
      });
      assertAssumptions(next);
      opportunity.assumptions = next;
      opportunity.updatedAt = nowIso();
      const test = state.productTests.find((item) => item.opportunityId === id);
      if (test && test.provenance !== "live") {
        test.assumptions = next;
        test.updatedAt = opportunity.updatedAt;
      }
    });
    if (error) return { error };
    revalidatePath(`/opportunities/${id}`);
    revalidatePath("/profit");
    return { ok: true };
  } catch (caught) {
    return { error: caught instanceof Error ? caught.message : "Could not save assumptions." };
  }
}

export async function createProductTest(_prev: ActionState | null, formData: FormData): Promise<ActionState> {
  const acknowledged = formData.get("acknowledge") === "on";
  if (!acknowledged) {
    return { error: "Confirm that the economics are estimates until actual orders replace them." };
  }
  const opportunityId = String(formData.get("opportunityId") ?? "");
  const budget = Number(formData.get("budget"));
  const startsOn = String(formData.get("startsOn") ?? "");
  const endsOn = String(formData.get("endsOn") ?? "");
  if (!Number.isFinite(budget) || budget <= 0) return { error: "Set a test budget above zero." };
  if (!startsOn || !endsOn) return { error: "Set a start and end date." };

  const repo = getRepository();
  let error: string | undefined;
  let testId = "";
  await repo.update((state) => {
    const opportunity = state.opportunities.find((item) => item.id === opportunityId);
    const product = state.products.find((item) => item.id === opportunity?.productId);
    if (!opportunity || !product) {
      error = "Opportunity not found.";
      return;
    }
    const existing = state.productTests.find((item) => item.opportunityId === opportunity.id);
    if (existing) {
      error = "A test already exists for this opportunity.";
      testId = existing.id;
      return;
    }
    const block = complianceBlocksProgress(product.complianceStatus);
    const id = randomUUID();
    testId = id;
    const stamp = nowIso();
    state.productTests.unshift({
      id,
      organizationId: state.organization.id,
      productId: product.id,
      nicheId: opportunity.nicheId,
      brandId: state.brands.find((brand) => brand.opportunityId === opportunity.id)?.id ?? null,
      opportunityId: opportunity.id,
      landingPath: `/products/${product.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`,
      testBudget: budget,
      currency: "EUR",
      startsOn,
      endsOn,
      status: "RESEARCH",
      kpis: ["Net profit", "Contribution after ads", "Refund rate"],
      resultSummary: block
        ? "Opened in research. Compliance review required before this can be prepared for traffic."
        : "Opened in research. Estimates are not a result.",
      actuals: null,
      assumptions: opportunity.assumptions,
      provenance: opportunity.provenance === "demo" ? "demo" : "user",
      createdAt: stamp,
      updatedAt: stamp,
    });
  });
  if (error && !testId) return { error };
  revalidatePath("/tests");
  revalidatePath(`/opportunities/${opportunityId}`);
  revalidatePath("/");
  return error ? { error } : { ok: true };
}

export async function moveTest(_prev: ActionState | null, formData: FormData): Promise<ActionState> {
  const testId = String(formData.get("testId") ?? "");
  const next = String(formData.get("status") ?? "") as TestStatus;
  const repo = getRepository();
  let error: string | undefined;
  await repo.update((state) => {
    const test = state.productTests.find((item) => item.id === testId);
    const product = state.products.find((item) => item.id === test?.productId);
    if (!test || !product) {
      error = "Test not found.";
      return;
    }
    const moved = transitionTest(test, next, product.complianceStatus);
    if (!moved.ok) {
      error = moved.reason;
      return;
    }
    Object.assign(test, moved.test);
  });
  revalidatePath(`/tests/${testId}`);
  revalidatePath("/tests");
  return error ? { error } : { ok: true };
}

export async function generateBrandAction(formData: FormData) {
  const opportunityId = String(formData.get("opportunityId") ?? "");
  const repo = getRepository();
  await repo.update((state) => {
    const opportunity = state.opportunities.find((item) => item.id === opportunityId);
    const product = state.products.find((item) => item.id === opportunity?.productId);
    const niche = state.niches.find((item) => item.id === opportunity?.nicheId);
    if (!opportunity || !product || !niche) return;
    const existing = state.brands.find((brand) => brand.opportunityId === opportunity.id);
    const draft = draftBrand({
      id: existing?.id ?? randomUUID(),
      organizationId: state.organization.id,
      opportunity,
      product,
      niche,
      now: nowIso(),
    });
    if (existing) Object.assign(existing, draft, { id: existing.id, createdAt: existing.createdAt });
    else state.brands.unshift(draft);
  });
  revalidatePath(`/opportunities/${opportunityId}`);
  revalidatePath("/brands");
}

export async function generateCreativesAction(formData: FormData) {
  const testId = String(formData.get("testId") ?? "");
  const repo = getRepository();
  await repo.update((state) => {
    const test = state.productTests.find((item) => item.id === testId);
    const product = state.products.find((item) => item.id === test?.productId);
    const niche = state.niches.find((item) => item.id === test?.nicheId);
    if (!test || !product || !niche) return;
    const stamp = nowIso();
    const drafts = draftCreatives({
      organizationId: state.organization.id,
      product,
      niche,
      productTestId: test.id,
      now: stamp,
      provenance: test.provenance === "demo" ? "demo" : "user",
      ids: Array.from({ length: 5 }, () => randomUUID()),
    });
    state.creativeConcepts = state.creativeConcepts.filter((item) => item.productTestId !== test.id);
    state.creativeConcepts.unshift(...drafts);
  });
  revalidatePath(`/tests/${testId}`);
  revalidatePath("/creatives");
}

export async function createVideoAction(_prev: ActionState | null, formData: FormData): Promise<ActionState> {
  const productId = String(formData.get("productId") ?? "");
  const pattern = String(formData.get("pattern") ?? "problem_solution") as VideoConcept["pattern"];
  const aspectRatio = String(formData.get("aspectRatio") ?? "9:16") as AspectRatio;
  const repo = getRepository();
  const env = getLabEnv();
  let error: string | undefined;
  await repo.update((state) => {
    const product = state.products.find((item) => item.id === productId);
    const niche = state.niches.find((item) => item.id === product?.nicheId);
    if (!product || !niche) {
      error = "Product not found.";
      return;
    }
    const concept =
      draftVideoConcepts(product, niche, aspectRatio).find((item) => item.pattern === pattern) ??
      draftVideoConcepts(product, niche, aspectRatio)[0];
    const stamp = nowIso();
    const liveProvider = env.heygenApiKey ? "heygen" : env.creatifyApiKey ? "creatify" : null;
    if (env.dataMode === "live" && !liveProvider) {
      error = "Provider not configured";
      return;
    }
    state.videoJobs.unshift({
      id: randomUUID(),
      organizationId: state.organization.id,
      productId: product.id,
      provider: env.dataMode === "demo" ? "mock" : liveProvider ?? "mock",
      status: "QUEUED",
      concept,
      error: null,
      assetUrl: null,
      note:
        env.dataMode === "demo"
          ? "Queued in the demo renderer. No video file will be produced."
          : "Concept stored. The provider is not called unless VIDEO_ALLOW_SPEND=true, which this action does not set.",
      provenance: env.dataMode === "demo" ? "demo" : "user",
      createdAt: stamp,
      updatedAt: stamp,
    });
  });
  revalidatePath("/videos");
  revalidatePath(`/products/${productId}`);
  return error ? { error } : { ok: true };
}

export async function createStoreDraft(_prev: ActionState | null, formData: FormData): Promise<ActionState> {
  if (formData.get("approved") !== "on") {
    return { error: "A Shopify draft is not created until you approve it." };
  }
  const productId = String(formData.get("productId") ?? "");
  const repo = getRepository();
  const env = getLabEnv();
  const state = await repo.read();
  const product = state.products.find((item) => item.id === productId);
  if (!product) return { error: "Product not found." };
  const block = complianceBlocksProgress(product.complianceStatus);
  if (block) return { error: block };
  const brand =
    state.brands.find((item) =>
      state.opportunities.some((opp) => opp.id === item.opportunityId && opp.productId === product.id),
    ) ?? null;
  const niche = state.niches.find((item) => item.id === product.nicheId) ?? null;
  const supplierProduct = state.supplierProducts.find((item) => item.productId === product.id) ?? null;
  const supplier = supplierProduct
    ? state.suppliers.find((item) => item.id === supplierProduct.supplierId) ?? null
    : null;
  const page = draftStorePage({ product, brand, niche, supplier, supplierProduct });
  if (env.dataMode === "live" && (!env.shopifyStoreUrl || !env.shopifyAccessToken)) {
    return { error: "Provider not configured" };
  }
  let externalId: string | null = null;
  if (env.dataMode === "live" && env.shopifyStoreUrl && env.shopifyAccessToken) {
    const provider = new ShopifyStoreProvider(env.shopifyStoreUrl, env.shopifyAccessToken, env.shopifyApiVersion);
    const created = await provider.createDraft({
      title: product.name,
      descriptionHtml: page.bodyHtml,
      seoTitle: page.seoTitle,
      seoDescription: page.seoDescription,
      approvedByOperator: true,
    });
    if ("ok" in created && created.ok === false) return { error: created.message };
    if (!("externalId" in created)) return { error: "Provider not configured" };
    externalId = created.externalId;
  }
  const store = state.stores[0];
  if (!store) return { error: "No store record exists." };
  const stamp = nowIso();
  await repo.update((draft) => {
    draft.storeProducts.unshift({
      id: randomUUID(),
      storeId: store.id,
      productId: product.id,
      brandId: brand?.id ?? null,
      externalId,
      status: "pushed_draft",
      seoTitle: page.seoTitle,
      seoDescription: page.seoDescription,
      bodyHtml: page.bodyHtml,
      approvedAt: stamp,
      provenance: env.dataMode === "demo" ? "demo" : "live",
      createdAt: stamp,
      updatedAt: stamp,
    });
  });
  revalidatePath("/stores");
  return { ok: true };
}

export async function createCampaignDraft(_prev: ActionState | null, formData: FormData): Promise<ActionState> {
  const name = String(formData.get("name") ?? "").trim();
  const budget = Number(formData.get("budget"));
  const creativeId = String(formData.get("creativeId") ?? "");
  const testId = String(formData.get("testId") ?? "");
  if (!name) return { error: "Name the campaign." };
  if (!Number.isFinite(budget) || budget <= 0) return { error: "Set a budget." };
  const repo = getRepository();
  let error: string | undefined;
  await repo.update((state) => {
    const test = state.productTests.find((item) => item.id === testId);
    const creative = state.creativeConcepts.find((item) => item.id === creativeId);
    if (!test || !creative) {
      error = "Choose a test and a creative that already exist.";
      return;
    }
    const product = state.products.find((item) => item.id === test.productId);
    if (!product) return;
    const block = complianceBlocksProgress(product.complianceStatus);
    if (block) {
      error = block;
      return;
    }
    const stamp = nowIso();
    const id = randomUUID();
    const tracking = {
      campaignId: id,
      creativeId: creative.id,
      productId: product.id,
      experimentId: state.experiments.find((item) => item.productTestId === test.id)?.id ?? null,
    };
    state.campaigns.unshift({
      id,
      organizationId: state.organization.id,
      adAccountId: state.adAccounts[0]?.id ?? null,
      productId: product.id,
      productTestId: test.id,
      creativeConceptId: creative.id,
      name,
      status: "draft",
      budget,
      currency: "EUR",
      landingUrl: withTracking(test.landingPath, tracking),
      tracking,
      approvedAt: null,
      provenance: test.provenance === "demo" ? "demo" : "user",
      createdAt: stamp,
      updatedAt: stamp,
    });
    const adSetId = randomUUID();
    state.adSets.unshift({
      id: adSetId,
      campaignId: id,
      name: "Draft audience",
      audience: "Audience is a note until a live ad account is connected.",
      budget,
      status: "draft",
      createdAt: stamp,
      updatedAt: stamp,
    });
    state.ads.unshift({
      id: randomUUID(),
      adSetId,
      creativeConceptId: creative.id,
      name: creative.headline,
      status: "draft",
      tracking,
      createdAt: stamp,
      updatedAt: stamp,
    });
  });
  revalidatePath("/ads");
  return error ? { error } : { ok: true };
}

export async function approveCampaign(formData: FormData) {
  const id = String(formData.get("campaignId") ?? "");
  const repo = getRepository();
  await repo.update((state) => {
    const campaign = state.campaigns.find((item) => item.id === id);
    if (!campaign) return;
    const stamp = nowIso();
    campaign.status = "approved_paused";
    campaign.approvedAt = stamp;
    campaign.updatedAt = stamp;
    for (const adSet of state.adSets) {
      if (adSet.campaignId === campaign.id) {
        adSet.status = "approved_paused";
        adSet.updatedAt = stamp;
      }
    }
    for (const ad of state.ads) {
      const adSet = state.adSets.find((item) => item.id === ad.adSetId);
      if (adSet?.campaignId === campaign.id) {
        ad.status = "approved_paused";
        ad.updatedAt = stamp;
      }
    }
  });
  revalidatePath("/ads");
}

const importSchema = z.object({
  productName: z.string().min(2),
  nicheName: z.string().min(2),
  problem: z.string().min(2),
  audience: z.string().min(2),
  productCost: z.number().nonnegative(),
  shippingCost: z.number().nonnegative(),
  retailPriceIncVat: z.number().positive(),
  supplierCountry: z.string().min(2),
  warehouseCountry: z.string().min(2),
  shippingDays: z.number().positive(),
});

const EU = new Set([
  "NL", "BE", "DE", "FR", "AT", "IT", "ES", "PL", "SE", "DK", "FI", "IE", "PT", "LU",
  "NETHERLANDS", "BELGIUM", "GERMANY", "FRANCE", "AUSTRIA", "ITALY", "SPAIN", "POLAND",
]);

export async function importOpportunity(_prev: ActionState | null, formData: FormData): Promise<ActionState> {
  try {
    const parsed = importSchema.parse(JSON.parse(String(formData.get("payload") ?? "")));
    const repo = getRepository();
    const stamp = nowIso();
    await repo.update((state) => {
      const nicheId = randomUUID();
      const productId = randomUUID();
      const supplierId = randomUUID();
      const supplierProductId = randomUUID();
      const opportunityId = randomUUID();
      state.niches.unshift({
        id: nicheId,
        organizationId: state.organization.id,
        name: parsed.nicheName,
        description: parsed.problem,
        targetAudience: parsed.audience,
        coreProblem: parsed.problem,
        buyingMotivation: "Imported by the operator. Motivation is not inferred.",
        priceRangeMin: parsed.retailPriceIncVat * 0.8,
        priceRangeMax: parsed.retailPriceIncVat * 1.2,
        currency: "EUR",
        seasonality: "Not provided.",
        geographicRelevance: parsed.warehouseCountry,
        whyInteresting: "Imported record. No trend explanation was generated.",
        provenance: "user",
        createdAt: stamp,
        updatedAt: stamp,
      });
      state.products.unshift({
        id: productId,
        organizationId: state.organization.id,
        nicheId,
        name: parsed.productName,
        description: parsed.problem,
        complianceStatus: "insufficient_information",
        complianceNotes: "Compliance review required. An import does not include documents.",
        productDocuments: "None provided.",
        safetyInformation: "None provided.",
        provenance: "user",
        createdAt: stamp,
        updatedAt: stamp,
      });
      state.suppliers.unshift({
        id: supplierId,
        organizationId: state.organization.id,
        name: `Imported supplier (${parsed.supplierCountry})`,
        country: parsed.supplierCountry,
        warehouseCountry: parsed.warehouseCountry,
        shippingDaysMin: parsed.shippingDays,
        shippingDaysMax: parsed.shippingDays,
        returnLocation: "Not provided.",
        vatInformation: "Not provided.",
        verificationStatus: "unverified",
        productDocuments: "None provided.",
        safetyInformation: "None provided.",
        provenance: "user",
        notes: "User input. Not verified.",
        createdAt: stamp,
        updatedAt: stamp,
      });
      state.supplierProducts.unshift({
        id: supplierProductId,
        supplierId,
        productId,
        supplierSku: "",
        productCost: parsed.productCost,
        shippingCost: parsed.shippingCost,
        currency: "EUR",
        moq: null,
        euWarehouse: EU.has(parsed.warehouseCountry.trim().toUpperCase()),
        leadTimeDays: parsed.shippingDays,
        url: "",
        provenance: "user",
        createdAt: stamp,
        updatedAt: stamp,
      });
      state.opportunities.unshift({
        id: opportunityId,
        organizationId: state.organization.id,
        productId,
        nicheId,
        supplierProductId,
        stage: "opportunity_hypothesis",
        assumptions: {
          ...EU_OPERATOR_DEFAULTS,
          productCost: parsed.productCost,
          shippingCost: parsed.shippingCost,
          retailPriceIncVat: parsed.retailPriceIncVat,
        },
        actuals: null,
        inputs: {
          demandScore: 0,
          demandNote: "No demand series was imported.",
          competitionScore: 50,
          competitionNote: "No competition source was imported. This neutral score is not a finding.",
          competitionIsEstimate: true,
          supplierAvailable: true,
          euWarehouse: EU.has(parsed.warehouseCountry.trim().toUpperCase()),
          shippingDays: parsed.shippingDays,
          refundRiskScore: 50,
          refundRiskNote: "Refund risk was not provided.",
          complexityScore: 50,
          complexityNote: "Complexity was not provided.",
          creativePotentialScore: 50,
          creativePotentialNote: "Creative potential was not judged.",
          audienceClarityScore: 50,
          audienceClarityNote: parsed.audience,
          seasonalityNote: "Not provided.",
        },
        positioning: "No positioning yet. Write one from the problem you imported, not from a score.",
        positioningKind: "user_input",
        provenance: "user",
        createdAt: stamp,
        updatedAt: stamp,
      });
    });
    revalidatePath("/discover");
    revalidatePath("/opportunities");
    return { ok: true };
  } catch (caught) {
    return { error: caught instanceof Error ? caught.message : "Import failed." };
  }
}

export async function refreshAnalyst(formData: FormData) {
  const testId = String(formData.get("testId") ?? "");
  const repo = getRepository();
  await repo.update((state) => {
    const test = state.productTests.find((item) => item.id === testId);
    if (!test) return;
    const ads = state.ads.filter((ad) => {
      const adSet = state.adSets.find((item) => item.id === ad.adSetId);
      const campaign = state.campaigns.find((item) => item.id === adSet?.campaignId);
      return campaign?.productTestId === test.id;
    });
    const metrics = state.adMetrics.filter((metric) => ads.some((ad) => ad.id === metric.adId));
    const profit = calculateProfit(test.assumptions, test.actuals);
    const report = analyzePerformance({
      metrics: metrics.map((metric) => ({
        spend: metric.spend,
        impressions: metric.impressions,
        clicks: metric.clicks,
        addToCarts: metric.addToCarts,
        purchases: metric.purchases,
        revenue: metric.revenue,
      })),
      profit,
    });
    const purchases = metrics.reduce((sum, metric) => sum + metric.purchases, 0);
    const experiment = state.experiments.find((item) => item.productTestId === test.id);
    if (experiment) {
      experiment.uncertaintyNote = describeUncertainty(purchases);
      experiment.updatedAt = nowIso();
    }
    state.aiRuns.unshift({
      id: randomUUID(),
      organizationId: state.organization.id,
      purpose: "performance-reading",
      provider: "rules",
      inputSummary: `Reading for test ${test.id}.`,
      output: report,
      provenance: test.provenance === "demo" ? "demo" : "user",
      createdAt: nowIso(),
      updatedAt: nowIso(),
    });
  });
  revalidatePath("/analytics");
  revalidatePath(`/tests/${testId}`);
}

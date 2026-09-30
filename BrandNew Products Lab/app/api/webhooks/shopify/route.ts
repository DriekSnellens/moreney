import { randomUUID } from "node:crypto";
import { getRepository } from "@/lib/repository";
import { verifyShopifyHmac } from "@/lib/shopify";

export async function POST(request: Request) {
  const secret = process.env.SHOPIFY_WEBHOOK_SECRET;
  const raw = await request.text();
  const hmac = request.headers.get("x-shopify-hmac-sha256");
  if (!secret || !hmac || !verifyShopifyHmac(raw, hmac, secret)) {
    return Response.json({ error: "Webhook signature was not accepted." }, { status: 401 });
  }
  const topic = request.headers.get("x-shopify-topic") ?? "unknown";
  const stamp = new Date().toISOString();
  let recorded = false;
  await getRepository().update((state) => {
    state.workflowRuns.unshift({
      id: randomUUID(),
      organizationId: state.organization.id,
      name: `shopify:${topic}`,
      status: "succeeded",
      input: { topic },
      output: null,
      error: null,
      startedAt: stamp,
      finishedAt: stamp,
      createdAt: stamp,
      updatedAt: stamp,
    });
    if (topic !== "orders/create") return;
    const body = JSON.parse(raw) as {
      id?: number | string;
      total_price?: string;
      total_discounts?: string;
      total_tax?: string;
      landing_site?: string;
    };
    const gross = Number(body.total_price);
    if (!body.id || !Number.isFinite(gross)) return;
    let params = new URLSearchParams();
    try {
      params = new URL(body.landing_site || "https://store.local/", "https://store.local").searchParams;
    } catch {
      params = new URLSearchParams();
    }
    state.orders.unshift({
      id: randomUUID(),
      organizationId: state.organization.id,
      storeId: state.stores[0]?.id ?? null,
      externalId: String(body.id),
      currency: "EUR",
      grossTotal: gross,
      discountTotal: Number(body.total_discounts ?? 0) || 0,
      vatTotal: Number(body.total_tax ?? 0) || 0,
      status: "paid",
      orderedAt: stamp,
      campaignId: params.get("campaign_id"),
      creativeId: params.get("creative_id"),
      productId: params.get("product_id"),
      experimentId: params.get("experiment_id"),
      utm: Object.fromEntries(params.entries()),
      provenance: "live",
      createdAt: stamp,
      updatedAt: stamp,
    });
    recorded = true;
  });
  return Response.json({ received: true, topic, orderRecorded: recorded });
}

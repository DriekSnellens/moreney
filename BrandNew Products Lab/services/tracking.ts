import type { TrackingIds } from "@/types/domain";

export function buildTrackingQuery(ids: TrackingIds): string {
  const params = new URLSearchParams({
    utm_source: "paid",
    utm_medium: "cpc",
    utm_campaign: ids.campaignId,
    utm_content: ids.creativeId,
    campaign_id: ids.campaignId,
    creative_id: ids.creativeId,
    product_id: ids.productId,
  });
  if (ids.experimentId) params.set("experiment_id", ids.experimentId);
  return params.toString();
}

export function withTracking(landingPath: string, ids: TrackingIds): string {
  const joiner = landingPath.includes("?") ? "&" : "?";
  return `${landingPath}${joiner}${buildTrackingQuery(ids)}`;
}

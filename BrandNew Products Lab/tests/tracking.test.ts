import { describe, expect, it } from "vitest";
import { buildTrackingQuery, withTracking } from "@/services/tracking";

describe("tracking", () => {
  it("puts campaign, creative, product, and experiment on the landing URL", () => {
    const query = buildTrackingQuery({
      campaignId: "camp-1",
      creativeId: "creative-1",
      productId: "product-1",
      experimentId: "exp-1",
    });
    expect(query).toContain("utm_campaign=camp-1");
    expect(query).toContain("creative_id=creative-1");
    expect(query).toContain("product_id=product-1");
    expect(query).toContain("experiment_id=exp-1");
    expect(withTracking("/products/afterwalk", {
      campaignId: "camp-1",
      creativeId: "creative-1",
      productId: "product-1",
      experimentId: null,
    })).toMatch(/^\/products\/afterwalk\?/);
  });
});

import { describe, expect, it } from "vitest";
import { classifyEvidence, scoreOpportunity } from "@/services/opportunity";
import type { OpportunityInputs, ProductTest } from "@/types/domain";
import { EU_OPERATOR_DEFAULTS } from "@/services/profit";

const inputs: OpportunityInputs = {
  demandScore: 70,
  demandNote: "Manual estimate from search interest, not a sales figure.",
  competitionScore: 55,
  competitionNote: "Estimate. No live marketplace crawl is connected.",
  competitionIsEstimate: true,
  supplierAvailable: true,
  euWarehouse: true,
  shippingDays: 3,
  refundRiskScore: 40,
  refundRiskNote: "Fabric and fit products tend to come back. This one is a simple tool, so the estimate is moderate.",
  complexityScore: 20,
  complexityNote: "One SKU, no installation.",
  creativePotentialScore: 82,
  creativePotentialNote: "The before-and-after on a car seat can be shown in one shot.",
  audienceClarityScore: 88,
  audienceClarityNote: "Dog owners who drive, specifically long hair, is a person you can picture.",
  seasonalityNote: "Hair shedding rises in spring, but the car problem persists all year.",
};

describe("opportunity scoring", () => {
  it("explains every factor and keeps a trend from becoming a profitable product", () => {
    const scored = scoreOpportunity({
      inputs,
      series: [20, 22, 21, 24, 28, 31, 36],
      seriesProvenance: "estimate",
      contributionMarginRate: 0.35,
      complianceStatus: "no_flags_found",
    });
    expect(scored.factors.every((factor) => factor.rationale.length > 10)).toBe(true);
    expect(scored.score).toBeGreaterThan(0);
    expect(scored.score).toBeLessThanOrEqual(100);
    const stage = classifyEvidence({
      trendSignals: [
        {
          id: "t",
          organizationId: "o",
          nicheId: null,
          productId: "p",
          source: "demo",
          query: "dog hair car",
          series: [1, 2, 3, 4],
          seriesNote: "demo",
          observedAt: "2026-09-01",
          provenance: "demo",
          createdAt: "2026-09-01",
          updatedAt: "2026-09-01",
        },
      ],
      hasSupplierCost: true,
      hasRetailPrice: true,
      tests: [],
      actualNetProfit: null,
      actualOrders: 0,
    });
    expect(stage).toBe("opportunity_hypothesis");
  });

  it("requires actual orders before validated or profitable", () => {
    expect(
      classifyEvidence({
        trendSignals: [],
        hasSupplierCost: false,
        hasRetailPrice: false,
        tests: [],
        actualNetProfit: null,
        actualOrders: 0,
      }),
    ).toBe("trend_signal");

    const test = {
      id: "test",
      actuals: { orders: 18, grossRevenue: 500, adSpend: 240, productCostTotal: 100 },
      assumptions: EU_OPERATOR_DEFAULTS,
      status: "TESTING",
    } as ProductTest;

    expect(
      classifyEvidence({
        trendSignals: [],
        hasSupplierCost: true,
        hasRetailPrice: true,
        tests: [test],
        actualNetProfit: -40,
        actualOrders: 18,
      }),
    ).toBe("validated_product");

    expect(
      classifyEvidence({
        trendSignals: [],
        hasSupplierCost: true,
        hasRetailPrice: true,
        tests: [{ ...test, status: "PROFITABLE" }],
        actualNetProfit: 80,
        actualOrders: 40,
      }),
    ).toBe("profitable_product");
  });
});

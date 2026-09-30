import { describe, expect, it } from "vitest";
import { EU_OPERATOR_DEFAULTS } from "@/services/profit";
import { profitabilityGate, transitionTest } from "@/services/product-tests";
import type { ProductTest } from "@/types/domain";

function makeTest(overrides: Partial<ProductTest> = {}): ProductTest {
  return {
    id: "test",
    organizationId: "org",
    productId: "product",
    nicheId: "niche",
    brandId: null,
    opportunityId: "opp",
    landingPath: "/p/demo",
    testBudget: 150,
    currency: "EUR",
    startsOn: "2026-09-01",
    endsOn: "2026-09-14",
    status: "RESEARCH",
    kpis: ["Net profit"],
    resultSummary: "",
    actuals: null,
    assumptions: EU_OPERATOR_DEFAULTS,
    provenance: "demo",
    createdAt: "2026-09-01T00:00:00.000Z",
    updatedAt: "2026-09-01T00:00:00.000Z",
    ...overrides,
  };
}

describe("product test gates", () => {
  it("blocks a compliance review from becoming ready to test", () => {
    const moved = transitionTest(
      makeTest({ status: "VALIDATION" }),
      "READY_TO_TEST",
      "review_required",
    );
    expect(moved.ok).toBe(false);
    if (!moved.ok) expect(moved.reason).toMatch(/Compliance review required/);
  });

  it("refuses profitable without actual positive net profit", () => {
    const gate = profitabilityGate(EU_OPERATOR_DEFAULTS, {
      orders: 40,
      grossRevenue: 100,
      adSpend: 90,
      productCostTotal: 80,
    });
    expect(gate.ok).toBe(false);
    const moved = transitionTest(
      makeTest({
        status: "PROMISING",
        actuals: { orders: 4, grossRevenue: 100, adSpend: 10, productCostTotal: 20 },
      }),
      "PROFITABLE",
      "no_flags_found",
    );
    expect(moved.ok).toBe(false);
  });

  it("allows research to move into validation", () => {
    const moved = transitionTest(makeTest(), "VALIDATION", "review_required");
    expect(moved.ok).toBe(true);
  });
});

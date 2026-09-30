import { describe, expect, it } from "vitest";
import { buildDemoState } from "@/lib/demo-state";
import { classifyEvidence } from "@/services/opportunity";
import { calculateProfit } from "@/services/profit";
import { buildReport } from "@/services/report";

describe("demo workspace", () => {
  it("keeps the car-hair test unprofitable and labels every seed record as demo", () => {
    const state = buildDemoState(new Date("2026-09-30T12:00:00.000Z"));
    expect(state.products.every((product) => product.provenance === "demo")).toBe(true);
    expect(state.suppliers.every((supplier) => supplier.verificationStatus === "unverified")).toBe(true);
    const test = state.productTests[0];
    const profit = calculateProfit(test.assumptions, test.actuals);
    expect(profit.netProfit).toBeLessThan(0);
    expect(profit.mode).toBe("actual");
    const opportunity = state.opportunities.find((item) => item.id === test.opportunityId);
    expect(opportunity).toBeTruthy();
    const report = buildReport(state, opportunity!);
    expect(report.evidence).toBe(
      classifyEvidence({
        trendSignals: report.trends,
        hasSupplierCost: true,
        hasRetailPrice: true,
        tests: report.tests,
        actualNetProfit: profit.netProfit,
        actualOrders: test.actuals?.orders ?? 0,
      }),
    );
    expect(report.evidence).toBe("validated_product");
    expect(report.factors.every((factor) => factor.rationale.length > 0)).toBe(true);
  });
});

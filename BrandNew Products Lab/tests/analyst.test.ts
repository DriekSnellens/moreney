import { describe, expect, it } from "vitest";
import { analyzePerformance, describeUncertainty } from "@/services/analyst";
import { calculateProfit } from "@/services/profit";
import type { ProfitAssumptions } from "@/types/domain";

const assumptions: ProfitAssumptions = {
  currency: "EUR",
  units: 18,
  orders: 18,
  retailPriceIncVat: 29.95,
  discountRate: 0,
  vatRate: 0.21,
  productCost: 6.4,
  shippingCost: 3.1,
  paymentFeeRate: 0.014,
  paymentFeeFixed: 0.25,
  refundRate: 2 / 18,
  returnRate: 2 / 18,
  returnShippingCost: 4.5,
  restockRecoveryRate: 0.5,
  supplierFeePerUnit: 0,
  operationalCostPerOrder: 1.2,
  otherCosts: 0,
  adSpend: 240,
  executionBufferRate: 0.08,
};

describe("analyst", () => {
  it("says ROAS can look fine while net profit is negative, and does not claim a cause on a thin sample", () => {
    const profit = calculateProfit(assumptions, {
      grossRevenue: 539.1,
      refundedRevenue: 59.9,
      adSpend: 240,
      productCostTotal: 108.8,
      orders: 18,
    });
    const report = analyzePerformance({
      metrics: [
        {
          spend: 240,
          impressions: 20000,
          clicks: 400,
          addToCarts: 20,
          purchases: 18,
          revenue: 479.2,
        },
      ],
      profit,
    });
    expect(report.sufficientForCausality).toBe(false);
    expect(report.uncertainty.toLowerCase()).toContain("possible");
    expect(report.whatHappened.join(" ")).toMatch(/ROAS/);
    expect(report.whatHappened.join(" ")).toMatch(/net profit is negative/i);
    expect(report.possibleExplanations.join(" ")).toMatch(/Possible explanation/);
    expect(report.possibleExplanations.join(" ").toLowerCase()).not.toContain("definitely");
  });

  it("withholds a winner on tiny experiments", () => {
    expect(describeUncertainty(8)).toMatch(/Insufficient/);
    expect(describeUncertainty(40)).toMatch(/Directional/);
    expect(describeUncertainty(200)).toMatch(/not a claim of statistical significance/);
  });
});

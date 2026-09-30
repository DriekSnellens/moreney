import { describe, expect, it } from "vitest";
import { EU_OPERATOR_DEFAULTS, ProfitInputError, calculateProfit } from "@/services/profit";
import type { ProfitAssumptions } from "@/types/domain";

const sample: ProfitAssumptions = {
  currency: "EUR",
  units: 10,
  orders: 10,
  retailPriceIncVat: 12.1,
  discountRate: 0,
  vatRate: 0.21,
  productCost: 4,
  shippingCost: 1,
  paymentFeeRate: 0.1,
  paymentFeeFixed: 0.5,
  refundRate: 0.1,
  returnRate: 0.1,
  returnShippingCost: 2,
  restockRecoveryRate: 0.5,
  supplierFeePerUnit: 0.2,
  operationalCostPerOrder: 1,
  otherCosts: 3,
  adSpend: 5,
  executionBufferRate: 0.1,
};

describe("calculateProfit", () => {
  it("accounts for VAT, returns, fees, ads, and the execution buffer", () => {
    const result = calculateProfit(sample);
    expect(result.grossRevenue).toBeCloseTo(121);
    expect(result.refundedRevenue).toBeCloseTo(12.1);
    expect(result.netSales).toBeCloseTo(108.9);
    expect(result.vat).toBeCloseTo(18.9);
    expect(result.netRevenueExVat).toBeCloseTo(90);
    expect(result.cogs).toBeCloseTo(38);
    expect(result.shipping).toBeCloseTo(10);
    expect(result.returnCosts).toBeCloseTo(2);
    expect(result.paymentFees).toBeCloseTo(15.39);
    expect(result.supplierFees).toBeCloseTo(2);
    expect(result.contributionMargin).toBeCloseTo(22.61);
    expect(result.adSpend).toBeCloseTo(5);
    expect(result.operationalCosts).toBeCloseTo(10);
    expect(result.executionBuffer).toBeCloseTo(7.739);
    expect(result.netProfit).toBeCloseTo(-3.129);
    expect(result.mode).toBe("estimated");
    expect(result.grossMargin).not.toBe(result.netMargin);
  });

  it("lets actual revenue, cost, and spend replace estimates", () => {
    const result = calculateProfit(sample, {
      grossRevenue: 200,
      adSpend: 40,
      productCostTotal: 50,
      orders: 12,
    });
    expect(result.mode).toBe("actual");
    expect(result.grossRevenue).toBe(200);
    expect(result.adSpend).toBe(40);
    expect(result.cogs).toBe(50);
    const revenueLine = result.lines.find((item) => item.key === "grossRevenue");
    const shippingLine = result.lines.find((item) => item.key === "shipping");
    expect(revenueLine?.kind).toBe("data");
    expect(shippingLine?.kind).toBe("estimate");
  });

  it("refuses a partial assumption set", () => {
    expect(() => calculateProfit({ ...sample, adSpend: Number.NaN })).toThrow(ProfitInputError);
  });

  it("refuses a return rate above the refund rate", () => {
    expect(() => calculateProfit({ ...sample, returnRate: 0.4, refundRate: 0.1 })).toThrow(
      /Return rate/,
    );
  });

  it("keeps the published EU defaults explicit and finite", () => {
    const result = calculateProfit(EU_OPERATOR_DEFAULTS);
    expect(Number.isFinite(result.netProfit)).toBe(true);
    expect(result.assumptions.executionBufferRate).toBe(0.08);
  });
});

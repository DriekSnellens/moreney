import type {
  ProfitActuals,
  ProfitAssumptions,
  ProfitLine,
  ProfitResult,
  ValueKind,
} from "@/types/domain";

export class ProfitInputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProfitInputError";
  }
}

/**
 * Explicit starting point for an EU operator. The calculator never applies
 * these by itself. The UI shows them and stores whatever the operator saves.
 */
export const EU_OPERATOR_DEFAULTS: ProfitAssumptions = {
  currency: "EUR",
  units: 100,
  orders: 100,
  retailPriceIncVat: 29.95,
  discountRate: 0,
  vatRate: 0.21,
  productCost: 6.4,
  shippingCost: 3.1,
  paymentFeeRate: 0.014,
  paymentFeeFixed: 0.25,
  refundRate: 0.08,
  returnRate: 0.05,
  returnShippingCost: 4.5,
  restockRecoveryRate: 0.5,
  supplierFeePerUnit: 0,
  operationalCostPerOrder: 1.2,
  otherCosts: 0,
  adSpend: 400,
  executionBufferRate: 0.08,
};

function assertRate(name: string, value: number) {
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    throw new ProfitInputError(`${name} must be between 0 and 1.`);
  }
}

export function assertAssumptions(input: ProfitAssumptions) {
  const required: (keyof ProfitAssumptions)[] = [
    "units",
    "orders",
    "retailPriceIncVat",
    "discountRate",
    "vatRate",
    "productCost",
    "shippingCost",
    "paymentFeeRate",
    "paymentFeeFixed",
    "refundRate",
    "returnRate",
    "returnShippingCost",
    "restockRecoveryRate",
    "supplierFeePerUnit",
    "operationalCostPerOrder",
    "otherCosts",
    "adSpend",
    "executionBufferRate",
  ];
  for (const key of required) {
    if (input[key] === undefined || input[key] === null || Number.isNaN(input[key])) {
      throw new ProfitInputError(`Missing assumption: ${key}.`);
    }
  }
  if (input.currency !== "EUR") {
    throw new ProfitInputError("This build calculates in EUR.");
  }
  if (input.units < 0 || input.orders < 0) {
    throw new ProfitInputError("Units and orders cannot be negative.");
  }
  if (input.retailPriceIncVat < 0 || input.productCost < 0 || input.shippingCost < 0) {
    throw new ProfitInputError("Prices and costs cannot be negative.");
  }
  if (input.adSpend < 0 || input.otherCosts < 0 || input.returnShippingCost < 0) {
    throw new ProfitInputError("Spend and fees cannot be negative.");
  }
  assertRate("discountRate", input.discountRate);
  assertRate("vatRate", input.vatRate);
  assertRate("paymentFeeRate", input.paymentFeeRate);
  assertRate("refundRate", input.refundRate);
  assertRate("returnRate", input.returnRate);
  assertRate("restockRecoveryRate", input.restockRecoveryRate);
  assertRate("executionBufferRate", input.executionBufferRate);
  if (input.returnRate > input.refundRate + 1e-9) {
    throw new ProfitInputError(
      "Return rate cannot exceed refund rate. A physical return is treated as a refunded order.",
    );
  }
}

function line(
  key: string,
  label: string,
  amount: number,
  kind: ValueKind,
  note: string,
): ProfitLine {
  return { key, label, amount, kind, note };
}

function pick(
  actual: number | undefined,
  estimate: number,
  estimateNote: string,
  dataNote: string,
): { amount: number; kind: ValueKind; note: string } {
  if (actual !== undefined) {
    return { amount: actual, kind: "data", note: dataNote };
  }
  return { amount: estimate, kind: "estimate", note: estimateNote };
}

/**
 * VAT-inclusive EU contribution model.
 *
 * Gross revenue is what shoppers pay, after discounts, including VAT.
 * Refunded revenue leaves the business.
 * VAT is the tax portion of what remains: netSales * vatRate / (1 + vatRate).
 * Orders refunded before shipment are refundRate - returnRate.
 * Shipped units still incur outbound shipping and COGS.
 * Returned units recover COGS only at restockRecoveryRate and cost return shipping.
 * Contribution margin is after variable fulfillment, before ads and overhead.
 * Net profit then subtracts ads, operational cost, other costs, and an explicit execution buffer.
 * Actual fields replace the matching estimate. They never fill in silently.
 */
export function calculateProfit(
  assumptions: ProfitAssumptions,
  actuals?: ProfitActuals | null,
): ProfitResult {
  assertAssumptions(assumptions);
  const facts = actuals ?? {};

  const estimatedGross =
    assumptions.units * assumptions.retailPriceIncVat * (1 - assumptions.discountRate);
  const gross = pick(
    facts.grossRevenue,
    estimatedGross,
    "Units × VAT-inclusive price × (1 − discount).",
    "Actual gross revenue.",
  );
  const refunded = pick(
    facts.refundedRevenue,
    gross.amount * assumptions.refundRate,
    "Refund rate applied to gross revenue.",
    "Actual refunded revenue.",
  );
  const netSales = gross.amount - refunded.amount;
  const vat = pick(
    facts.vat,
    netSales * (assumptions.vatRate / (1 + assumptions.vatRate)),
    "VAT extracted from VAT-inclusive net sales.",
    "Actual VAT.",
  );
  const netRevenueExVat = netSales - vat.amount;

  const shippedFraction = 1 - assumptions.refundRate + assumptions.returnRate;
  const shippedUnits = assumptions.units * shippedFraction;
  const returnedUnits = assumptions.units * assumptions.returnRate;
  const estimatedCogs =
    shippedUnits * assumptions.productCost -
    returnedUnits * assumptions.productCost * assumptions.restockRecoveryRate;
  const cogs = pick(
    facts.productCostTotal,
    estimatedCogs,
    "COGS on shipped units, minus the share recovered when returns restock.",
    "Actual product cost.",
  );
  const shipping = pick(
    facts.shippingTotal,
    shippedUnits * assumptions.shippingCost,
    "Outbound shipping on shipped units.",
    "Actual shipping cost.",
  );
  const returnCosts = pick(
    facts.returnCosts,
    returnedUnits * assumptions.returnShippingCost,
    "Return shipping on physically returned units.",
    "Actual return costs.",
  );
  const estimatedPayment =
    netSales * assumptions.paymentFeeRate +
    assumptions.orders * (1 - assumptions.refundRate) * assumptions.paymentFeeFixed;
  const paymentFees = pick(
    facts.paymentFees,
    estimatedPayment,
    "Percentage on net sales plus a fixed fee on orders that were not refunded.",
    "Actual payment fees.",
  );
  const supplierFees = pick(
    facts.supplierFees,
    shippedUnits * assumptions.supplierFeePerUnit,
    "Supplier fees on shipped units.",
    "Actual supplier fees.",
  );
  const operational = pick(
    facts.operationalCosts,
    assumptions.orders * assumptions.operationalCostPerOrder,
    "Configured operational cost per order.",
    "Actual operational costs.",
  );
  const other = pick(facts.otherCosts, assumptions.otherCosts, "Other configured costs.", "Actual other costs.");
  const ads = pick(facts.adSpend, assumptions.adSpend, "Planned advertising spend.", "Actual advertising spend.");

  const variableStack =
    cogs.amount +
    shipping.amount +
    returnCosts.amount +
    paymentFees.amount +
    supplierFees.amount +
    operational.amount;
  const executionBuffer = variableStack * assumptions.executionBufferRate;
  const grossProfit = netRevenueExVat - cogs.amount;
  const contributionMargin =
    netRevenueExVat -
    cogs.amount -
    shipping.amount -
    returnCosts.amount -
    paymentFees.amount -
    supplierFees.amount;
  const contributionAfterAds = contributionMargin - ads.amount;
  const netProfit =
    contributionAfterAds - operational.amount - other.amount - executionBuffer;

  const ratio = (part: number) => (netRevenueExVat > 0 ? part / netRevenueExVat : null);

  const actualKeys: (keyof ProfitActuals)[] = [
    "grossRevenue",
    "refundedRevenue",
    "vat",
    "productCostTotal",
    "shippingTotal",
    "paymentFees",
    "returnCosts",
    "supplierFees",
    "operationalCosts",
    "otherCosts",
    "adSpend",
  ];
  const present = actualKeys.filter((key) => facts[key] !== undefined);
  const requiredForActual = ["grossRevenue", "adSpend", "productCostTotal"] as const;
  const mode: ProfitResult["mode"] =
    present.length === 0
      ? "estimated"
      : requiredForActual.every((key) => facts[key] !== undefined)
        ? "actual"
        : "blended";

  const lines: ProfitLine[] = [
    line("grossRevenue", "Gross revenue", gross.amount, gross.kind, gross.note),
    line("refundedRevenue", "Refunds", -refunded.amount, refunded.kind, refunded.note),
    line("vat", "VAT", -vat.amount, vat.kind, vat.note),
    line("netRevenueExVat", "Net revenue ex VAT", netRevenueExVat, vat.kind, "Net sales minus VAT."),
    line("cogs", "Product cost", -cogs.amount, cogs.kind, cogs.note),
    line("shipping", "Shipping", -shipping.amount, shipping.kind, shipping.note),
    line("returns", "Returns", -returnCosts.amount, returnCosts.kind, returnCosts.note),
    line("payment", "Payment fees", -paymentFees.amount, paymentFees.kind, paymentFees.note),
    line("supplier", "Supplier fees", -supplierFees.amount, supplierFees.kind, supplierFees.note),
    line(
      "contribution",
      "Contribution margin",
      contributionMargin,
      "estimate",
      "After variable fulfillment. Before ads and overhead.",
    ),
    line("ads", "Advertising", -ads.amount, ads.kind, ads.note),
    line(
      "contributionAfterAds",
      "Contribution after ads",
      contributionAfterAds,
      ads.kind,
      "This is the operating result ads have to clear. ROAS is not a substitute.",
    ),
    line("ops", "Operational costs", -operational.amount, operational.kind, operational.note),
    line("other", "Other costs", -other.amount, other.kind, other.note),
    line(
      "buffer",
      "Execution buffer",
      -executionBuffer,
      "estimate",
      "Buffer on variable and operational costs. It stays visible.",
    ),
    line("net", "Net profit", netProfit, mode === "estimated" ? "estimate" : "data", "Revenue minus every cost above."),
  ];

  return {
    currency: "EUR",
    lines,
    grossRevenue: gross.amount,
    refundedRevenue: refunded.amount,
    netSales,
    vat: vat.amount,
    netRevenueExVat,
    cogs: cogs.amount,
    shipping: shipping.amount,
    paymentFees: paymentFees.amount,
    returnCosts: returnCosts.amount,
    supplierFees: supplierFees.amount,
    grossProfit,
    grossMargin: ratio(grossProfit),
    contributionMargin,
    contributionMarginRate: ratio(contributionMargin),
    contributionAfterAds,
    operationalCosts: operational.amount,
    executionBuffer,
    adSpend: ads.amount,
    otherCosts: other.amount,
    netProfit,
    netMargin: ratio(netProfit),
    mode,
    assumptions,
  };
}

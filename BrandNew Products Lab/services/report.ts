import { classifyEvidence, evidenceLabel, scoreOpportunity } from "@/services/opportunity";
import { calculateProfit } from "@/services/profit";
import { readTrend } from "@/services/trend-math";
import type { LabState, Opportunity } from "@/types/domain";

export function buildReport(state: LabState, opportunity: Opportunity) {
  const product = state.products.find((item) => item.id === opportunity.productId) ?? null;
  const niche = state.niches.find((item) => item.id === opportunity.nicheId) ?? null;
  const supplierProduct =
    state.supplierProducts.find((item) => item.id === opportunity.supplierProductId) ?? null;
  const supplier = supplierProduct
    ? state.suppliers.find((item) => item.id === supplierProduct.supplierId) ?? null
    : null;
  const trends = state.trendSignals.filter((item) => item.productId === opportunity.productId);
  const tests = state.productTests.filter((item) => item.opportunityId === opportunity.id);
  const profit = calculateProfit(opportunity.assumptions, opportunity.actuals);
  const series = trends[0]?.series ?? [];
  const scored = scoreOpportunity({
    inputs: opportunity.inputs,
    series,
    seriesProvenance: trends[0]?.provenance === "demo" ? "estimate" : "data",
    contributionMarginRate: profit.contributionMarginRate,
    complianceStatus: product?.complianceStatus ?? "insufficient_information",
  });
  const actualOrders = tests.reduce((sum, test) => sum + (test.actuals?.orders ?? 0), 0);
  const evidence = classifyEvidence({
    trendSignals: trends,
    hasSupplierCost: Boolean(supplierProduct),
    hasRetailPrice: opportunity.assumptions.retailPriceIncVat > 0,
    tests,
    actualNetProfit: opportunity.actuals ? profit.netProfit : null,
    actualOrders,
  });
  return {
    product,
    niche,
    supplier,
    supplierProduct,
    trends,
    trend: readTrend(series),
    tests,
    test: tests[0] ?? null,
    profit,
    factors: scored.factors,
    score: scored.score,
    evidence,
    evidenceLabel: evidenceLabel(evidence),
    competition: state.competitionSignals.filter((item) => item.productId === opportunity.productId),
    market: state.marketSignals.filter((item) => item.productId === opportunity.productId),
    brand: state.brands.find((item) => item.opportunityId === opportunity.id) ?? null,
    creatives: state.creativeConcepts.filter((item) => item.productId === opportunity.productId),
    videos: state.videoJobs.filter((item) => item.productId === opportunity.productId),
  };
}

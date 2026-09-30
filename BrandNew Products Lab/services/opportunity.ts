import type {
  ComplianceStatus,
  EvidenceStage,
  FactorScore,
  OpportunityInputs,
  ProductTest,
  ProfitActuals,
  TrendSignal,
  ValueKind,
} from "@/types/domain";
import { readTrend } from "@/services/trend-math";

export const FACTOR_WEIGHTS = {
  demand: 0.12,
  trendMomentum: 0.1,
  trendAcceleration: 0.08,
  competition: 0.1,
  supplier: 0.08,
  warehouse: 0.08,
  shipping: 0.06,
  margin: 0.12,
  refundRisk: 0.06,
  complexity: 0.04,
  creative: 0.08,
  audience: 0.04,
  seasonality: 0.02,
  compliance: 0.02,
} as const;

function clamp(score: number): number {
  if (!Number.isFinite(score)) return 0;
  return Math.max(0, Math.min(100, score));
}

function factor(
  key: string,
  label: string,
  score: number,
  weight: number,
  rationale: string,
  kind: ValueKind,
): FactorScore {
  if (!rationale.trim()) {
    throw new Error(`Factor ${key} is missing an explanation.`);
  }
  return { key, label, score: clamp(score), weight, rationale, kind };
}

export function scoreOpportunity(input: {
  inputs: OpportunityInputs;
  series: number[];
  seriesProvenance: ValueKind;
  contributionMarginRate: number | null;
  complianceStatus: ComplianceStatus;
}): { factors: FactorScore[]; score: number } {
  const trend = readTrend(input.series);
  const momentumScore =
    trend.direction === "insufficient"
      ? 0
      : clamp(50 + trend.momentumPerStep * 4);
  const accelerationScore =
    trend.direction === "insufficient" ? 0 : clamp(50 + inputSeriesAccel(trend.acceleration));
  const marginScore =
    input.contributionMarginRate === null
      ? 0
      : clamp(input.contributionMarginRate * 180);
  const shippingScore =
    input.inputs.shippingDays === null
      ? 0
      : clamp(100 - Math.max(0, input.inputs.shippingDays - 2) * 12);

  const factors: FactorScore[] = [
    factor(
      "demand",
      "Demand",
      input.inputs.demandScore,
      FACTOR_WEIGHTS.demand,
      input.inputs.demandNote,
      "estimate",
    ),
    factor(
      "trendMomentum",
      "Trend momentum",
      momentumScore,
      FACTOR_WEIGHTS.trendMomentum,
      trend.note,
      input.seriesProvenance,
    ),
    factor(
      "trendAcceleration",
      "Trend acceleration",
      accelerationScore,
      FACTOR_WEIGHTS.trendAcceleration,
      trend.direction === "insufficient"
        ? "Acceleration is withheld until the series is long enough."
        : `Second-half slope minus first-half slope is ${trend.acceleration.toFixed(2)}.`,
      input.seriesProvenance,
    ),
    factor(
      "competition",
      "Competition",
      100 - input.inputs.competitionScore,
      FACTOR_WEIGHTS.competition,
      input.inputs.competitionNote,
      input.inputs.competitionIsEstimate ? "estimate" : "data",
    ),
    factor(
      "supplier",
      "Supplier availability",
      input.inputs.supplierAvailable ? 80 : 10,
      FACTOR_WEIGHTS.supplier,
      input.inputs.supplierAvailable
        ? "A supplier record exists. Verification is a separate field and is not implied by this score."
        : "No supplier record.",
      "data",
    ),
    factor(
      "warehouse",
      "EU warehouse",
      input.inputs.euWarehouse ? 90 : 25,
      FACTOR_WEIGHTS.warehouse,
      input.inputs.euWarehouse
        ? "The supplier record says the warehouse is in the EU."
        : "No EU warehouse is recorded.",
      "data",
    ),
    factor(
      "shipping",
      "Shipping speed",
      shippingScore,
      FACTOR_WEIGHTS.shipping,
      input.inputs.shippingDays === null
        ? "Shipping days are missing, so speed scores zero."
        : `${input.inputs.shippingDays} days on the supplier record. Faster than about a week scores higher.`,
      input.inputs.shippingDays === null ? "estimate" : "data",
    ),
    factor(
      "margin",
      "Contribution margin",
      marginScore,
      FACTOR_WEIGHTS.margin,
      input.contributionMarginRate === null
        ? "No contribution margin because net revenue is zero."
        : `Estimated contribution margin is ${(input.contributionMarginRate * 100).toFixed(1)}% of net revenue ex VAT, before ads.`,
      "estimate",
    ),
    factor(
      "refundRisk",
      "Refund and return risk",
      100 - input.inputs.refundRiskScore,
      FACTOR_WEIGHTS.refundRisk,
      input.inputs.refundRiskNote,
      "estimate",
    ),
    factor(
      "complexity",
      "Product complexity",
      100 - input.inputs.complexityScore,
      FACTOR_WEIGHTS.complexity,
      input.inputs.complexityNote,
      "estimate",
    ),
    factor(
      "creative",
      "Creative potential",
      input.inputs.creativePotentialScore,
      FACTOR_WEIGHTS.creative,
      input.inputs.creativePotentialNote,
      "estimate",
    ),
    factor(
      "audience",
      "Audience clarity",
      input.inputs.audienceClarityScore,
      FACTOR_WEIGHTS.audience,
      input.inputs.audienceClarityNote,
      "estimate",
    ),
    factor(
      "seasonality",
      "Seasonality",
      60,
      FACTOR_WEIGHTS.seasonality,
      input.inputs.seasonalityNote,
      "estimate",
    ),
    factor(
      "compliance",
      "Compliance headroom",
      complianceScore(input.complianceStatus),
      FACTOR_WEIGHTS.compliance,
      complianceRationale(input.complianceStatus),
      "data",
    ),
  ];

  const weight = factors.reduce((sum, item) => sum + item.weight, 0);
  const score = factors.reduce((sum, item) => sum + item.score * item.weight, 0) / weight;
  return { factors, score };
}

function inputSeriesAccel(acceleration: number): number {
  return acceleration * 25;
}

function complianceScore(status: ComplianceStatus): number {
  switch (status) {
    case "no_flags_found":
      return 70;
    case "insufficient_information":
      return 30;
    case "review_required":
      return 15;
    case "blocked":
      return 0;
  }
}

function complianceRationale(status: ComplianceStatus): string {
  switch (status) {
    case "no_flags_found":
      return "No compliance flags are stored. That is not a legal clearance.";
    case "insufficient_information":
      return "Documents are missing. The product stays in review.";
    case "review_required":
      return "The record is marked compliance review required. It cannot be launched.";
    case "blocked":
      return "The record is blocked. Do not test or launch it.";
  }
}

export function classifyEvidence(input: {
  trendSignals: TrendSignal[];
  hasSupplierCost: boolean;
  hasRetailPrice: boolean;
  tests: ProductTest[];
  actualNetProfit: number | null;
  actualOrders: number;
}): EvidenceStage {
  const profitable =
    input.actualNetProfit !== null && input.actualNetProfit > 0 && input.actualOrders >= 30;
  if (profitable && input.actualOrders >= 100 && input.tests.some((test) => test.status === "SCALING")) {
    return "scaling_candidate";
  }
  if (profitable) return "profitable_product";
  if (input.tests.some((test) => (test.actuals?.orders ?? 0) > 0)) return "validated_product";
  if (input.hasSupplierCost && input.hasRetailPrice) return "opportunity_hypothesis";
  if (input.trendSignals.length > 0) return "trend_signal";
  return "trend_signal";
}

export function evidenceLabel(stage: EvidenceStage): string {
  switch (stage) {
    case "trend_signal":
      return "Trend signal";
    case "opportunity_hypothesis":
      return "Opportunity hypothesis";
    case "validated_product":
      return "Validated product";
    case "profitable_product":
      return "Profitable product";
    case "scaling_candidate":
      return "Scaling candidate";
  }
}

export function actualOrders(actuals: ProfitActuals | null | undefined): number {
  return actuals?.orders ?? 0;
}

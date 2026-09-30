import type { ComplianceStatus, ProductTest, ProfitActuals, ProfitAssumptions, TestStatus } from "@/types/domain";
import { calculateProfit } from "@/services/profit";

const TRANSITIONS: Record<TestStatus, TestStatus[]> = {
  RESEARCH: ["VALIDATION", "KILLED", "PAUSED"],
  VALIDATION: ["READY_TO_TEST", "RESEARCH", "KILLED", "PAUSED"],
  READY_TO_TEST: ["TESTING", "KILLED", "PAUSED"],
  TESTING: ["PROMISING", "KILLED", "PAUSED"],
  PROMISING: ["PROFITABLE", "TESTING", "KILLED", "PAUSED"],
  PROFITABLE: ["SCALING", "PAUSED", "KILLED"],
  SCALING: ["PAUSED", "KILLED", "PROFITABLE"],
  KILLED: ["RESEARCH"],
  PAUSED: ["RESEARCH", "VALIDATION", "READY_TO_TEST", "TESTING", "PROMISING", "PROFITABLE", "SCALING"],
};

export function allowedTransitions(status: TestStatus): TestStatus[] {
  return TRANSITIONS[status];
}

export function complianceBlocksProgress(status: ComplianceStatus): string | null {
  if (status === "no_flags_found") return null;
  if (status === "blocked") {
    return "This product is blocked. It cannot move toward a test or a launch.";
  }
  return "Compliance review required. Keep the test in research until documents and safety information are reviewed.";
}

/**
 * Profitable is an actuals gate, not a score.
 * Thirty orders is an operator threshold. It is not a significance test.
 */
export function profitabilityGate(
  assumptions: ProfitAssumptions,
  actuals: ProfitActuals | null,
): { ok: boolean; reason: string } {
  if (!actuals) {
    return { ok: false, reason: "No actuals are stored. A product cannot be marked profitable from an estimate." };
  }
  const orders = actuals.orders ?? 0;
  if (orders < 30) {
    return {
      ok: false,
      reason: `Only ${orders} actual orders are recorded. The threshold is 30, and even that is not statistical proof.`,
    };
  }
  if (
    actuals.grossRevenue === undefined ||
    actuals.adSpend === undefined ||
    actuals.productCostTotal === undefined
  ) {
    return {
      ok: false,
      reason: "Actual revenue, ad spend, and product cost are all required before this status.",
    };
  }
  const result = calculateProfit(assumptions, actuals);
  if (result.netProfit <= 0) {
    return {
      ok: false,
      reason: `Actual net profit is ${result.netProfit.toFixed(2)} EUR. Positive gross margin or ROAS is not enough.`,
    };
  }
  return {
    ok: true,
    reason: "Actual net profit is positive and the order threshold is met. This is still not a claim of certainty.",
  };
}

export function transitionTest(
  test: ProductTest,
  next: TestStatus,
  compliance: ComplianceStatus,
): { ok: true; test: ProductTest } | { ok: false; reason: string } {
  if (!TRANSITIONS[test.status].includes(next)) {
    return { ok: false, reason: `${test.status} cannot move directly to ${next}.` };
  }
  const launchStatuses: TestStatus[] = ["READY_TO_TEST", "TESTING", "PROMISING", "PROFITABLE", "SCALING"];
  if (launchStatuses.includes(next)) {
    const block = complianceBlocksProgress(compliance);
    if (block) return { ok: false, reason: block };
  }
  if (next === "PROFITABLE" || next === "SCALING") {
    const gate = profitabilityGate(test.assumptions, test.actuals);
    if (!gate.ok) return { ok: false, reason: gate.reason };
  }
  if (next === "PROMISING") {
    const orders = test.actuals?.orders ?? 0;
    if (orders < 5) {
      return {
        ok: false,
        reason: "Promising requires a test with at least a handful of actual orders. Trend data does not qualify.",
      };
    }
  }
  return {
    ok: true,
    test: { ...test, status: next, updatedAt: new Date().toISOString() },
  };
}

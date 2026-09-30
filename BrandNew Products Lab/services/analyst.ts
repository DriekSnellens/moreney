import type { AdMetric, AnalystReport, ProfitResult } from "@/types/domain";

export interface AnalystInput {
  metrics: Pick<AdMetric, "spend" | "impressions" | "clicks" | "addToCarts" | "purchases" | "revenue">[];
  profit: ProfitResult;
  landingViews?: number;
}

function sum(metrics: AnalystInput["metrics"]) {
  return metrics.reduce(
    (acc, metric) => ({
      spend: acc.spend + metric.spend,
      impressions: acc.impressions + metric.impressions,
      clicks: acc.clicks + metric.clicks,
      addToCarts: acc.addToCarts + metric.addToCarts,
      purchases: acc.purchases + metric.purchases,
      revenue: acc.revenue + metric.revenue,
    }),
    { spend: 0, impressions: 0, clicks: 0, addToCarts: 0, purchases: 0, revenue: 0 },
  );
}

/**
 * Reads recorded numbers and refuses causal language when the sample is thin.
 * sufficientForCausality is always false: this system does not claim causes.
 */
export function analyzePerformance(input: AnalystInput): AnalystReport {
  const totals = sum(input.metrics);
  const clicks = totals.clicks;
  const ctr = totals.impressions > 0 ? totals.clicks / totals.impressions : null;
  const atcRate = clicks > 0 ? totals.addToCarts / clicks : null;
  const purchaseRate = clicks > 0 ? totals.purchases / clicks : null;
  const roas = totals.spend > 0 ? totals.revenue / totals.spend : null;
  const whatHappened: string[] = [];
  const possibleExplanations: string[] = [];
  const whatToTestNext: string[] = [];

  if (totals.impressions === 0 && totals.spend === 0) {
    return {
      whatHappened: ["No advertising metrics are recorded yet."],
      possibleExplanations: [],
      whatToTestNext: ["Run a small approved test before asking the analyst for a reading."],
      uncertainty: "There is nothing to explain.",
      roas: null,
      netProfit: input.profit.netProfit,
      sufficientForCausality: false,
    };
  }

  whatHappened.push(
    `${totals.impressions} impressions, ${totals.clicks} clicks, ${totals.addToCarts} add to carts, ${totals.purchases} purchases.`,
  );
  whatHappened.push(
    `Spend ${money(totals.spend)}. Revenue ${money(totals.revenue)}. Net profit on the profit model ${money(input.profit.netProfit)} (${input.profit.mode}).`,
  );
  if (roas !== null) {
    whatHappened.push(`ROAS is ${roas.toFixed(2)}. ROAS is not the decision.`);
  }
  if (roas !== null && roas >= 1.5 && input.profit.netProfit < 0) {
    whatHappened.push(
      "ROAS looks acceptable while net profit is negative. Fees, VAT, returns, and the execution buffer are still in the result.",
    );
  }

  const thin = totals.purchases < 5 || clicks < 50;
  if (thin) {
    return {
      whatHappened,
      possibleExplanations: [
        "The sample is too small for a cause. Any story at this size is a guess.",
      ],
      whatToTestNext: [
        "Keep one variable moving: hook, price, or landing page. Do not change all three.",
        "Stop the test if net profit stays negative after the planned budget.",
      ],
      uncertainty: "Possible explanations only. This is not a diagnosis.",
      roas,
      netProfit: input.profit.netProfit,
      sufficientForCausality: false,
    };
  }

  if (ctr !== null && ctr >= 0.015 && atcRate !== null && atcRate < 0.08) {
    whatHappened.push(
      `CTR is ${(ctr * 100).toFixed(1)}%, which is relatively strong here, while add-to-cart per click is ${(atcRate * 100).toFixed(1)}%.`,
    );
    possibleExplanations.push(
      "Possible explanation: the click promise and the landing page are describing different offers.",
    );
    possibleExplanations.push("Possible explanation: the price meets resistance once the product is specific.");
    possibleExplanations.push("Possible explanation: trust or shipping terms are unclear at the moment of adding to cart.");
    whatToTestNext.push("Test the same hook against a landing page that repeats its exact claim.");
    whatToTestNext.push("Test two prices inside an experiment. Keep the creative fixed.");
  } else if (ctr !== null && ctr < 0.008) {
    whatHappened.push(`CTR is ${(ctr * 100).toFixed(1)}%. The ad is not earning the click.`);
    possibleExplanations.push("Possible explanation: the hook is generic for this niche.");
    possibleExplanations.push("Possible explanation: the audience is broader than the problem.");
    whatToTestNext.push("Replace the hook. Keep the product and the landing page still.");
  }

  if (purchaseRate !== null && atcRate !== null && atcRate >= 0.08 && purchaseRate < 0.02) {
    possibleExplanations.push(
      "Possible explanation: people add the product and then stall at shipping, returns, or payment.",
    );
    whatToTestNext.push("Put shipping time and the return location above the fold. Only state terms you can honor.");
  }

  if (input.profit.netProfit < 0) {
    whatToTestNext.push("If the next test does not clear contribution after ads, kill the product.");
  } else {
    whatToTestNext.push("Net profit is positive on this slice. Repeat it on a second creative before calling it a pattern.");
  }

  if (possibleExplanations.length === 0) {
    possibleExplanations.push("The rates do not point at a single obvious break. Keep the next test narrow.");
  }

  return {
    whatHappened,
    possibleExplanations,
    whatToTestNext,
    uncertainty: "Possible explanations only. This is not a diagnosis.",
    roas,
    netProfit: input.profit.netProfit,
    sufficientForCausality: false,
  };
}

function money(value: number): string {
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(value);
}

export function describeUncertainty(purchases: number): string {
  if (purchases < 20) {
    return "Insufficient data. Treat any gap as a hint, not a result.";
  }
  if (purchases < 100) {
    return "Directional only. Do not call a winner.";
  }
  return "Suggestive. This is still not a claim of statistical significance.";
}

/** Shared with workers/python/trendmath.py. Keep the two implementations aligned. */

export interface TrendReading {
  momentumPerStep: number;
  acceleration: number;
  direction: "insufficient" | "falling" | "flat" | "rising";
  note: string;
}

function mean(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function linearSlope(values: number[]): number {
  const n = values.length;
  if (n < 2) return 0;
  const xMean = (n - 1) / 2;
  const yMean = mean(values);
  let numerator = 0;
  let denominator = 0;
  for (let i = 0; i < n; i += 1) {
    const dx = i - xMean;
    numerator += dx * (values[i] - yMean);
    denominator += dx * dx;
  }
  return denominator === 0 ? 0 : numerator / denominator;
}

export function readTrend(series: number[]): TrendReading {
  if (series.length < 4 || series.some((value) => !Number.isFinite(value))) {
    return {
      momentumPerStep: 0,
      acceleration: 0,
      direction: "insufficient",
      note: "Fewer than four observations. No momentum claim.",
    };
  }
  const average = mean(series);
  const slope = linearSlope(series);
  const momentumPerStep = average === 0 ? 0 : (slope / Math.abs(average)) * 100;
  const split = Math.floor(series.length / 2);
  const acceleration = linearSlope(series.slice(split)) - linearSlope(series.slice(0, split));
  const direction =
    Math.abs(momentumPerStep) < 1 ? "flat" : momentumPerStep > 0 ? "rising" : "falling";
  const accelText =
    Math.abs(acceleration) < 0.05
      ? "Acceleration is near zero."
      : acceleration > 0
        ? "The later half is steeper than the earlier half."
        : "The later half is flatter or falling versus the earlier half.";
  return {
    momentumPerStep,
    acceleration,
    direction,
    note: `Slope is ${slope.toFixed(2)} per step, ${momentumPerStep.toFixed(1)}% of the series average. ${accelText}`,
  };
}

import { describe, expect, it } from "vitest";
import { linearSlope, readTrend } from "@/services/trend-math";

describe("trend math", () => {
  it("matches the shared slope for a straight series", () => {
    expect(linearSlope([1, 2, 3])).toBeCloseTo(1);
    const reading = readTrend([1, 2, 3, 4]);
    expect(reading.direction).toBe("rising");
    expect(reading.momentumPerStep).toBeGreaterThan(0);
    expect(reading.note.length).toBeGreaterThan(10);
  });

  it("refuses a short series", () => {
    expect(readTrend([1, 2, 3]).direction).toBe("insufficient");
  });
});

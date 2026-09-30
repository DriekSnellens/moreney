"""Trend slope, momentum, and acceleration.

The TypeScript copy lives in services/trend-math.ts. Change both together.
"""

from __future__ import annotations


def linear_slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    numerator = 0.0
    denominator = 0.0
    for i, value in enumerate(values):
        dx = i - x_mean
        numerator += dx * (value - y_mean)
        denominator += dx * dx
    if denominator == 0:
        return 0.0
    return numerator / denominator


def read_trend(series: list[float]) -> dict[str, float | str]:
    if len(series) < 4 or any(value != value for value in series):  # NaN check
        return {
            "momentum_per_step": 0.0,
            "acceleration": 0.0,
            "direction": "insufficient",
        }
    average = sum(series) / len(series)
    slope = linear_slope(series)
    momentum = 0.0 if average == 0 else (slope / abs(average)) * 100
    split = len(series) // 2
    acceleration = linear_slope(series[split:]) - linear_slope(series[:split])
    if abs(momentum) < 1:
        direction = "flat"
    elif momentum > 0:
        direction = "rising"
    else:
        direction = "falling"
    return {
        "momentum_per_step": momentum,
        "acceleration": acceleration,
        "direction": direction,
    }

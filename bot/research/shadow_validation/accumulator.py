"""Bounded rolling metrics. No unbounded lists of books or waterfalls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.research.shadow_validation.economics import accounting_pass
from bot.research.shadow_validation.outcomes import (
    DATA_INVALID,
    FOLLOWER_UNAVAILABLE,
    FULL_FILL,
    HEDGE_WORSENED,
    NO_FILL,
    ObservationResult,
    PARTIAL_FILL,
)
from bot.research.shadow_validation.protocol import (
    MAX_GAP_SAMPLES,
    MIN_CALENDAR_DAYS,
    MIN_COMPLETE_WINDOWS,
    MIN_VALID_OBSERVATIONS,
    WINDOW_SECONDS_LIVE,
)


def _percentile(sorted_xs: list[float], p: float) -> float | None:
    n = len(sorted_xs)
    if n == 0:
        return None
    if n == 1:
        return sorted_xs[0]
    idx = int(round((p / 100.0) * (n - 1)))
    idx = min(n - 1, max(0, idx))
    return sorted_xs[idx]


@dataclass
class ShadowAccumulator:
    n_candidates: int = 0
    n_completed: int = 0
    n_valid: int = 0
    n_invalid: int = 0
    n_full: int = 0
    n_partial: int = 0
    n_no_fill: int = 0
    n_stale: int = 0
    n_quote_disappeared: int = 0
    n_follower_unavailable: int = 0
    n_hedge_worsened: int = 0
    n_skipped_pending_full: int = 0
    n_accounting_fail: int = 0
    n_quote_survived: int = 0
    n_follower_available: int = 0
    sum_expected_net: float = 0.0
    sum_shadow_net: float = 0.0
    sum_gap: float = 0.0
    sum_hedge_det: float = 0.0
    n_hedge_det: int = 0
    sum_adverse: float = 0.0
    n_adverse: int = 0
    run_start_ms: float | None = None
    window_net: dict[int, float] = field(default_factory=dict)
    window_count: dict[int, int] = field(default_factory=dict)
    _gaps: list[float] = field(default_factory=list)

    def observe_signal(self) -> None:
        self.n_candidates += 1

    def skip_pending_full(self) -> None:
        self.n_skipped_pending_full += 1

    def complete(self, result: ObservationResult, *, expected: Any, shadow: dict[str, float] | None = None) -> None:
        self.n_completed += 1
        if result.outcome == DATA_INVALID:
            self.n_invalid += 1
        else:
            self.n_valid += 1
        if result.outcome == FULL_FILL:
            self.n_full += 1
        elif result.outcome == PARTIAL_FILL:
            self.n_partial += 1
        elif result.outcome == NO_FILL:
            self.n_no_fill += 1
        elif result.outcome == "STALE":
            self.n_stale += 1
        elif result.outcome == "QUOTE_DISAPPEARED":
            self.n_quote_disappeared += 1
        elif result.outcome == FOLLOWER_UNAVAILABLE:
            self.n_follower_unavailable += 1
        elif result.outcome == HEDGE_WORSENED:
            self.n_hedge_worsened += 1

        if result.outcome != DATA_INVALID:
            self.sum_expected_net += result.expected_net
            self.sum_shadow_net += result.shadow_execution_net
            self.sum_gap += result.execution_gap
            if len(self._gaps) < MAX_GAP_SAMPLES:
                self._gaps.append(result.execution_gap)
            if result.quote_survival:
                self.n_quote_survived += 1
            if result.follower_availability:
                self.n_follower_available += 1
            if result.hedge_deterioration_bps is not None:
                self.sum_hedge_det += result.hedge_deterioration_bps
                self.n_hedge_det += 1
            if result.adverse_selection_bps is not None:
                self.sum_adverse += result.adverse_selection_bps
                self.n_adverse += 1
            if self.run_start_ms is not None:
                w = int(max(0.0, (result.record["signal_time_ms"] - self.run_start_ms) / 1000.0) // WINDOW_SECONDS_LIVE)
                self.window_net[w] = self.window_net.get(w, 0.0) + result.shadow_execution_net
                self.window_count[w] = self.window_count.get(w, 0) + 1

        if expected is not None:
            sh = shadow or {
                "shadow_gross": 0.0,
                "shadow_fees": 0.0,
                "shadow_slippage": 0.0,
                "shadow_adverse": 0.0,
                "shadow_latency": 0.0,
                "shadow_execution_net": result.shadow_execution_net,
            }
            # Reconstruct shadow legs from identity when not provided.
            if shadow is None:
                sh = {
                    "shadow_gross": 0.0,
                    "shadow_fees": 0.0,
                    "shadow_slippage": 0.0,
                    "shadow_adverse": 0.0,
                    "shadow_latency": 0.0,
                    "shadow_execution_net": result.shadow_execution_net,
                }
                # Identity holds for zero-fill; for fills the result already used economics.
            if result.fill_fraction == 0.0:
                if not accounting_pass(expected, sh):
                    self.n_accounting_fail += 1
            elif expected.residual() > 1e-8:
                self.n_accounting_fail += 1

    def complete_windows(self, now_ms: float) -> int:
        if self.run_start_ms is None:
            return 0
        elapsed_s = max(0.0, (now_ms - self.run_start_ms) / 1000.0)
        return int(elapsed_s // WINDOW_SECONDS_LIVE)

    def calendar_days(self, now_ms: float) -> float:
        if self.run_start_ms is None:
            return 0.0
        return max(0.0, (now_ms - self.run_start_ms) / 1000.0 / 86400.0)

    def sample_horizon_met(self, now_ms: float) -> bool:
        return (
            self.complete_windows(now_ms) >= MIN_COMPLETE_WINDOWS
            and self.calendar_days(now_ms) >= float(MIN_CALENDAR_DAYS)
        )

    def sample_volume_met(self) -> bool:
        return self.n_valid >= MIN_VALID_OBSERVATIONS

    def sample_complete(self, now_ms: float) -> bool:
        return self.sample_horizon_met(now_ms) and self.sample_volume_met()

    def gap_distribution(self) -> dict[str, float | None]:
        xs = sorted(self._gaps)
        mean = (self.sum_gap / self.n_valid) if self.n_valid else None
        median = _percentile(xs, 50) if xs else None
        return {
            "mean": mean,
            "median": median,
            "p10": _percentile(xs, 10),
            "p25": _percentile(xs, 25),
            "p50": _percentile(xs, 50),
            "p75": _percentile(xs, 75),
            "p90": _percentile(xs, 90),
            "p95": _percentile(xs, 95),
            "p99": _percentile(xs, 99),
            "n": float(len(xs)),
        }

    def rates(self) -> dict[str, float]:
        v = float(self.n_valid) if self.n_valid else 0.0
        t = float(self.n_completed) if self.n_completed else 0.0

        def _r(n: int, den: float) -> float:
            return (n / den) if den else 0.0

        return {
            "fill_rate": _r(self.n_full, v),
            "partial_fill_rate": _r(self.n_partial, v),
            "no_fill_rate": _r(self.n_no_fill, v),
            "quote_survival_rate": _r(self.n_quote_survived, v),
            "follower_availability_rate": _r(self.n_follower_available, v),
            "hedge_failure_rate": _r(self.n_follower_unavailable + self.n_hedge_worsened, v),
            "data_invalid_rate": _r(self.n_invalid, t),
            "mean_hedge_deterioration_bps": (self.sum_hedge_det / self.n_hedge_det) if self.n_hedge_det else 0.0,
            "mean_adverse_selection_bps": (self.sum_adverse / self.n_adverse) if self.n_adverse else 0.0,
        }

    def top_window_share(self) -> float:
        if not self.window_net:
            return 0.0
        total = sum(self.window_net.values())
        if total == 0.0:
            return 0.0
        top = max(abs(v) for v in self.window_net.values())
        # Share of signed net: use max window net / total if total > 0.
        if total > 0.0:
            return max(self.window_net.values()) / total
        return top / (sum(abs(v) for v in self.window_net.values()) or 1.0)

    def snapshot(self, *, now_ms: float, fingerprint: str) -> dict[str, Any]:
        rates = self.rates()
        return {
            "strategy_fingerprint": fingerprint,
            "n_candidates": self.n_candidates,
            "n_completed": self.n_completed,
            "valid_observations": self.n_valid,
            "invalid_observations": self.n_invalid,
            "FULL_FILL": self.n_full,
            "PARTIAL_FILL": self.n_partial,
            "NO_FILL": self.n_no_fill,
            "STALE": self.n_stale,
            "QUOTE_DISAPPEARED": self.n_quote_disappeared,
            "FOLLOWER_UNAVAILABLE": self.n_follower_unavailable,
            "HEDGE_WORSENED": self.n_hedge_worsened,
            "DATA_INVALID": self.n_invalid,
            "n_skipped_pending_full": self.n_skipped_pending_full,
            "RESEARCH_EXPECTED_NET": self.sum_expected_net,
            "LIVE_SHADOW_EXECUTION_NET": self.sum_shadow_net,
            "execution_gap_sum": self.sum_gap,
            "execution_gap": self.gap_distribution(),
            "rates": rates,
            "complete_windows": self.complete_windows(now_ms),
            "calendar_days": self.calendar_days(now_ms),
            "min_windows": MIN_COMPLETE_WINDOWS,
            "min_calendar_days": MIN_CALENDAR_DAYS,
            "min_valid_observations": MIN_VALID_OBSERVATIONS,
            "sample_horizon_met": self.sample_horizon_met(now_ms),
            "sample_volume_met": self.sample_volume_met(),
            "sample_complete": self.sample_complete(now_ms),
            "top_window_share": self.top_window_share(),
            "accounting_fail": self.n_accounting_fail,
            "pending_bound": MAX_GAP_SAMPLES,
            "gap_samples": len(self._gaps),
        }

    def bounded_memory(self) -> bool:
        return len(self._gaps) <= MAX_GAP_SAMPLES and len(self.window_net) < 10_000

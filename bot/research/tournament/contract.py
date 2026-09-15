"""Common strategy research contract — identical lifecycle for every family."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass
class SignalStats:
    observations: int = 0
    signals: int = 0
    conditional_forward_mean: float | None = None
    conditional_forward_median: float | None = None
    up_probability: float | None = None
    down_probability: float | None = None
    effect_size: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p10: float | None = None
    p90: float | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "signals": self.signals,
            "conditional_forward_mean": self.conditional_forward_mean,
            "conditional_forward_median": self.conditional_forward_median,
            "up_probability": self.up_probability,
            "down_probability": self.down_probability,
            "effect_size": self.effect_size,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "p10": self.p10,
            "p90": self.p90,
            "notes": list(self.notes),
        }


@dataclass
class CandidateResult:
    strategy_id: str
    verdict: str
    failed_gate: str | None
    requested_horizons: list[int]
    supported_horizons: list[int]
    horizon_reason: str | None
    frozen_params: dict[str, Any]
    dev_stats: SignalStats
    oos_stats: SignalStats | None
    oos_class: str | None  # CONSISTENT | WEAKENED | DISAPPEARED | REVERSED
    expected_gross: float | None
    expected_net: float | None
    execution_net: float | None
    waterfall: dict[str, Any]
    stability: dict[str, Any]
    tournament_score: float
    experiment_id: str | None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "verdict": self.verdict,
            "failed_gate": self.failed_gate,
            "requested_horizons": self.requested_horizons,
            "supported_horizons": self.supported_horizons,
            "horizon_reason": self.horizon_reason,
            "frozen_params": self.frozen_params,
            "dev_stats": self.dev_stats.as_dict(),
            "oos_stats": self.oos_stats.as_dict() if self.oos_stats else None,
            "oos_class": self.oos_class,
            "expected_gross": self.expected_gross,
            "expected_net": self.expected_net,
            "execution_net": self.execution_net,
            "waterfall": self.waterfall,
            "stability": self.stability,
            "tournament_score": self.tournament_score,
            "experiment_id": self.experiment_id,
            "notes": list(self.notes),
            "DEV_SIGNALS": self.dev_stats.signals,
            "OOS_SIGNALS": self.oos_stats.signals if self.oos_stats else 0,
            "EXPECTED_NET": self.expected_net,
            "EXECUTION_NET": self.execution_net,
        }


class StrategyResearchCandidate(ABC):
    """Every family implements this lifecycle. No future leakage. No private fees."""

    strategy_id: str

    @abstractmethod
    def required_horizons(self) -> Sequence[int]:
        ...

    @abstractmethod
    def required_features(self) -> Sequence[str]:
        ...

    @abstractmethod
    def run(
        self,
        *,
        index: Any,
        split: dict[str, Any],
        horizon_readiness: dict[str, str],
        dataset_meta: dict[str, Any],
    ) -> CandidateResult:
        """Full gated run. Must not read OOS during fit."""
        ...

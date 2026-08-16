"""Interpretable lead-lag signal models A–D (causal rolling only)."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Deque

from bot.opportunity.lead_lag.types import LeadLagObservation, LeadLagSignal

_ZERO = Decimal("0")
_BPS = Decimal("10000")


@dataclass
class CausalRollingState:
    """Past-only rolling stats — updated only after outcomes are known."""

    window: int = 100
    leader_returns: Deque[float] = field(default_factory=deque)
    follower_responses: Deque[float] = field(default_factory=deque)
    residuals: Deque[float] = field(default_factory=deque)
    fair_gaps: Deque[float] = field(default_factory=deque)
    n_updates: int = 0

    def observe_outcome(
        self,
        *,
        leader_return_bps: float,
        follower_response_bps: float,
        fair_gap_bps: float | None = None,
    ) -> None:
        self.leader_returns.append(leader_return_bps)
        self.follower_responses.append(follower_response_bps)
        self.residuals.append(follower_response_bps - leader_return_bps)
        if fair_gap_bps is not None:
            self.fair_gaps.append(fair_gap_bps)
        while len(self.leader_returns) > self.window:
            self.leader_returns.popleft()
            self.follower_responses.popleft()
            self.residuals.popleft()
        while len(self.fair_gaps) > self.window:
            self.fair_gaps.popleft()
        self.n_updates += 1

    def vol(self) -> float:
        xs = list(self.leader_returns)
        if len(xs) < 2:
            return 1.0
        mu = sum(xs) / len(xs)
        var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
        return max(1e-6, math.sqrt(var))

    def residual_mae(self) -> float:
        if not self.residuals:
            return 10.0
        return sum(abs(x) for x in self.residuals) / len(self.residuals)

    def mean_response_given_sign(self, leader_return_bps: float) -> float:
        if not self.leader_returns:
            return leader_return_bps
        sign = 1.0 if leader_return_bps >= 0 else -1.0
        matched = [
            f
            for l, f in zip(self.leader_returns, self.follower_responses)
            if (l >= 0) == (sign > 0)
        ]
        if not matched:
            return leader_return_bps
        return sum(matched) / len(matched)


def _dec(x: float) -> Decimal:
    return Decimal(str(round(x, 8)))


@dataclass
class ModelA:
    """Simple signed leader return as predicted follower move."""

    version: str = "A_SIGNED_LEADER_v1"
    state: CausalRollingState = field(default_factory=CausalRollingState)

    def predict(self, obs: LeadLagObservation, *, horizon_ms: int) -> LeadLagSignal:
        pred = float(obs.leader_return_bps)
        unc = max(self.state.residual_mae(), 1.0)
        n = self.state.n_updates
        strength = abs(pred) / (unc + 1e-6)
        return LeadLagSignal(
            decision_timestamp_ms=obs.timestamp_ms,
            symbol=obs.symbol,
            leader_venue=obs.leader_venue,
            follower_venue=obs.follower_venue,
            horizon_ms=horizon_ms,
            predicted_follower_move_bps=_dec(pred),
            uncertainty_bps=_dec(unc / math.sqrt(n + 1)),
            signal_strength=_dec(strength),
            model_version=self.version,
            evidence_sample_count=n,
            leader_return_bps=obs.leader_return_bps,
        )


@dataclass
class ModelB:
    """Leader return minus follower contemporaneous move (incremental)."""

    version: str = "B_INCREMENTAL_v1"
    state: CausalRollingState = field(default_factory=CausalRollingState)

    def predict(self, obs: LeadLagObservation, *, horizon_ms: int) -> LeadLagSignal:
        pred = float(obs.leader_return_bps - obs.follower_return_bps)
        unc = max(self.state.residual_mae(), 1.0)
        n = self.state.n_updates
        return LeadLagSignal(
            decision_timestamp_ms=obs.timestamp_ms,
            symbol=obs.symbol,
            leader_venue=obs.leader_venue,
            follower_venue=obs.follower_venue,
            horizon_ms=horizon_ms,
            predicted_follower_move_bps=_dec(pred),
            uncertainty_bps=_dec(unc / math.sqrt(n + 1)),
            signal_strength=_dec(abs(pred) / (unc + 1e-6)),
            model_version=self.version,
            evidence_sample_count=n,
            leader_return_bps=obs.leader_return_bps,
        )


@dataclass
class ModelC:
    """Standardized leader shock vs causal rolling volatility."""

    version: str = "C_STANDARDIZED_SHOCK_v1"
    state: CausalRollingState = field(default_factory=CausalRollingState)

    def predict(self, obs: LeadLagObservation, *, horizon_ms: int) -> LeadLagSignal:
        vol = self.state.vol()
        z = float(obs.leader_return_bps) / vol
        # Map shock to expected follower response using past same-sign mean.
        pred = self.state.mean_response_given_sign(float(obs.leader_return_bps))
        # Prefer z-scaled leader when little history.
        if self.state.n_updates < 5:
            pred = float(obs.leader_return_bps)
        unc = max(self.state.residual_mae(), vol * 0.5, 1.0)
        n = self.state.n_updates
        return LeadLagSignal(
            decision_timestamp_ms=obs.timestamp_ms,
            symbol=obs.symbol,
            leader_venue=obs.leader_venue,
            follower_venue=obs.follower_venue,
            horizon_ms=horizon_ms,
            predicted_follower_move_bps=_dec(pred),
            uncertainty_bps=_dec(unc / math.sqrt(n + 1)),
            signal_strength=_dec(abs(z)),
            model_version=self.version,
            evidence_sample_count=n,
            leader_return_bps=obs.leader_return_bps,
        )


@dataclass
class ModelD:
    """Cross-venue dislocation vs causal rolling fair gap."""

    version: str = "D_DISLOCATION_v1"
    state: CausalRollingState = field(default_factory=CausalRollingState)

    def predict(self, obs: LeadLagObservation, *, horizon_ms: int) -> LeadLagSignal:
        gap = float((obs.leader_mid - obs.follower_mid) / obs.follower_mid * _BPS) if obs.follower_mid else 0.0
        fair = (
            sum(self.state.fair_gaps) / len(self.state.fair_gaps) if self.state.fair_gaps else 0.0
        )
        pred = -(gap - fair)  # expect follower to close gap toward leader
        unc = max(self.state.residual_mae(), 1.0)
        n = self.state.n_updates
        return LeadLagSignal(
            decision_timestamp_ms=obs.timestamp_ms,
            symbol=obs.symbol,
            leader_venue=obs.leader_venue,
            follower_venue=obs.follower_venue,
            horizon_ms=horizon_ms,
            predicted_follower_move_bps=_dec(pred),
            uncertainty_bps=_dec(unc / math.sqrt(n + 1)),
            signal_strength=_dec(abs(pred) / (unc + 1e-6)),
            model_version=self.version,
            evidence_sample_count=n,
            leader_return_bps=obs.leader_return_bps,
        )


MODEL_REGISTRY = {
    "A_SIGNED_LEADER_v1": ModelA,
    "B_INCREMENTAL_v1": ModelB,
    "C_STANDARDIZED_SHOCK_v1": ModelC,
    "D_DISLOCATION_v1": ModelD,
}


def make_models() -> dict[str, ModelA | ModelB | ModelC | ModelD]:
    return {
        "A_SIGNED_LEADER_v1": ModelA(),
        "B_INCREMENTAL_v1": ModelB(),
        "C_STANDARDIZED_SHOCK_v1": ModelC(),
        "D_DISLOCATION_v1": ModelD(),
    }


def model_state_key(leader: str, follower: str, symbol: str, horizon_ms: int) -> str:
    return f"{symbol}|{leader}->{follower}|{horizon_ms}"


def fair_gap_bps(obs: LeadLagObservation) -> float:
    if obs.follower_mid <= 0:
        return 0.0
    return float((obs.leader_mid - obs.follower_mid) / obs.follower_mid * _BPS)

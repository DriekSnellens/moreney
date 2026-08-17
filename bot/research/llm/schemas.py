"""Strict schemas for autonomous LLM research output."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ALLOWED_STRATEGY_FAMILIES = (
    "lead_lag",
    "cross_venue_dislocation",
    "short_horizon_mean_reversion",
    "order_book_imbalance",
    "short_horizon_momentum",
)

ALLOWED_FEATURES = (
    "leader_return",
    "follower_forward_return",
    "route",
    "dislocation_bps",
    "spread_change",
    "deviation_from_cross_mid",
    "forward_return",
    "depth_imbalance",
    "microprice",
    "spread",
    "past_return",
    "bid_depth",
    "ask_depth",
    "book_pressure",
    "quote_staleness",
    "event_rate",
    "realized_volatility",
)

ALLOWED_HORIZONS_MS = (50, 100, 250, 500, 1000, 2000, 5000)

Confidence = Literal["low", "medium", "high"]
InfoValue = Literal["HIGH", "MEDIUM", "LOW"]


class Observation(BaseModel):
    model_config = {"extra": "forbid"}

    evidence_id: str
    observation: str
    confidence: Confidence


class AnalysisBlock(BaseModel):
    model_config = {"extra": "forbid"}

    observations: list[Observation] = Field(default_factory=list)


class HypothesisProposal(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(min_length=3, max_length=200)
    mechanism: str = Field(min_length=8, max_length=2000)
    why_now: str = Field(min_length=3, max_length=1000)
    not_equivalent_to: list[str] = Field(default_factory=list)
    difference_from_prior_failures: str = Field(default="", max_length=2000)
    strategy_family: str
    required_features: list[str] = Field(default_factory=list)
    required_horizons_ms: list[int] = Field(default_factory=list)
    signal_concept: str = Field(min_length=3, max_length=1000)
    expected_failure_modes: list[str] = Field(default_factory=list)
    economic_mechanism: str = Field(min_length=3, max_length=1000)
    execution_assumption: str = Field(default="trade_through_conservative", max_length=200)
    information_value: InfoValue = "MEDIUM"
    priority: int = Field(default=1, ge=1, le=10)
    what_we_learn_if_fails: str = Field(default="", max_length=1000)

    @field_validator("strategy_family")
    @classmethod
    def _family(cls, v: str) -> str:
        key = str(v).strip().lower()
        if key not in ALLOWED_STRATEGY_FAMILIES:
            raise ValueError(f"unknown strategy_family={v}")
        return key

    @field_validator("required_features")
    @classmethod
    def _features(cls, v: list[str]) -> list[str]:
        out = []
        for f in v:
            key = str(f).strip()
            if key not in ALLOWED_FEATURES:
                raise ValueError(f"unsupported feature={f}")
            out.append(key)
        return out

    @field_validator("required_horizons_ms")
    @classmethod
    def _horizons(cls, v: list[int]) -> list[int]:
        out = []
        for h in v:
            hi = int(h)
            if hi not in ALLOWED_HORIZONS_MS:
                raise ValueError(f"unsupported horizon_ms={h}")
            out.append(hi)
        return out


class HypothesisBatch(BaseModel):
    model_config = {"extra": "forbid"}

    analysis: AnalysisBlock = Field(default_factory=AnalysisBlock)
    hypotheses: list[HypothesisProposal] = Field(default_factory=list)


class ResultAnalysisItem(BaseModel):
    model_config = {"extra": "forbid"}

    hypothesis_id: str | None = None
    strategy_family: str | None = None
    learned: str = Field(max_length=2000)
    failure_gate: str | None = None
    shared_failure_mechanism: str | None = Field(default=None, max_length=1000)
    next_information_value: InfoValue = "MEDIUM"
    another_experiment_justified: bool = False
    notes: str = Field(default="", max_length=2000)


class ResultAnalysisBatch(BaseModel):
    model_config = {"extra": "forbid"}

    label: Literal["NON_AUTHORITATIVE_ANALYSIS"] = "NON_AUTHORITATIVE_ANALYSIS"
    items: list[ResultAnalysisItem] = Field(default_factory=list)
    shared_lessons: list[str] = Field(default_factory=list)


ForensicsExplanation = Literal[
    "RANDOM",
    "SYMBOL",
    "VENUE",
    "TIME",
    "REGIME",
    "INSUFFICIENT_EVIDENCE",
]


class ForensicsHypothesisAdvice(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(min_length=3, max_length=200)
    parent_hypothesis_id: str = Field(min_length=3, max_length=32)
    economic_mechanism: str = Field(min_length=8, max_length=2000)
    what_changed: str = Field(min_length=3, max_length=2000)
    pre_trade_features: list[str] = Field(default_factory=list)
    expected_failure_mode: str = Field(min_length=3, max_length=1000)
    what_we_learn_if_fails: str = Field(min_length=3, max_length=1000)
    information_value: InfoValue = "MEDIUM"
    strategy_family: str

    @field_validator("strategy_family")
    @classmethod
    def _fam(cls, v: str) -> str:
        key = str(v).strip().lower()
        if key not in ALLOWED_STRATEGY_FAMILIES:
            raise ValueError(f"unknown strategy_family={v}")
        return key

    @field_validator("pre_trade_features")
    @classmethod
    def _feats(cls, v: list[str]) -> list[str]:
        out = []
        for f in v:
            key = str(f).strip()
            if key not in ALLOWED_FEATURES:
                raise ValueError(f"unsupported feature={f}")
            out.append(key)
        return out


class ForensicsAdvisory(BaseModel):
    model_config = {"extra": "forbid"}

    structurally_interesting_pattern: str = Field(min_length=3, max_length=2000)
    most_likely_explanation: ForensicsExplanation
    hypotheses: list[ForensicsHypothesisAdvice] = Field(default_factory=list, max_length=2)
    notes: str = Field(default="", max_length=2000)


def hypothesis_batch_schema() -> dict[str, Any]:
    return HypothesisBatch.model_json_schema()


def result_analysis_schema() -> dict[str, Any]:
    return ResultAnalysisBatch.model_json_schema()

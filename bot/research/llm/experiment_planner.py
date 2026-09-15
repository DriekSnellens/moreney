"""Validate LLM strategy specs against registered DSL — no arbitrary code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.research.llm.budget import ExperimentBudget
from bot.research.llm.schemas import (
    ALLOWED_FEATURES,
    ALLOWED_HORIZONS_MS,
    ALLOWED_STRATEGY_FAMILIES,
    HypothesisProposal,
)


@dataclass
class ValidationResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    spec: dict[str, Any] | None = None


class StrategySpecValidator:
    """Compile/validate hypotheses into existing tournament DSL only."""

    def validate(
        self,
        proposal: HypothesisProposal,
        *,
        budget: ExperimentBudget,
        supported_horizons: set[int] | None = None,
    ) -> ValidationResult:
        reasons: list[str] = []
        if proposal.strategy_family not in ALLOWED_STRATEGY_FAMILIES:
            reasons.append("unknown_strategy_family")
        if len(proposal.required_features) > budget.max_features_per_strategy:
            reasons.append("too_many_features")
        for f in proposal.required_features:
            if f not in ALLOWED_FEATURES:
                reasons.append(f"unsupported_feature:{f}")
        for h in proposal.required_horizons_ms:
            if h not in ALLOWED_HORIZONS_MS:
                reasons.append(f"unsupported_horizon:{h}")
            if supported_horizons is not None and h not in supported_horizons:
                # still allow proposal but mark data risk — validator rejects only if ALL unsupported
                pass
        if proposal.required_horizons_ms and supported_horizons is not None:
            if not any(h in supported_horizons for h in proposal.required_horizons_ms):
                reasons.append("all_requested_horizons_unsupported")
        # Forbid economics/execution/OOS overrides via assumption text
        bad_tokens = (
            "fee_override",
            "lower fee",
            "queue fill",
            "enable execution",
            "modify oos",
            "shuffle oos",
            "import ",
            "subprocess",
            "os.system",
        )
        blob = " ".join(
            [
                proposal.mechanism,
                proposal.signal_concept,
                proposal.execution_assumption,
                proposal.economic_mechanism,
            ]
        ).lower()
        for tok in bad_tokens:
            if tok in blob:
                reasons.append(f"forbidden_token:{tok}")
        ok_budget, why = budget.can_accept_hypothesis(
            n_features=len(proposal.required_features),
            n_params=max(1, len(proposal.required_horizons_ms)),
        )
        if not ok_budget:
            reasons.append(why)

        if reasons:
            return ValidationResult(ok=False, reasons=reasons)

        spec = {
            "strategy_family": proposal.strategy_family,
            "required_features": list(proposal.required_features),
            "required_horizons_ms": list(proposal.required_horizons_ms),
            "signal_concept": proposal.signal_concept,
            "economic_mechanism": proposal.economic_mechanism,
            "execution_assumption": "trade_through_conservative",
            "cost_model": "shared_retail_taker_roundtrip",
            "oos_mode": "chronological_immutable",
            "information_value": proposal.information_value,
            "what_we_learn_if_fails": proposal.what_we_learn_if_fails,
        }
        return ValidationResult(ok=True, reasons=[], spec=spec)

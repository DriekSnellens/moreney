"""Experiment budget — prevents unlimited LLM data mining."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExperimentBudget:
    max_new_hypotheses_per_run: int = 3
    max_experiments_per_hypothesis: int = 5
    max_parameter_combinations: int = 25
    max_features_per_strategy: int = 4
    max_total_experiments_per_dataset: int = 50
    max_llm_calls_per_run: int = 2
    used_hypotheses_this_run: int = 0
    used_experiments_dataset: int = 0
    used_parameter_combinations: int = 0
    used_llm_calls: int = 0

    def remaining_hypotheses_this_run(self) -> int:
        return max(0, self.max_new_hypotheses_per_run - self.used_hypotheses_this_run)

    def remaining_dataset_experiments(self) -> int:
        return max(0, self.max_total_experiments_per_dataset - self.used_experiments_dataset)

    def remaining_llm_calls(self) -> int:
        return max(0, self.max_llm_calls_per_run - self.used_llm_calls)

    def can_accept_hypothesis(self, *, n_features: int, n_params: int) -> tuple[bool, str]:
        if self.remaining_hypotheses_this_run() <= 0:
            return False, "max_new_hypotheses_per_run"
        if self.remaining_dataset_experiments() <= 0:
            return False, "max_total_experiments_per_dataset"
        if n_features > self.max_features_per_strategy:
            return False, "max_features_per_strategy"
        if n_params > self.max_parameter_combinations:
            return False, "max_parameter_combinations"
        if self.used_parameter_combinations + n_params > self.max_parameter_combinations:
            return False, "parameter_combinations_budget"
        return True, ""

    def record_hypothesis(self, *, n_params: int = 1) -> None:
        self.used_hypotheses_this_run += 1
        self.used_experiments_dataset += 1
        self.used_parameter_combinations += max(1, n_params)

    def record_llm_call(self) -> None:
        self.used_llm_calls += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_new_hypotheses_per_run": self.max_new_hypotheses_per_run,
            "max_experiments_per_hypothesis": self.max_experiments_per_hypothesis,
            "max_parameter_combinations": self.max_parameter_combinations,
            "max_features_per_strategy": self.max_features_per_strategy,
            "max_total_experiments_per_dataset": self.max_total_experiments_per_dataset,
            "max_llm_calls_per_run": self.max_llm_calls_per_run,
            "used_hypotheses_this_run": self.used_hypotheses_this_run,
            "used_experiments_dataset": self.used_experiments_dataset,
            "used_parameter_combinations": self.used_parameter_combinations,
            "used_llm_calls": self.used_llm_calls,
            "remaining_this_run": self.remaining_hypotheses_this_run(),
            "remaining_dataset_experiments": self.remaining_dataset_experiments(),
            "remaining_llm_calls": self.remaining_llm_calls(),
        }


def budget_from_settings(settings: Any, *, used_dataset_experiments: int = 0) -> ExperimentBudget:
    return ExperimentBudget(
        max_new_hypotheses_per_run=int(
            getattr(settings, "research_max_new_hypotheses_per_run", 3)
        ),
        max_experiments_per_hypothesis=int(
            getattr(settings, "research_max_experiments_per_hypothesis", 5)
        ),
        max_parameter_combinations=int(
            getattr(settings, "research_max_parameter_combinations", 25)
        ),
        max_features_per_strategy=int(
            getattr(settings, "research_max_features_per_strategy", 4)
        ),
        max_total_experiments_per_dataset=int(
            getattr(settings, "research_max_total_experiments_per_dataset", 50)
        ),
        max_llm_calls_per_run=int(getattr(settings, "research_llm_max_calls_per_run", 2)),
        used_experiments_dataset=used_dataset_experiments,
    )

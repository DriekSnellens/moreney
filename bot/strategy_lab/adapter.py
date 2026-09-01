"""StrategyResearchAdapter protocol and base helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Sequence

from bot.strategy_lab.capital import CapitalLedger
from bot.strategy_lab.economics import CommonEconomics
from bot.strategy_lab.types import (
    CycleSnapshot,
    DecisionAction,
    StrategyDecision,
    StrategyOutcome,
)


class StrategyResearchAdapter(ABC):
    """Common research interface — all strategies see identical cycles."""

    strategy_id: str
    strategy_version: str = "v1"

    def __init__(
        self,
        *,
        economics: CommonEconomics,
        capital: CapitalLedger,
        settings: Any,
    ) -> None:
        self._economics = economics
        self._capital = capital
        self._settings = settings
        self._decisions: list[StrategyDecision] = []
        self._outcomes: list[StrategyOutcome] = []

    @abstractmethod
    def generate_decisions(self, cycle: CycleSnapshot) -> list[StrategyDecision]:
        """Causal signal → opportunity → decide. Must not look at future cycles."""

    def record_outcome(self, outcome: StrategyOutcome) -> None:
        """Attach delayed labels. Must never mutate prior decisions."""
        self._outcomes.append(outcome)

    def decisions(self) -> Sequence[StrategyDecision]:
        return list(self._decisions)

    def outcomes(self) -> Sequence[StrategyOutcome]:
        return list(self._outcomes)

    def reset(self) -> None:
        self._decisions.clear()
        self._outcomes.clear()

    def run_cycle(self, cycle: CycleSnapshot) -> list[StrategyDecision]:
        decisions = self.generate_decisions(cycle)
        self._decisions.extend(decisions)
        return decisions

    def as_meta(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "n_decisions": len(self._decisions),
            "n_outcomes": len(self._outcomes),
            "accepted": sum(
                1 for d in self._decisions if d.action == DecisionAction.ACCEPT
            ),
            "rejected": sum(
                1 for d in self._decisions if d.action == DecisionAction.REJECT
            ),
        }


def decision_key(d: StrategyDecision) -> str:
    return (
        f"{d.strategy_id}|{d.cycle_id}|{d.symbol}|{d.route}|"
        f"{d.action.value}|{d.ts_ns}"
    )


def empty_reject(
    *,
    strategy_id: str,
    strategy_version: str,
    cycle: CycleSnapshot,
    symbol: str,
    venue: str,
    route: str,
    reason: str,
) -> StrategyDecision:
    from bot.strategy_lab.types import CostBreakdown

    return StrategyDecision(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        cycle_id=cycle.cycle_id,
        ts_ns=cycle.ts_ns,
        symbol=symbol,
        venue=venue,
        route=route,
        action=DecisionAction.REJECT,
        reject_reason=reason,
        expected_edge_eur=Decimal("0"),
        costs=CostBreakdown(),
        capital_required_eur=Decimal("0"),
        estimated_capital_lock_ms=0.0,
        uncertainty=Decimal("1"),
    )

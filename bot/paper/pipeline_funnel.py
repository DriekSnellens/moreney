"""Live pipeline observability funnel. Counts only — changes no parameters.

Semantics documented:
- ENTRY DECISION: made at signal time (no 5s wait)
- OUTCOME HORIZON: 5 seconds (measurement only, handled by shadow observer)
- "candidate_created_immediately" = decision-time candidate from >=40 bps signal
- The old "valid_5s_candidates" counter was misleading: it implied a 5s entry gate.
  Renamed to "candidate_created_immediately" for semantic accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LivePipelineFunnel:
    """Accumulates stage counts across cycles. Thread-safe via GIL."""

    markets_scanned: int = 0
    okx_quote_available: int = 0
    bitvavo_quote_available: int = 0
    valid_synchronized: int = 0
    raw_dislocations: int = 0
    above_threshold: int = 0
    candidate_created_immediately: int = 0
    profitability_passed: int = 0
    profitability_rejected: int = 0
    risk_passed: int = 0
    risk_rejected: int = 0
    paper_orders: int = 0
    no_fill: int = 0
    partial_fill: int = 0
    full_fill: int = 0
    closed: int = 0
    t_plus_5_outcome_recorded: int = 0
    t_plus_5_data_invalid: int = 0

    _cycle_count: int = field(default=0, repr=False)

    def observe_market_scan(
        self,
        *,
        symbol_count: int,
        okx_symbols: int,
        bitvavo_symbols: int,
        synchronized_symbols: int,
    ) -> None:
        self.markets_scanned += symbol_count
        self.okx_quote_available += okx_symbols
        self.bitvavo_quote_available += bitvavo_symbols
        self.valid_synchronized += synchronized_symbols

    def observe_dislocation(self, *, raw: int, above_40bps: int) -> None:
        self.raw_dislocations += raw
        self.above_threshold += above_40bps

    def observe_candidates(self, n: int) -> None:
        self.candidate_created_immediately += n

    def observe_profitability_passed(self, n: int) -> None:
        self.profitability_passed += n

    def observe_profitability_rejected(self, n: int) -> None:
        self.profitability_rejected += n

    def observe_risk_passed(self, n: int) -> None:
        self.risk_passed += n

    def observe_risk_rejected(self, n: int) -> None:
        self.risk_rejected += n

    def observe_paper_orders(self, n: int) -> None:
        self.paper_orders += n

    def observe_fill(self, *, full: bool = False, partial: bool = False, no_fill: bool = False) -> None:
        if full:
            self.full_fill += 1
        elif partial:
            self.partial_fill += 1
        elif no_fill:
            self.no_fill += 1

    def observe_closed(self, n: int) -> None:
        self.closed += n

    def observe_outcome(self, *, valid: bool) -> None:
        if valid:
            self.t_plus_5_outcome_recorded += 1
        else:
            self.t_plus_5_data_invalid += 1

    def tick_cycle(self) -> None:
        self._cycle_count += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "markets_scanned": self.markets_scanned,
            "okx_quote_available": self.okx_quote_available,
            "bitvavo_quote_available": self.bitvavo_quote_available,
            "valid_synchronized": self.valid_synchronized,
            "raw_dislocations": self.raw_dislocations,
            "above_threshold_40bps": self.above_threshold,
            "candidate_created_immediately": self.candidate_created_immediately,
            "profitability_passed": self.profitability_passed,
            "profitability_rejected": self.profitability_rejected,
            "risk_passed": self.risk_passed,
            "risk_rejected": self.risk_rejected,
            "paper_orders": self.paper_orders,
            "no_fill": self.no_fill,
            "partial_fill": self.partial_fill,
            "full_fill": self.full_fill,
            "closed": self.closed,
            "t_plus_5_outcome_recorded": self.t_plus_5_outcome_recorded,
            "t_plus_5_data_invalid": self.t_plus_5_data_invalid,
            "cycles": self._cycle_count,
            "entry_semantics": "Decision is made at signal time",
            "outcome_horizon": "5 seconds",
        }

    def reset(self) -> None:
        self.markets_scanned = 0
        self.okx_quote_available = 0
        self.bitvavo_quote_available = 0
        self.valid_synchronized = 0
        self.raw_dislocations = 0
        self.above_threshold = 0
        self.candidate_created_immediately = 0
        self.profitability_passed = 0
        self.profitability_rejected = 0
        self.risk_passed = 0
        self.risk_rejected = 0
        self.paper_orders = 0
        self.no_fill = 0
        self.partial_fill = 0
        self.full_fill = 0
        self.closed = 0
        self.t_plus_5_outcome_recorded = 0
        self.t_plus_5_data_invalid = 0
        self._cycle_count = 0

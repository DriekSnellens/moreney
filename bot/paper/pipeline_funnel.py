"""Live pipeline observability funnel. Counts only — changes no parameters."""

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
    valid_5s_candidates: int = 0
    profitability_passed: int = 0
    risk_passed: int = 0
    paper_orders: int = 0
    filled: int = 0
    closed: int = 0

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
        self.valid_5s_candidates += n

    def observe_profitability_passed(self, n: int) -> None:
        self.profitability_passed += n

    def observe_risk_passed(self, n: int) -> None:
        self.risk_passed += n

    def observe_paper_orders(self, n: int) -> None:
        self.paper_orders += n

    def observe_filled(self, n: int) -> None:
        self.filled += n

    def observe_closed(self, n: int) -> None:
        self.closed += n

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
            "valid_5s_candidates": self.valid_5s_candidates,
            "profitability_passed": self.profitability_passed,
            "risk_passed": self.risk_passed,
            "paper_orders": self.paper_orders,
            "filled": self.filled,
            "closed": self.closed,
            "cycles": self._cycle_count,
        }

    def reset(self) -> None:
        self.markets_scanned = 0
        self.okx_quote_available = 0
        self.bitvavo_quote_available = 0
        self.valid_synchronized = 0
        self.raw_dislocations = 0
        self.above_threshold = 0
        self.valid_5s_candidates = 0
        self.profitability_passed = 0
        self.risk_passed = 0
        self.paper_orders = 0
        self.filled = 0
        self.closed = 0
        self._cycle_count = 0

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
class CrossVenueFunnel:
    """OKX ↔ Bitvavo cross-venue funnel (session cumulative)."""

    pairs_evaluated: int = 0
    edges_found: int = 0
    opportunities_emitted: int = 0
    profitability_passed: int = 0
    profitability_rejected: int = 0
    risk_passed: int = 0
    risk_rejected: int = 0
    live_orders: int = 0
    live_fills: int = 0
    reject_counts: dict[str, int] = field(default_factory=dict)

    def observe_scan_delta(
        self,
        *,
        pairs_evaluated: int = 0,
        edges_found: int = 0,
        opportunities_emitted: int = 0,
        reject_counts: dict[str, int] | None = None,
    ) -> None:
        self.pairs_evaluated += max(0, pairs_evaluated)
        self.edges_found += max(0, edges_found)
        self.opportunities_emitted += max(0, opportunities_emitted)
        if reject_counts:
            for code, count in reject_counts.items():
                self.reject_counts[code] = self.reject_counts.get(code, 0) + int(count)

    def observe_profitability_passed(self, n: int = 1) -> None:
        self.profitability_passed += max(0, n)

    def observe_profitability_rejected(self, n: int = 1) -> None:
        self.profitability_rejected += max(0, n)

    def observe_risk_passed(self, n: int = 1) -> None:
        self.risk_passed += max(0, n)

    def observe_risk_rejected(self, n: int = 1) -> None:
        self.risk_rejected += max(0, n)

    def observe_live_orders(self, n: int = 1) -> None:
        self.live_orders += max(0, n)

    def observe_live_fills(self, n: int = 1) -> None:
        self.live_fills += max(0, n)

    def snapshot(self) -> dict[str, Any]:
        top_rejects = sorted(
            self.reject_counts.items(), key=lambda item: item[1], reverse=True
        )[:8]
        return {
            "venues": "okx,bitvavo",
            "pairs_evaluated": self.pairs_evaluated,
            "edges_found": self.edges_found,
            "opportunities_emitted": self.opportunities_emitted,
            "profitability_passed": self.profitability_passed,
            "profitability_rejected": self.profitability_rejected,
            "risk_passed": self.risk_passed,
            "risk_rejected": self.risk_rejected,
            "live_orders": self.live_orders,
            "live_fills": self.live_fills,
            "reject_counts": dict(sorted(self.reject_counts.items())),
            "top_rejection_reasons": [
                {"reason": reason, "count": count} for reason, count in top_rejects
            ],
        }

    def reset(self) -> None:
        self.pairs_evaluated = 0
        self.edges_found = 0
        self.opportunities_emitted = 0
        self.profitability_passed = 0
        self.profitability_rejected = 0
        self.risk_passed = 0
        self.risk_rejected = 0
        self.live_orders = 0
        self.live_fills = 0
        self.reject_counts.clear()


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

    cross_venue: CrossVenueFunnel = field(default_factory=CrossVenueFunnel)
    _cycle_count: int = field(default=0, repr=False)
    _cv_scan_cursor: dict[str, int] = field(default_factory=dict, repr=False)

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

    def observe_cross_venue_scan(self, stats: dict[str, Any]) -> None:
        """Ingest cumulative maker cross-venue scan stats as per-cycle deltas."""
        cv = stats.get("cross_venue") if isinstance(stats, dict) else None
        if not isinstance(cv, dict):
            return
        cursor = self._cv_scan_cursor
        pairs = int(cv.get("pairs_evaluated") or 0)
        edges = int(cv.get("edges_found") or 0)
        emitted = int(cv.get("opportunities_emitted") or 0)
        reject_raw = cv.get("reject_counts") or {}
        reject_delta: dict[str, int] = {}
        if isinstance(reject_raw, dict):
            for code, count in reject_raw.items():
                cur = int(count or 0)
                prev = int(cursor.get(f"rej:{code}") or 0)
                delta = max(0, cur - prev)
                if delta:
                    reject_delta[str(code)] = delta
                cursor[f"rej:{code}"] = cur
        self.cross_venue.observe_scan_delta(
            pairs_evaluated=max(0, pairs - int(cursor.get("pairs") or 0)),
            edges_found=max(0, edges - int(cursor.get("edges") or 0)),
            opportunities_emitted=max(0, emitted - int(cursor.get("emitted") or 0)),
            reject_counts=reject_delta,
        )
        cursor["pairs"] = pairs
        cursor["edges"] = edges
        cursor["emitted"] = emitted

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
            "cross_venue": self.cross_venue.snapshot(),
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
        self._cv_scan_cursor.clear()
        self.cross_venue.reset()

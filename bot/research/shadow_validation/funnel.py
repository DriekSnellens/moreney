"""Live execution funnel. Counts + percentages with explicit denominators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _rate(n: int, den: int) -> dict[str, Any]:
    d = int(den)
    return {
        "count": int(n),
        "denominator": d,
        "rate": (float(n) / float(d)) if d else 0.0,
    }


@dataclass
class ExecutionFunnel:
    signals: int = 0
    data_invalid: int = 0
    stale: int = 0
    leader_unavailable: int = 0
    follower_unavailable: int = 0
    quote_disappeared: int = 0
    no_fill: int = 0
    partial_fill: int = 0
    full_fill: int = 0
    hedge_success: int = 0
    hedge_worsened: int = 0
    hedge_failed: int = 0
    valid_market_data: int = 0
    both_venues_available: int = 0
    leader_quote_valid: int = 0
    follower_quote_valid: int = 0
    estimated_fill_attempts: int = 0
    hedge_attempts: int = 0
    market_outcome_5s: int = 0

    def observe_signal(self) -> None:
        self.signals += 1

    def observe_outcome(
        self,
        *,
        outcome: str,
        has_5s_markout: bool,
    ) -> None:
        if outcome == "DATA_INVALID":
            self.data_invalid += 1
            return
        self.valid_market_data += 1
        if outcome == "STALE":
            self.stale += 1
            return
        if outcome == "QUOTE_DISAPPEARED":
            self.quote_disappeared += 1
            self.leader_unavailable += 1
            return
        self.leader_quote_valid += 1
        if outcome == "FOLLOWER_UNAVAILABLE":
            self.follower_unavailable += 1
            self.hedge_failed += 1
            return
        self.both_venues_available += 1
        self.follower_quote_valid += 1
        self.estimated_fill_attempts += 1
        if outcome == "NO_FILL":
            self.no_fill += 1
        elif outcome == "PARTIAL_FILL":
            self.partial_fill += 1
            self.hedge_attempts += 1
            self.hedge_success += 1
        elif outcome == "FULL_FILL":
            self.full_fill += 1
            self.hedge_attempts += 1
            self.hedge_success += 1
        elif outcome == "HEDGE_WORSENED":
            self.hedge_attempts += 1
            self.hedge_worsened += 1
            if has_5s_markout:
                pass
        if has_5s_markout:
            self.market_outcome_5s += 1

    def snapshot(self) -> dict[str, Any]:
        sig = self.signals
        completed = (
            self.data_invalid
            + self.stale
            + self.quote_disappeared
            + self.follower_unavailable
            + self.no_fill
            + self.partial_fill
            + self.full_fill
            + self.hedge_worsened
        )
        valid = self.valid_market_data
        fill_den = self.estimated_fill_attempts
        hedge_den = self.hedge_attempts
        return {
            "signals": _rate(self.signals, sig),
            "data_invalid": _rate(self.data_invalid, completed or sig),
            "stale": _rate(self.stale, valid or sig),
            "leader_unavailable": _rate(self.leader_unavailable, valid or sig),
            "follower_unavailable": _rate(self.follower_unavailable, self.leader_quote_valid or valid or sig),
            "quote_disappeared": _rate(self.quote_disappeared, valid or sig),
            "no_fill": _rate(self.no_fill, fill_den or valid or sig),
            "partial_fill": _rate(self.partial_fill, fill_den or valid or sig),
            "full_fill": _rate(self.full_fill, fill_den or valid or sig),
            "hedge_success": _rate(self.hedge_success, hedge_den or fill_den or sig),
            "hedge_worsened": _rate(self.hedge_worsened, hedge_den or fill_den or sig),
            "hedge_failed": _rate(self.hedge_failed, hedge_den or self.leader_quote_valid or sig),
            "stages": {
                "SIGNAL": _rate(self.signals, sig),
                "VALID_MARKET_DATA": _rate(self.valid_market_data, sig),
                "BOTH_VENUES_AVAILABLE": _rate(self.both_venues_available, sig),
                "LEADER_QUOTE_STILL_VALID": _rate(self.leader_quote_valid, sig),
                "FOLLOWER_QUOTE_STILL_VALID": _rate(self.follower_quote_valid, sig),
                "ESTIMATED_FILL": _rate(self.estimated_fill_attempts, sig),
                "HEDGE_ATTEMPT": _rate(self.hedge_attempts, sig),
                "MARKET_OUTCOME_5S": _rate(self.market_outcome_5s, sig),
            },
            "note": "Rates expose count/denominator. A SIGNAL is not a fill.",
        }

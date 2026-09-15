"""Missed-opportunity log: first limiting gate + counterfactual NET.

Counterfactual markout uses only market data *after* the decision, and is
never fed back into the live ranking path (reporting only).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from bot.core.enums import OpportunityDecisionAction
from bot.opportunity.models import OpportunityDecision, ScoredOpportunity

_ZERO = Decimal("0")
_BPS = Decimal("10000")
_HORIZONS_MS = (1000, 5000, 30000, 60000)


@dataclass
class MissedRecord:
    opportunity_id: UUID
    timestamp_ms: float
    symbol: str
    strategy: str
    buy_exchange: str
    sell_exchange: str
    side: str
    raw_ev: Decimal
    calibrated_ev: Decimal
    expected_net_eur: Decimal
    required_capital: Decimal
    venue: str
    rejection_reason: str
    first_limiting_gate: str
    all_gates: list[str]
    entry_price: Decimal
    theoretical_net: Decimal
    simulated_fill: bool = False
    simulated_markout_bps: Decimal | None = None
    simulated_realized_net: Decimal | None = None
    classification: str = "pending"  # justified | missed_profit | phantom
    captured: dict[int, Decimal] = field(default_factory=dict)


class MissedOpportunityTracker:
    """Ring buffer of rejected opportunities with post-hoc markout."""

    def __init__(self, *, max_entries: int = 2000) -> None:
        self._records: deque[MissedRecord] = deque(maxlen=max_entries)
        self._pending: list[MissedRecord] = []
        self._gate_counts: dict[str, int] = {}
        self._gate_theoretical: dict[str, Decimal] = {}
        self._gate_simulated: dict[str, Decimal] = {}

    def record_reject(
        self,
        scored: ScoredOpportunity,
        decision: OpportunityDecision,
        *,
        first_gate: str,
        all_gates: list[str],
    ) -> None:
        if decision.action != OpportunityDecisionAction.REJECT:
            return
        meta = scored.opportunity.metadata or {}
        buy = str(meta.get("buy_exchange") or "")
        sell = str(meta.get("sell_exchange") or "")
        net = getattr(scored, "expected_net_eur", None)
        if net is None:
            net = scored.profitability.net_profit_usd
        rec = MissedRecord(
            opportunity_id=scored.opportunity_id,
            timestamp_ms=time.time() * 1000.0,
            symbol=scored.opportunity.symbol,
            strategy=scored.opportunity.strategy_name,
            buy_exchange=buy,
            sell_exchange=sell,
            side=scored.opportunity.side.value,
            raw_ev=scored.expected_value,
            calibrated_ev=getattr(scored, "calibrated_expected_value", scored.expected_value),
            expected_net_eur=Decimal(str(net)),
            required_capital=scored.capital_required,
            venue=buy or "multi",
            rejection_reason=decision.reason,
            first_limiting_gate=first_gate,
            all_gates=list(all_gates),
            entry_price=scored.opportunity.entry_price,
            theoretical_net=scored.profitability.net_profit_usd,
        )
        self._records.append(rec)
        self._pending.append(rec)
        self._gate_counts[first_gate] = self._gate_counts.get(first_gate, 0) + 1
        self._gate_theoretical[first_gate] = (
            self._gate_theoretical.get(first_gate, _ZERO) + rec.theoretical_net
        )

    def update_mids(self, mids: dict[str, Decimal]) -> None:
        """Advance counterfactual markout. Reporting only — not a live signal."""
        now = time.time() * 1000.0
        still: list[MissedRecord] = []
        for rec in self._pending:
            mid = mids.get(rec.symbol.upper())
            if mid is None or mid <= 0 or rec.entry_price <= 0:
                still.append(rec)
                continue
            age = now - rec.timestamp_ms
            for horizon in _HORIZONS_MS:
                if horizon in rec.captured or age < horizon:
                    continue
                if rec.side.lower() in {"buy", "long"}:
                    adverse = (mid - rec.entry_price) / rec.entry_price * _BPS
                else:
                    adverse = (rec.entry_price - mid) / rec.entry_price * _BPS
                rec.captured[horizon] = adverse
            if len(rec.captured) >= len(_HORIZONS_MS):
                primary = rec.captured.get(5000, _ZERO)
                rec.simulated_fill = True
                rec.simulated_markout_bps = primary
                adverse_eur = rec.required_capital * primary / _BPS
                rec.simulated_realized_net = rec.theoretical_net - adverse_eur
                rec.classification = _classify(rec)
                self._gate_simulated[rec.first_limiting_gate] = (
                    self._gate_simulated.get(rec.first_limiting_gate, _ZERO)
                    + (rec.simulated_realized_net or _ZERO)
                )
            else:
                still.append(rec)
        self._pending = still

    def gate_table(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for gate, count in sorted(self._gate_counts.items(), key=lambda kv: -kv[1]):
            theoretical = self._gate_theoretical.get(gate, _ZERO)
            simulated = self._gate_simulated.get(gate, _ZERO)
            # Good rejection ≈ theoretical positive but simulated realized <= 0,
            # approximated here as max(0, theoretical - max(simulated, 0)).
            missed = max(_ZERO, simulated)
            saved = max(_ZERO, theoretical - missed)
            if simulated < 0 <= theoretical:
                saved = abs(simulated)
                missed = _ZERO
            recommendation = _recommend(gate, count, missed, saved)
            rows.append(
                {
                    "gate": gate,
                    "rejections": count,
                    "estimated_good_rejections_eur": str(saved),
                    "estimated_missed_profit_eur": str(missed),
                    "theoretical_net_sum": str(theoretical),
                    "simulated_net_sum": str(simulated),
                    "recommendation": recommendation,
                }
            )
        return rows

    def why_not_trade(self) -> dict[str, Any]:
        return {
            "top_rejection_reasons": [
                {"reason": k, "count": v}
                for k, v in sorted(self._gate_counts.items(), key=lambda kv: -kv[1])
            ],
            "gate_table": self.gate_table(),
            "recent": [self._public(r) for r in list(self._records)[-25:]],
        }

    def snapshot(self) -> dict[str, Any]:
        classified = {"justified": 0, "missed_profit": 0, "phantom": 0, "pending": 0}
        for rec in self._records:
            classified[rec.classification] = classified.get(rec.classification, 0) + 1
        return {
            "records": len(self._records),
            "pending_counterfactual": len(self._pending),
            "classified": classified,
            "why_not_trade": self.why_not_trade(),
        }

    def export_state(self) -> dict[str, Any]:
        return {
            "gate_counts": dict(self._gate_counts),
            "gate_theoretical": {k: str(v) for k, v in self._gate_theoretical.items()},
            "gate_simulated": {k: str(v) for k, v in self._gate_simulated.items()},
        }

    def import_state(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        raw_counts = data.get("gate_counts") or {}
        if isinstance(raw_counts, dict):
            self._gate_counts = {str(k): int(v) for k, v in raw_counts.items()}
        for store_name, target in (
            ("gate_theoretical", self._gate_theoretical),
            ("gate_simulated", self._gate_simulated),
        ):
            raw = data.get(store_name) or {}
            if isinstance(raw, dict):
                target.clear()
                for k, v in raw.items():
                    target[str(k)] = Decimal(str(v))

    @staticmethod
    def _public(rec: MissedRecord) -> dict[str, Any]:
        return {
            "opportunity_id": str(rec.opportunity_id),
            "symbol": rec.symbol,
            "strategy": rec.strategy,
            "venue": rec.venue,
            "raw_ev": str(rec.raw_ev),
            "calibrated_ev": str(rec.calibrated_ev),
            "expected_net_eur": str(rec.expected_net_eur),
            "required_capital": str(rec.required_capital),
            "rejection_reason": rec.rejection_reason,
            "first_limiting_gate": rec.first_limiting_gate,
            "all_gates": rec.all_gates,
            "theoretical_net": str(rec.theoretical_net),
            "simulated_realized_net": (
                str(rec.simulated_realized_net) if rec.simulated_realized_net is not None else None
            ),
            "classification": rec.classification,
        }


def _classify(rec: MissedRecord) -> str:
    sim = rec.simulated_realized_net
    if sim is None:
        return "pending"
    if rec.theoretical_net <= 0 or sim <= 0:
        if sim <= 0:
            return "justified"
        return "phantom"
    if rec.calibrated_ev > 0 and sim > 0:
        return "missed_profit"
    if sim > 0 and rec.theoretical_net > 0:
        return "phantom" if rec.calibrated_ev <= 0 else "missed_profit"
    return "justified"


def _recommend(gate: str, count: int, missed: Decimal, saved: Decimal) -> str:
    if count <= 0:
        return "keep"
    if missed > saved * Decimal("2") and missed > Decimal("1"):
        return "review — possible missed profit"
    if saved > missed * Decimal("2"):
        return "keep — filter is earning its keep"
    return "measure more samples"

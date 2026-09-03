"""AlphaI decision attribution — measure blocked buys, size boosts, exit urgency.

Counterfactual / estimated only. auto_apply = false.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

_ZERO = Decimal("0")


@dataclass
class AlphaIAttributionEvent:
    kind: str  # blocked_buy | size_boost | exit_urgency | adverse_wait | score_feature
    base: str
    venue: str
    estimated_net_delta_eur: Decimal
    baseline_net_eur: Decimal
    alphai_net_eur: Decimal
    reasons: tuple[str, ...] = ()
    counterfactual: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "base": self.base,
            "venue": self.venue,
            "estimated_net_delta_eur": str(self.estimated_net_delta_eur.quantize(Decimal("0.01"))),
            "baseline_net_eur": str(self.baseline_net_eur.quantize(Decimal("0.01"))),
            "alphai_net_eur": str(self.alphai_net_eur.quantize(Decimal("0.01"))),
            "reasons": list(self.reasons),
            "counterfactual": self.counterfactual,
        }


@dataclass
class AlphaIAttributionStore:
    events: list[AlphaIAttributionEvent] = field(default_factory=list)
    blocked_buys: int = 0
    size_boosts: int = 0
    exit_urgencies: int = 0
    adverse_waits: int = 0
    sum_blocked_avoided_loss: Decimal = _ZERO
    sum_size_boost_delta: Decimal = _ZERO
    sum_exit_urgency_delta: Decimal = _ZERO
    auto_apply: bool = False

    def record(self, event: AlphaIAttributionEvent) -> None:
        self.events.append(event)
        if len(self.events) > 2000:
            self.events = self.events[-1500:]
        if event.kind == "blocked_buy":
            self.blocked_buys += 1
            # Positive delta = avoided loss when alphai_net < baseline and baseline toxic
            if event.estimated_net_delta_eur > 0:
                self.sum_blocked_avoided_loss += event.estimated_net_delta_eur
        elif event.kind == "size_boost":
            self.size_boosts += 1
            self.sum_size_boost_delta += event.estimated_net_delta_eur
        elif event.kind == "exit_urgency":
            self.exit_urgencies += 1
            self.sum_exit_urgency_delta += event.estimated_net_delta_eur
        elif event.kind == "adverse_wait":
            self.adverse_waits += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "alphai_attr_blocked_buys": self.blocked_buys,
            "alphai_attr_size_boosts": self.size_boosts,
            "alphai_attr_exit_urgencies": self.exit_urgencies,
            "alphai_attr_adverse_waits": self.adverse_waits,
            "alphai_attr_avoided_loss_eur": str(
                self.sum_blocked_avoided_loss.quantize(Decimal("0.01"))
            ),
            "alphai_attr_size_boost_delta_eur": str(
                self.sum_size_boost_delta.quantize(Decimal("0.01"))
            ),
            "alphai_attr_exit_urgency_delta_eur": str(
                self.sum_exit_urgency_delta.quantize(Decimal("0.01"))
            ),
            "alphai_attr_auto_apply": self.auto_apply,
            "alphai_attr_event_count": len(self.events),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked_buys": self.blocked_buys,
            "size_boosts": self.size_boosts,
            "exit_urgencies": self.exit_urgencies,
            "adverse_waits": self.adverse_waits,
            "sum_blocked_avoided_loss": str(self.sum_blocked_avoided_loss),
            "sum_size_boost_delta": str(self.sum_size_boost_delta),
            "sum_exit_urgency_delta": str(self.sum_exit_urgency_delta),
            "auto_apply": self.auto_apply,
            "events": [e.as_dict() for e in self.events[-200:]],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> AlphaIAttributionStore:
        store = cls()
        if not isinstance(raw, dict):
            return store
        store.blocked_buys = int(raw.get("blocked_buys") or 0)
        store.size_boosts = int(raw.get("size_boosts") or 0)
        store.exit_urgencies = int(raw.get("exit_urgencies") or 0)
        store.adverse_waits = int(raw.get("adverse_waits") or 0)
        store.sum_blocked_avoided_loss = Decimal(str(raw.get("sum_blocked_avoided_loss") or 0))
        store.sum_size_boost_delta = Decimal(str(raw.get("sum_size_boost_delta") or 0))
        store.sum_exit_urgency_delta = Decimal(str(raw.get("sum_exit_urgency_delta") or 0))
        store.auto_apply = bool(raw.get("auto_apply") or False)
        return store

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: Path | str) -> AlphaIAttributionStore:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return cls()

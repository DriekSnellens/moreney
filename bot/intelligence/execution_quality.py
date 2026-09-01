"""Execution Quality Engine — maker/taker/wait decisions and fill statistics."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from bot.intelligence.adverse_selection import AdverseSelectionAssessment
from bot.intelligence.market_regime_engine import MarketRegimeAssessment

_ZERO = Decimal("0")
_ONE = Decimal("1")


class ExecutionDecision(str, Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"
    WAIT = "WAIT"
    REJECT = "REJECT"


class Urgency(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class ExecutionQualityConfig:
    enabled: bool = True
    min_maker_edge_eur: Decimal = Decimal("0.10")
    min_taker_edge_eur: Decimal = Decimal("0.05")
    maker_wait_penalty_per_min: Decimal = Decimal("0.02")
    adverse_penalty_weight: Decimal = Decimal("0.35")
    churn_cancel_penalty: Decimal = Decimal("0.05")
    default_maker_fill_probability: Decimal = Decimal("0.40")
    default_taker_fill_probability: Decimal = Decimal("0.95")
    default_expected_wait_minutes: Decimal = Decimal("10")


@dataclass(frozen=True, slots=True)
class ExecutionQualityAssessment:
    decision: ExecutionDecision
    urgency: Urgency
    fill_probability: Decimal
    expected_maker_net_eur: Decimal | None
    expected_taker_net_eur: Decimal | None
    expected_wait_minutes: Decimal
    execution_score: Decimal
    reasons: tuple[str, ...]


@dataclass
class ExecutionQualityBucket:
    attempts: int = 0
    fills: int = 0
    cancels: int = 0
    partials: int = 0
    toxic_fills: int = 0
    sum_slippage_bps: Decimal = _ZERO
    sum_adverse_bps: Decimal = _ZERO
    sum_mfe_capture: Decimal = _ZERO
    sum_realized_net: Decimal = _ZERO
    sum_hold_seconds: Decimal = _ZERO
    maker_fills: int = 0
    taker_fills: int = 0
    maker_net: Decimal = _ZERO
    taker_net: Decimal = _ZERO

    def record_fill(
        self,
        *,
        is_maker: bool,
        net_eur: Decimal,
        slippage_bps: Decimal = _ZERO,
        adverse_bps: Decimal = _ZERO,
        mfe_capture: Decimal | None = None,
        hold_seconds: Decimal | None = None,
        toxic: bool = False,
    ) -> None:
        self.fills += 1
        self.sum_realized_net += net_eur
        self.sum_slippage_bps += slippage_bps
        self.sum_adverse_bps += adverse_bps
        if mfe_capture is not None:
            self.sum_mfe_capture += mfe_capture
        if hold_seconds is not None:
            self.sum_hold_seconds += hold_seconds
        if toxic:
            self.toxic_fills += 1
        if is_maker:
            self.maker_fills += 1
            self.maker_net += net_eur
        else:
            self.taker_fills += 1
            self.taker_net += net_eur

    def record_cancel(self) -> None:
        self.cancels += 1

    def record_attempt(self) -> None:
        self.attempts += 1

    def snapshot(self) -> dict[str, str | None]:
        n = self.fills or 0
        att = self.attempts or 0
        return {
            "attempts": str(att),
            "fills": str(n),
            "fill_rate": str((Decimal(n) / Decimal(att)).quantize(Decimal("0.01"))) if att else None,
            "cancel_rate": str((Decimal(self.cancels) / Decimal(att)).quantize(Decimal("0.01")))
            if att
            else None,
            "toxic_fill_rate": str((Decimal(self.toxic_fills) / Decimal(n)).quantize(Decimal("0.01")))
            if n
            else None,
            "avg_slippage_bps": str((self.sum_slippage_bps / n).quantize(Decimal("0.01"))) if n else None,
            "avg_adverse_bps": str((self.sum_adverse_bps / n).quantize(Decimal("0.01"))) if n else None,
            "avg_mfe_capture": str((self.sum_mfe_capture / n).quantize(Decimal("0.0001"))) if n else None,
            "avg_hold_seconds": str((self.sum_hold_seconds / n).quantize(Decimal("0.1"))) if n else None,
            "maker_fill_rate": str((Decimal(self.maker_fills) / Decimal(n)).quantize(Decimal("0.01")))
            if n
            else None,
            "maker_net_eur": str(self.maker_net.quantize(Decimal("0.01"))),
            "taker_net_eur": str(self.taker_net.quantize(Decimal("0.01"))),
        }


@dataclass
class ExecutionQualityStore:
    """Restart-safe execution statistics keyed by symbol/venue/strategy/regime."""

    buckets: dict[str, ExecutionQualityBucket] = field(default_factory=dict)
    cancel_count: int = 0
    replace_count: int = 0
    observation_cancels: int = 0

    def _key(
        self,
        *,
        symbol: str,
        venue: str,
        strategy: str,
        regime: str,
        order_type: str = "maker",
    ) -> str:
        return f"{symbol}|{venue}|{strategy}|{regime}|{order_type}"

    def bucket(
        self,
        *,
        symbol: str,
        venue: str,
        strategy: str,
        regime: str,
        order_type: str = "maker",
    ) -> ExecutionQualityBucket:
        key = self._key(
            symbol=symbol, venue=venue, strategy=strategy, regime=regime, order_type=order_type
        )
        if key not in self.buckets:
            self.buckets[key] = ExecutionQualityBucket()
        return self.buckets[key]

    def record_churn(self, *, cancel: bool = False, replace: bool = False) -> None:
        if cancel:
            self.cancel_count += 1
        if replace:
            self.replace_count += 1

    def snapshot(self) -> dict[str, Any]:
        total_fills = sum(b.fills for b in self.buckets.values())
        total_toxic = sum(b.toxic_fills for b in self.buckets.values())
        total_maker = sum(b.maker_fills for b in self.buckets.values())
        total_taker = sum(b.taker_fills for b in self.buckets.values())
        att = sum(b.attempts for b in self.buckets.values()) or 1
        return {
            "execution_fill_rate": str((Decimal(total_fills) / Decimal(att)).quantize(Decimal("0.01")))
            if att
            else None,
            "execution_toxic_fill_rate": str(
                (Decimal(total_toxic) / Decimal(total_fills)).quantize(Decimal("0.01"))
            )
            if total_fills
            else None,
            "execution_maker_fill_rate": str(
                (Decimal(total_maker) / Decimal(total_fills)).quantize(Decimal("0.01"))
            )
            if total_fills
            else None,
            "execution_taker_fill_rate": str(
                (Decimal(total_taker) / Decimal(total_fills)).quantize(Decimal("0.01"))
            )
            if total_fills
            else None,
            "execution_cancel_rate": str(
                (Decimal(self.cancel_count) / Decimal(max(1, att))).quantize(Decimal("0.01"))
            ),
            "execution_replace_rate": str(
                (Decimal(self.replace_count) / Decimal(max(1, att))).quantize(Decimal("0.01"))
            ),
            "execution_order_churn": str(self.cancel_count + self.replace_count),
            "execution_observation_cancels": str(self.observation_cancels),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "buckets": {k: vars(v) for k, v in self.buckets.items()},
            "cancel_count": self.cancel_count,
            "replace_count": self.replace_count,
            "observation_cancels": self.observation_cancels,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ExecutionQualityStore:
        store = cls()
        if not isinstance(raw, dict):
            return store
        store.cancel_count = int(raw.get("cancel_count") or 0)
        store.replace_count = int(raw.get("replace_count") or 0)
        store.observation_cancels = int(raw.get("observation_cancels") or 0)
        for key, val in (raw.get("buckets") or {}).items():
            if not isinstance(val, dict):
                continue
            b = ExecutionQualityBucket()
            for attr in (
                "attempts", "fills", "cancels", "partials", "toxic_fills",
                "maker_fills", "taker_fills",
            ):
                if attr in val:
                    setattr(b, attr, int(val[attr]))
            for attr in (
                "sum_slippage_bps", "sum_adverse_bps", "sum_mfe_capture",
                "sum_realized_net", "sum_hold_seconds", "maker_net", "taker_net",
            ):
                if attr in val:
                    setattr(b, attr, Decimal(str(val[attr])))
            store.buckets[str(key)] = b
        return store

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: Path | str) -> ExecutionQualityStore:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return cls()


def estimate_fill_probability(
    *,
    spread_pct: Decimal | None,
    adverse_score: Decimal,
    regime_confidence: Decimal,
    historical_fill_rate: Decimal | None,
    config: ExecutionQualityConfig | None = None,
) -> Decimal:
    """Simple explainable P(fill) — blends base rate with spread/adverse/regime."""
    cfg = config or ExecutionQualityConfig()
    base = historical_fill_rate if historical_fill_rate is not None else cfg.default_maker_fill_probability
    p = base
    if spread_pct is not None:
        if spread_pct > Decimal("0.006"):
            p *= Decimal("0.7")
        elif spread_pct < Decimal("0.001"):
            p *= Decimal("1.1")
    p *= _ONE - adverse_score * Decimal("0.4")
    p *= Decimal("0.85") + regime_confidence * Decimal("0.15")
    return max(Decimal("0.05"), min(_ONE, p))


def classify_urgency(
    *,
    opportunity_decay: Decimal | None = None,
    extension_pct: Decimal | None = None,
) -> Urgency:
    ext = extension_pct or _ZERO
    decay = opportunity_decay or _ZERO
    if ext >= Decimal("0.02") or decay >= Decimal("0.7"):
        return Urgency.HIGH
    if ext >= Decimal("0.01") or decay >= Decimal("0.4"):
        return Urgency.NORMAL
    return Urgency.LOW


def assess_execution(
    *,
    maker_net_eur: Decimal,
    taker_net_eur: Decimal,
    adverse: AdverseSelectionAssessment | None = None,
    regime: MarketRegimeAssessment | None = None,
    spread_pct: Decimal | None = None,
    urgency: Urgency = Urgency.NORMAL,
    historical_fill_rate: Decimal | None = None,
    config: ExecutionQualityConfig | None = None,
) -> ExecutionQualityAssessment:
    """Compare maker vs taker economics including wait and adverse selection."""
    cfg = config or ExecutionQualityConfig()
    adv_score = adverse.adverse_selection_score if adverse else Decimal("0.35")
    regime_conf = regime.confidence if regime else Decimal("0.5")
    fresh = regime.data_freshness_score if regime else _ONE

    if fresh < Decimal("0.2"):
        return ExecutionQualityAssessment(
            decision=ExecutionDecision.REJECT,
            urgency=urgency,
            fill_probability=_ZERO,
            expected_maker_net_eur=maker_net_eur,
            expected_taker_net_eur=taker_net_eur,
            expected_wait_minutes=cfg.default_expected_wait_minutes,
            execution_score=_ZERO,
            reasons=("stale_market_data",),
        )

    fill_p = estimate_fill_probability(
        spread_pct=spread_pct,
        adverse_score=adv_score,
        regime_confidence=regime_conf,
        historical_fill_rate=historical_fill_rate,
        config=cfg,
    )
    wait_min = cfg.default_expected_wait_minutes
    if urgency == Urgency.HIGH:
        wait_min = wait_min / Decimal("3")
    elif urgency == Urgency.LOW:
        wait_min = wait_min * Decimal("1.5")

    wait_cost = wait_min * cfg.maker_wait_penalty_per_min
    adverse_cost = adv_score * maker_net_eur * cfg.adverse_penalty_weight
    maker_ev = maker_net_eur * fill_p - wait_cost - adverse_cost
    taker_ev = taker_net_eur * cfg.default_taker_fill_probability

    reasons: list[str] = []
    if maker_ev >= taker_ev and maker_ev >= cfg.min_maker_edge_eur:
        decision = ExecutionDecision.MAKER
        reasons.append("maker_ev_higher")
    elif taker_ev >= cfg.min_taker_edge_eur:
        decision = ExecutionDecision.TAKER
        reasons.append("taker_ev_higher")
    elif maker_ev > _ZERO or taker_ev > _ZERO:
        decision = ExecutionDecision.WAIT
        reasons.append("insufficient_edge")
    else:
        decision = ExecutionDecision.REJECT
        reasons.append("negative_expected_net")

    if adv_score >= Decimal("0.75") and decision == ExecutionDecision.MAKER:
        decision = ExecutionDecision.WAIT
        reasons.append("adverse_selection_high")

    exec_score = max(_ZERO, max(maker_ev, taker_ev) / Decimal("2"))
    exec_score = min(_ONE, exec_score)

    return ExecutionQualityAssessment(
        decision=decision,
        urgency=urgency,
        fill_probability=fill_p,
        expected_maker_net_eur=maker_net_eur,
        expected_taker_net_eur=taker_net_eur,
        expected_wait_minutes=wait_min,
        execution_score=exec_score,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def config_from_settings(settings: Any) -> ExecutionQualityConfig:
    return ExecutionQualityConfig(
        enabled=bool(getattr(settings, "live_micro_execution_quality_enabled", True)),
        default_maker_fill_probability=Decimal(
            str(getattr(settings, "live_micro_maker_fill_probability", 0.40))
        ),
        default_taker_fill_probability=Decimal(
            str(getattr(settings, "live_micro_taker_fill_probability", 0.95))
        ),
    )

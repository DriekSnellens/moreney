"""Historical Outcome Learning — controlled empirical score adjustments."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class OutcomeLearningConfig:
    enabled: bool = True
    min_learning_samples: int = 20
    full_learning_samples: int = 50
    empirical_multiplier_min: Decimal = Decimal("0.80")
    empirical_multiplier_max: Decimal = Decimal("1.20")
    neutral_multiplier: Decimal = Decimal("1.0")
    weak_adjustment_strength: Decimal = Decimal("0.5")


@dataclass
class OutcomeRecord:
    symbol: str
    venue: str
    strategy: str
    regime: str
    order_type: str
    net_eur: Decimal = _ZERO
    hold_seconds: Decimal | None = None
    mfe_capture: Decimal | None = None
    adverse_bps: Decimal = _ZERO
    toxic: bool = False
    won: bool = False


@dataclass
class OutcomeBucket:
    samples: int = 0
    wins: int = 0
    sum_net_eur: Decimal = _ZERO
    sum_hold_seconds: Decimal = _ZERO
    sum_mfe_capture: Decimal = _ZERO
    sum_adverse_bps: Decimal = _ZERO
    toxic_count: int = 0

    def record(self, rec: OutcomeRecord) -> None:
        self.samples += 1
        if rec.won:
            self.wins += 1
        self.sum_net_eur += rec.net_eur
        if rec.hold_seconds is not None:
            self.sum_hold_seconds += rec.hold_seconds
        if rec.mfe_capture is not None:
            self.sum_mfe_capture += rec.mfe_capture
        self.sum_adverse_bps += rec.adverse_bps
        if rec.toxic:
            self.toxic_count += 1

    @property
    def win_rate(self) -> Decimal | None:
        if self.samples <= 0:
            return None
        return Decimal(self.wins) / Decimal(self.samples)

    @property
    def avg_net(self) -> Decimal | None:
        if self.samples <= 0:
            return None
        return self.sum_net_eur / Decimal(self.samples)

    @property
    def avg_mfe_capture(self) -> Decimal | None:
        if self.samples <= 0:
            return None
        return self.sum_mfe_capture / Decimal(self.samples)

    @property
    def avg_adverse_bps(self) -> Decimal | None:
        if self.samples <= 0:
            return None
        return self.sum_adverse_bps / Decimal(self.samples)

    @property
    def toxic_rate(self) -> Decimal | None:
        if self.samples <= 0:
            return None
        return Decimal(self.toxic_count) / Decimal(self.samples)

    def snapshot(self) -> dict[str, str | None]:
        return {
            "samples": str(self.samples),
            "win_rate": str(self.win_rate.quantize(Decimal("0.01"))) if self.win_rate else None,
            "avg_net_eur": str(self.avg_net.quantize(Decimal("0.01"))) if self.avg_net else None,
            "avg_mfe_capture": str(self.avg_mfe_capture.quantize(Decimal("0.0001")))
            if self.avg_mfe_capture
            else None,
            "avg_adverse_bps": str(self.avg_adverse_bps.quantize(Decimal("0.01")))
            if self.avg_adverse_bps
            else None,
            "toxic_rate": str(self.toxic_rate.quantize(Decimal("0.01"))) if self.toxic_rate else None,
        }


@dataclass
class OutcomeLearningStore:
    buckets: dict[str, OutcomeBucket] = field(default_factory=dict)

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

    def record(self, rec: OutcomeRecord) -> None:
        key = self._key(
            symbol=rec.symbol,
            venue=rec.venue,
            strategy=rec.strategy,
            regime=rec.regime,
            order_type=rec.order_type,
        )
        if key not in self.buckets:
            self.buckets[key] = OutcomeBucket()
        self.buckets[key].record(rec)

    def bucket(
        self,
        *,
        symbol: str,
        venue: str,
        strategy: str,
        regime: str,
        order_type: str = "maker",
    ) -> OutcomeBucket | None:
        key = self._key(
            symbol=symbol, venue=venue, strategy=strategy, regime=regime, order_type=order_type
        )
        return self.buckets.get(key)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, b in self.buckets.items():
            out[key] = {
                "samples": b.samples,
                "wins": b.wins,
                "sum_net_eur": str(b.sum_net_eur),
                "sum_hold_seconds": str(b.sum_hold_seconds),
                "sum_mfe_capture": str(b.sum_mfe_capture),
                "sum_adverse_bps": str(b.sum_adverse_bps),
                "toxic_count": b.toxic_count,
            }
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> OutcomeLearningStore:
        store = cls()
        if not isinstance(raw, dict):
            return store
        for key, val in raw.items():
            if not isinstance(val, dict):
                continue
            b = OutcomeBucket(
                samples=int(val.get("samples") or 0),
                wins=int(val.get("wins") or 0),
                sum_net_eur=Decimal(str(val.get("sum_net_eur") or 0)),
                sum_hold_seconds=Decimal(str(val.get("sum_hold_seconds") or 0)),
                sum_mfe_capture=Decimal(str(val.get("sum_mfe_capture") or 0)),
                sum_adverse_bps=Decimal(str(val.get("sum_adverse_bps") or 0)),
                toxic_count=int(val.get("toxic_count") or 0),
            )
            store.buckets[str(key)] = b
        return store

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: Path | str) -> OutcomeLearningStore:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return cls()


def empirical_multiplier(
    *,
    bucket: OutcomeBucket | None,
    config: OutcomeLearningConfig | None = None,
) -> Decimal:
    """Return multiplier in [min, max] with shrinkage toward neutral."""
    cfg = config or OutcomeLearningConfig()
    if bucket is None or bucket.samples < cfg.min_learning_samples:
        return cfg.neutral_multiplier

    strength = _ONE
    confidence = "FULL"
    if bucket.samples < cfg.full_learning_samples:
        strength = cfg.weak_adjustment_strength
        confidence = "WEAK"

    win_rate = bucket.win_rate or Decimal("0.5")
    avg_net = bucket.avg_net or _ZERO
    mfe = bucket.avg_mfe_capture or Decimal("0.5")
    toxic = bucket.toxic_rate or _ZERO

    quality = (
        win_rate * Decimal("0.35")
        + (Decimal("0.5") + avg_net) * Decimal("0.25")
        + mfe * Decimal("0.25")
        - toxic * Decimal("0.35")
    )
    quality = max(Decimal("0.2"), min(Decimal("1.2"), quality))

    raw = cfg.neutral_multiplier + (quality - Decimal("0.5")) * strength * Decimal("0.4")

    # Shrinkage: small samples pull harder toward neutral (e.g. 3/3 wins → not 1.20)
    shrink = min(_ONE, Decimal(bucket.samples) / Decimal(cfg.full_learning_samples))
    adjusted = cfg.neutral_multiplier + (raw - cfg.neutral_multiplier) * shrink

    return max(cfg.empirical_multiplier_min, min(cfg.empirical_multiplier_max, adjusted))


def learning_confidence(bucket: OutcomeBucket | None, config: OutcomeLearningConfig | None = None) -> tuple[str, int]:
    """Return (confidence_label, sample_count)."""
    cfg = config or OutcomeLearningConfig()
    if bucket is None:
        return "NEUTRAL", 0
    n = bucket.samples
    if n < cfg.min_learning_samples:
        return "NEUTRAL", n
    if n < cfg.full_learning_samples:
        return "WEAK", n
    return "FULL", n


@dataclass(frozen=True, slots=True)
class PnLAttribution:
    """Attribution layer — does not replace existing PnL definitions."""

    gross_edge: Decimal = _ZERO
    fees: Decimal = _ZERO
    slippage: Decimal = _ZERO
    spread_cost: Decimal = _ZERO
    adverse_selection_cost: Decimal = _ZERO
    execution_alpha: Decimal = _ZERO
    timing_alpha: Decimal = _ZERO
    exit_alpha: Decimal = _ZERO
    net: Decimal = _ZERO

    def snapshot(self) -> dict[str, str]:
        return {k: str(getattr(self, k).quantize(Decimal("0.0001"))) for k in (
            "gross_edge", "fees", "slippage", "spread_cost",
            "adverse_selection_cost", "execution_alpha", "timing_alpha",
            "exit_alpha", "net",
        )}


def config_from_settings(settings: Any) -> OutcomeLearningConfig:
    return OutcomeLearningConfig(
        enabled=bool(getattr(settings, "live_micro_outcome_learning_enabled", True)),
        min_learning_samples=int(getattr(settings, "live_micro_min_learning_samples", 20)),
        full_learning_samples=int(getattr(settings, "live_micro_learning_full_samples", 50)),
        empirical_multiplier_min=Decimal(
            str(getattr(settings, "live_micro_empirical_multiplier_min", 0.80))
        ),
        empirical_multiplier_max=Decimal(
            str(getattr(settings, "live_micro_empirical_multiplier_max", 1.20))
        ),
    )

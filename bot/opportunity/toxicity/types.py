"""Types for pre-trade toxicity prediction (decision-time only features)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PreTradeFeatures:
    """Information available at quote decision time — never post-fill labels."""

    timestamp: str
    opportunity_id: str
    venue: str  # primary fill venue (buy for maker bid, sell for ask)
    route: str
    symbol: str
    side: str
    strategy: str
    fill_type: str  # expected: trade_through when queue off
    # Market / quote state (may be missing → empty / zero)
    spread_bps: Decimal = _ZERO
    book_age_ms: Decimal = _ZERO
    quote_age_bucket: str = "unknown"
    spread_bucket: str = "unknown"
    vol_bucket: str = "unknown"
    regime: str = "unknown"
    fair_value_deviation_bps: Decimal = _ZERO
    inventory_direction: str = "unknown"
    # Deterministic economics at decision time
    expected_gross_eur: Decimal = _ZERO
    expected_fees_eur: Decimal = _ZERO
    expected_slippage_eur: Decimal = _ZERO
    expected_buffer_eur: Decimal = _ZERO
    expected_net_eur: Decimal = _ZERO
    notional_eur: Decimal = _ZERO

    def as_dict(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class ToxicityPrediction:
    """Pre-trade prediction of adverse selection conditional on fill."""

    expected_adverse_bps: Decimal
    expected_adverse_eur: Decimal
    sample_count: int
    uncertainty_bps: Decimal
    shrinkage_source: str
    toxicity_percentile: Decimal | None = None
    model_name: str = ""
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "expected_adverse_bps": str(self.expected_adverse_bps),
            "expected_adverse_eur": str(self.expected_adverse_eur),
            "sample_count": self.sample_count,
            "uncertainty_bps": str(self.uncertainty_bps),
            "shrinkage_source": self.shrinkage_source,
            "toxicity_percentile": (
                str(self.toxicity_percentile)
                if self.toxicity_percentile is not None
                else None
            ),
            "model_name": self.model_name,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    """Shadow admission — never alters live execution."""

    accept: bool
    reason: str
    expected_net_before_toxicity: Decimal
    expected_adverse_eur: Decimal
    uncertainty_penalty_eur: Decimal
    toxicity_adjusted_net: Decimal
    prediction: ToxicityPrediction

    def as_dict(self) -> dict[str, Any]:
        return {
            "accept": self.accept,
            "reason": self.reason,
            "expected_net_before_toxicity": str(self.expected_net_before_toxicity),
            "expected_adverse_eur": str(self.expected_adverse_eur),
            "uncertainty_penalty_eur": str(self.uncertainty_penalty_eur),
            "toxicity_adjusted_net": str(self.toxicity_adjusted_net),
            "prediction": self.prediction.as_dict(),
        }


@dataclass
class LabeledEvent:
    """One completed fill observation for causal learning (labels post-fill only)."""

    features: PreTradeFeatures
    # Labels — MUST NOT be used as features
    realized_net_eur: Decimal
    realized_adverse_eur: Decimal
    adverse_bps_proxy: Decimal  # side-adjusted EUR adverse → bps via notional
    markout_1s_bps: Decimal | None = None
    markout_5s_bps: Decimal | None = None
    markout_30s_bps: Decimal | None = None
    markout_60s_bps: Decimal | None = None
    fill_type_observed: str = "trade_through"
    won: bool = False

    def label_bps(self, horizon: str = "5s") -> Decimal:
        mapping = {
            "1s": self.markout_1s_bps,
            "5s": self.markout_5s_bps if self.markout_5s_bps is not None else self.adverse_bps_proxy,
            "30s": self.markout_30s_bps,
            "60s": self.markout_60s_bps,
            "proxy": self.adverse_bps_proxy,
        }
        val = mapping.get(horizon)
        return val if val is not None else self.adverse_bps_proxy

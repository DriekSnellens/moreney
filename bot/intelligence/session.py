"""Intelligence session — coordinates stores, diagnostics, and persistence."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.intelligence.adverse_selection import AdverseSelectionConfig, config_from_settings as adverse_cfg
from bot.intelligence.capital_intelligence import CapitalIntelligenceConfig, config_from_settings as capital_cfg
from bot.intelligence.execution_quality import ExecutionQualityStore, config_from_settings as exec_cfg
from bot.intelligence.market_regime_engine import MarketRegimeAssessment
from bot.intelligence.outcome_learning import OutcomeLearningStore, config_from_settings as learning_cfg
from bot.intelligence.resting_order_intelligence import RestingOrderConfig, config_from_settings as resting_cfg

logger = logging.getLogger(__name__)


@dataclass
class IntelligenceSession:
    """Restart-safe intelligence state for live micro."""

    adverse_config: AdverseSelectionConfig = field(default_factory=AdverseSelectionConfig)
    execution_config: Any = field(default_factory=lambda: exec_cfg(None))
    learning_config: Any = field(default_factory=lambda: learning_cfg(None))
    capital_config: CapitalIntelligenceConfig = field(default_factory=CapitalIntelligenceConfig)
    resting_config: RestingOrderConfig = field(default_factory=RestingOrderConfig)
    execution_store: ExecutionQualityStore = field(default_factory=ExecutionQualityStore)
    outcome_store: OutcomeLearningStore = field(default_factory=OutcomeLearningStore)
    observation_mode: bool = True
    current_regime: MarketRegimeAssessment | None = None
    _pending_post_fill: dict[str, dict[str, Any]] = field(default_factory=dict)
    churn_without_net_improvement: int = 0

    @classmethod
    def from_settings(cls, settings: Any) -> IntelligenceSession:
        return cls(
            adverse_config=adverse_cfg(settings),
            execution_config=exec_cfg(settings),
            learning_config=learning_cfg(settings),
            capital_config=capital_cfg(settings),
            resting_config=resting_cfg(settings),
            observation_mode=bool(
                getattr(settings, "live_micro_intelligence_observation_mode", True)
            ),
        )

    def snapshot(self) -> dict[str, Any]:
        regime = self.current_regime
        out: dict[str, Any] = {
            "intelligence_observation_mode": self.observation_mode,
            **self.execution_store.snapshot(),
        }
        if regime is not None:
            out.update({
                "market_regime": regime.regime.value,
                "market_regime_confidence": str(regime.confidence.quantize(Decimal("0.01"))),
                "market_regime_reasons": ",".join(regime.reasons),
                "data_freshness_score": str(regime.data_freshness_score.quantize(Decimal("0.01"))),
                "regime_return_5m": (
                    str(regime.return_5m.quantize(Decimal("0.0001")))
                    if regime.return_5m is not None
                    else None
                ),
                "regime_realized_volatility": (
                    str(regime.realized_volatility.quantize(Decimal("0.0001")))
                    if regime.realized_volatility is not None
                    else None
                ),
                "regime_orderbook_imbalance": (
                    str(regime.orderbook_imbalance.quantize(Decimal("0.01")))
                    if regime.orderbook_imbalance is not None
                    else None
                ),
            })
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution": self.execution_store.to_dict(),
            "outcomes": self.outcome_store.to_dict(),
            "observation_mode": self.observation_mode,
            "churn_without_net_improvement": self.churn_without_net_improvement,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None, settings: Any) -> IntelligenceSession:
        sess = cls.from_settings(settings)
        if not isinstance(raw, dict):
            return sess
        sess.execution_store = ExecutionQualityStore.from_dict(raw.get("execution"))
        sess.outcome_store = OutcomeLearningStore.from_dict(raw.get("outcomes"))
        if "observation_mode" in raw:
            sess.observation_mode = bool(raw["observation_mode"])
        sess.churn_without_net_improvement = int(raw.get("churn_without_net_improvement") or 0)
        return sess

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
            tmp.replace(p)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intelligence state save failed: %s", exc)

    @classmethod
    def load(cls, path: Path | str, settings: Any) -> IntelligenceSession:
        p = Path(path)
        if not p.exists():
            return cls.from_settings(settings)
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")), settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intelligence state load failed: %s", exc)
            return cls.from_settings(settings)

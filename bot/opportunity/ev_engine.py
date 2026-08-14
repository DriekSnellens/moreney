"""Expected value layer above deterministic NET profit."""

from __future__ import annotations

from decimal import Decimal

from bot.core.config import Settings
from bot.core.models import ProfitabilityResult, TradeOpportunity

_ZERO = Decimal("0")
_ONE = Decimal("1")


class ExpectedValueEngine:
    """Risk-adjusted EV: P(win)*gain - P(loss)*loss - costs."""

    def __init__(self, settings: Settings, *, markout_win_rate: float | None = None) -> None:
        self._settings = settings
        self._default_p_win = float(getattr(settings, "opportunity_default_win_prob", 0.55))
        self._markout_win_rate = markout_win_rate

    def enrich(
        self,
        opportunity: TradeOpportunity,
        profitability: ProfitabilityResult,
        *,
        regime_weight: Decimal = _ONE,
        transfer_cost: Decimal = _ZERO,
    ) -> dict[str, Decimal | float]:
        net = profitability.net_profit_usd
        notional = opportunity.quantity * opportunity.entry_price
        if notional <= 0:
            return {
                "expected_value": _ZERO,
                "probability_profit": self._default_p_win,
                "expected_loss": _ZERO,
                "risk_reward": _ZERO,
            }

        p_win = self._resolve_probability(opportunity, profitability)
        p_loss = 1.0 - p_win

        gain = max(net, _ZERO)
        loss = abs(min(net, _ZERO))
        if loss <= 0 and net > 0:
            loss = notional * Decimal(str(getattr(self._settings, "opportunity_default_loss_pct", 0.002)))

        adverse_bps = Decimal(str(opportunity.metadata.get("adverse_bps", 0) or 0))
        if adverse_bps > 0:
            loss = max(loss, notional * adverse_bps / Decimal("10000"))

        ev = (
            Decimal(str(p_win)) * gain
            - Decimal(str(p_loss)) * loss
            - transfer_cost
        ) * regime_weight

        rr = gain / loss if loss > 0 else gain

        return {
            "expected_value": ev,
            "probability_profit": p_win,
            "expected_loss": loss,
            "risk_reward": rr,
        }

    def _resolve_probability(
        self,
        opportunity: TradeOpportunity,
        profitability: ProfitabilityResult,
    ) -> float:
        if opportunity.confidence > 0:
            return min(0.95, max(0.05, opportunity.confidence))
        if self._markout_win_rate is not None and self._markout_win_rate > 0:
            return min(0.95, max(0.05, self._markout_win_rate))
        meta = opportunity.metadata or {}
        if "win_probability" in meta:
            return min(0.95, max(0.05, float(meta["win_probability"])))
        net_return = float(profitability.net_return)
        if net_return > 0.01:
            return min(0.85, self._default_p_win + 0.1)
        if net_return > 0.001:
            return self._default_p_win
        return max(0.35, self._default_p_win - 0.15)

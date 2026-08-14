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
        meta = opportunity.metadata or {}
        post_only = bool(meta.get("post_only"))

        gain = max(net, _ZERO)
        loss = abs(min(net, _ZERO))
        adverse_bps = Decimal(str(meta.get("adverse_bps", 0) or 0))
        if post_only:
            # Resting maker: unfilled quotes expire at 0, they do not take a
            # 20 bp inventory shock. Adverse is already inside NET.
            through = Decimal(
                str(getattr(self._settings, "paper_maker_trade_through_fill_pct", 0) or 0)
            )
            queue = Decimal(
                str(getattr(self._settings, "paper_maker_queue_fill_pct", 0) or 0)
            )
            p_fill = max(through, queue)
            if p_fill <= 0:
                p_fill = Decimal(str(p_win))
            p_win = float(p_fill)
            loss = (
                notional * adverse_bps / Decimal("10000") if adverse_bps > 0 else _ZERO
            )
            ev = (p_fill * gain - transfer_cost) * regime_weight
        else:
            if loss <= 0 and net > 0:
                loss = notional * Decimal(
                    str(getattr(self._settings, "opportunity_default_loss_pct", 0.002))
                )
            if adverse_bps > 0:
                loss = max(loss, notional * adverse_bps / Decimal("10000"))
            p_loss = 1.0 - p_win
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

"""Expected value layer above deterministic NET profit.

Maker fills in this paper stack are trade-through conditioned when
``paper_maker_queue_fill_pct=0``: a fill occurs only when the book trades
through the quote. Then ``NET`` measured at quote time is *not* independent
of the fill event, so:

    EV = P(fill | state) × E(NET | fill, state)

not ``p_fill × unconditional_NET``.
"""

from __future__ import annotations

from decimal import Decimal

from bot.core.config import Settings
from bot.core.models import ProfitabilityResult, TradeOpportunity

_ZERO = Decimal("0")
_ONE = Decimal("1")
_BPS = Decimal("10000")


class ExpectedValueEngine:
    """Risk-adjusted EV with fill-conditional maker expectation."""

    def __init__(
        self,
        settings: Settings,
        *,
        markout_win_rate: float | None = None,
        markout_samples: int = 0,
        min_markout_samples: int = 20,
        conditional_adverse_bps: Decimal | None = None,
    ) -> None:
        self._settings = settings
        self._default_p_win = float(getattr(settings, "opportunity_default_win_prob", 0.55))
        # Only trust empirical markout win rate once the sample is large enough.
        self._markout_win_rate: float | None = None
        if (
            markout_win_rate is not None
            and markout_win_rate > 0
            and markout_samples >= min_markout_samples
        ):
            self._markout_win_rate = markout_win_rate
        # E[adverse_bps | fill] for trade-through conditioned maker fills.
        self._conditional_adverse_bps = conditional_adverse_bps

    def enrich(
        self,
        opportunity: TradeOpportunity,
        profitability: ProfitabilityResult,
        *,
        regime_weight: Decimal = _ONE,
        transfer_cost: Decimal = _ZERO,
        inventory_relief: Decimal = _ZERO,
        conditional_adverse_bps: Decimal | None = None,
    ) -> dict[str, Decimal | float]:
        net = profitability.net_profit_usd
        notional = opportunity.quantity * opportunity.entry_price
        if notional <= 0:
            return {
                "expected_value": _ZERO,
                "probability_profit": self._default_p_win,
                "expected_loss": _ZERO,
                "risk_reward": _ZERO,
                "p_fill": _ZERO,
                "e_net_given_fill": _ZERO,
                "fill_conditioned": False,
            }

        p_win = self._resolve_probability(opportunity, profitability)
        meta = opportunity.metadata or {}
        post_only = bool(meta.get("post_only"))

        gain = max(net, _ZERO)
        # Inventory relief may increase gain but never rescue a non-positive NET.
        if net > 0 and inventory_relief > 0:
            gain = gain + inventory_relief
        loss = abs(min(net, _ZERO))
        adverse_bps = Decimal(str(meta.get("adverse_bps", 0) or 0))
        fill_conditioned = False
        p_fill = Decimal(str(p_win))
        e_net_given_fill = net + (inventory_relief if net > 0 else _ZERO)

        if post_only:
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

            # Trade-through-only fills ⇒ NET at quote time is optimistic vs fill.
            trade_through_only = through > 0 and queue <= 0
            if trade_through_only:
                fill_conditioned = True
                cond_bps = conditional_adverse_bps
                if cond_bps is None:
                    cond_bps = self._conditional_adverse_bps
                if cond_bps is None:
                    cond_bps = adverse_bps
                # Buffer already inside NET; charge only the *extra* conditional gap.
                buffer_bps = Decimal(
                    str(getattr(self._settings, "profitability_execution_buffer_bps", 0) or 0)
                )
                # Maker builds buffer as 1 + adverse; prefer meta/adverse when set.
                already = max(buffer_bps, Decimal("1") + adverse_bps)
                extra_bps = max(_ZERO, Decimal(str(cond_bps)) - already)
                e_net_given_fill = (
                    net
                    - notional * extra_bps / _BPS
                    + (inventory_relief if net > 0 else _ZERO)
                )
                # Inventory relief still cannot flip a non-positive conditional NET.
                if net <= 0:
                    e_net_given_fill = min(e_net_given_fill, net)
                gain = max(e_net_given_fill, _ZERO)
                loss = (
                    notional * Decimal(str(cond_bps)) / _BPS
                    if Decimal(str(cond_bps)) > 0
                    else _ZERO
                )
            else:
                loss = (
                    notional * adverse_bps / _BPS if adverse_bps > 0 else _ZERO
                )
            ev = (p_fill * e_net_given_fill - transfer_cost) * regime_weight
        else:
            if loss <= 0 and net > 0:
                loss = notional * Decimal(
                    str(getattr(self._settings, "opportunity_default_loss_pct", 0.002))
                )
            if adverse_bps > 0:
                loss = max(loss, notional * adverse_bps / _BPS)
            p_loss = 1.0 - p_win
            ev = (
                Decimal(str(p_win)) * gain
                - Decimal(str(p_loss)) * loss
                - transfer_cost
            ) * regime_weight
            e_net_given_fill = gain - (
                Decimal(str(1.0 - p_win)) * loss if p_win < 1 else _ZERO
            )

        rr = gain / loss if loss > 0 else gain

        return {
            "expected_value": ev,
            "probability_profit": p_win,
            "expected_loss": loss,
            "risk_reward": rr,
            "p_fill": p_fill,
            "e_net_given_fill": e_net_given_fill,
            "fill_conditioned": fill_conditioned,
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

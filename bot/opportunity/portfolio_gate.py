"""Portfolio-level exposure and correlation gate."""

from __future__ import annotations

from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import RiskRejectReason
from bot.core.models import PortfolioSnapshot, TradeOpportunity
from bot.opportunity.models import ScoredOpportunity

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def correlation_group_for_symbol(symbol: str) -> str:
    """Match InstrumentRegistry crypto grouping (not symbol[:3])."""
    sym = (symbol or "").upper()
    if "BTC" in sym:
        return "crypto_btc_beta"
    if "ETH" in sym:
        return "crypto_eth_beta"
    if sym:
        return "crypto_alt"
    return "general"


class PortfolioExposureGate:
    """Reject opportunities that worsen concentration or correlation."""

    def __init__(self, settings: Settings) -> None:
        self._max_corr_pct = Decimal(str(getattr(settings, "global_max_correlation_exposure_pct", 40)))
        self._max_strategy_pct = Decimal(
            str(getattr(settings, "global_max_strategy_exposure_pct", 50))
        )
        self._max_venue_pct = Decimal(str(getattr(settings, "global_max_venue_exposure_pct", 35)))
        self._exposure: dict[str, Decimal] = {}
        self._strategy_exposure: dict[str, Decimal] = {}
        self._venue_exposure: dict[str, Decimal] = {}

    def sync_from_portfolio(self, portfolio: PortfolioSnapshot) -> None:
        self._exposure = {}
        for pos in portfolio.positions:
            group = correlation_group_for_symbol(pos.symbol or "")
            notional = abs(pos.quantity * pos.average_entry_price)
            self._exposure[group] = self._exposure.get(group, _ZERO) + notional

    def record_fill(self, scored: ScoredOpportunity, notional: Decimal) -> None:
        group = scored.correlation_group or correlation_group_for_symbol(
            scored.opportunity.symbol
        )
        self._exposure[group] = self._exposure.get(group, _ZERO) + notional
        strat = scored.opportunity.strategy_name
        self._strategy_exposure[strat] = self._strategy_exposure.get(strat, _ZERO) + notional
        meta = scored.opportunity.metadata or {}
        for venue in (
            str(meta.get("buy_exchange") or ""),
            str(meta.get("sell_exchange") or ""),
        ):
            if venue:
                self._venue_exposure[venue] = self._venue_exposure.get(venue, _ZERO) + notional

    def check(
        self,
        scored: ScoredOpportunity,
        portfolio: PortfolioSnapshot,
    ) -> tuple[bool, str, RiskRejectReason | None]:
        equity = portfolio.equity_usd
        if equity <= 0:
            return True, "", None

        notional = scored.opportunity.quantity * scored.opportunity.entry_price
        group = scored.correlation_group or correlation_group_for_symbol(
            scored.opportunity.symbol
        )
        corr_after = (self._exposure.get(group, _ZERO) + notional) / equity * _HUNDRED
        if corr_after > self._max_corr_pct:
            return (
                False,
                f"Correlation group {group} exposure {corr_after:.2f}% > {self._max_corr_pct}%",
                RiskRejectReason.CORRELATION_LIMIT,
            )

        strat = scored.opportunity.strategy_name
        strat_after = (self._strategy_exposure.get(strat, _ZERO) + notional) / equity * _HUNDRED
        if strat_after > self._max_strategy_pct:
            return (
                False,
                f"Strategy {strat} exposure {strat_after:.2f}% > {self._max_strategy_pct}%",
                RiskRejectReason.STRATEGY_EXPOSURE_LIMIT,
            )

        meta = scored.opportunity.metadata or {}
        buy = str(meta.get("buy_exchange") or "")
        sell = str(meta.get("sell_exchange") or "")
        for venue in {v for v in (buy, sell) if v}:
            venue_after = (self._venue_exposure.get(venue, _ZERO) + notional) / equity * _HUNDRED
            if venue_after > self._max_venue_pct:
                return (
                    False,
                    f"Venue {venue} exposure {venue_after:.2f}% > {self._max_venue_pct}%",
                    RiskRejectReason.VENUE_EXPOSURE_LIMIT,
                )

        return True, "", None

    def snapshot(self) -> dict[str, dict[str, str]]:
        return {
            "correlation": {k: str(v) for k, v in self._exposure.items()},
            "strategy": {k: str(v) for k, v in self._strategy_exposure.items()},
            "venue": {k: str(v) for k, v in self._venue_exposure.items()},
        }

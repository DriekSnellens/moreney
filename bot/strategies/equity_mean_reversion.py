"""Equity mean-reversion strategy (stub quotes for paper architecture)."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.strategies.base import BaseStrategy

_ZERO = Decimal("0")


class EquityMeanReversionStrategy(BaseStrategy):
    """Range-bound equity mean reversion when stub/live equity feed available."""

    name = "equity_mean_reversion"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = bool(getattr(settings, "global_equity_enabled", False))
        self._profitability = DefaultProfitabilityEngine(settings)
        self._means: dict[str, Decimal] = {}
        self._scan_rejections = 0
        self._opportunities_emitted = 0
        self._deviation_bps = Decimal(
            str(getattr(settings, "global_equity_deviation_bps", 30) or 30)
        )

    async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]:
        return []

    async def evaluate_markets(
        self,
        snapshots: Sequence[MarketSnapshot],
        *,
        equity: Decimal | None = None,
        inventory: object = None,
    ) -> list[TradeOpportunity]:
        if not self._enabled:
            return []
        eq_snaps = [
            s for s in snapshots if (s.metadata or {}).get("asset_class") == "equity"
        ]
        out: list[TradeOpportunity] = []
        for snap in eq_snaps:
            if snap.bid <= 0:
                continue
            mid = snap.mid
            sym = snap.symbol.upper()
            mean = self._means.get(sym, mid)
            self._means[sym] = mean if mean == mid else (mean * Decimal("0.9") + mid * Decimal("0.1"))
            if mean <= 0:
                continue
            dev_bps = (mid - mean) / mean * Decimal("10000")
            if abs(dev_bps) < self._deviation_bps:
                self._scan_rejections += 1
                continue
            side = OpportunitySide.SELL if dev_bps > 0 else OpportunitySide.BUY
            qty = Decimal("1")
            if equity and equity > 0:
                qty = min(Decimal("10"), (equity * Decimal("0.02")) / mid)
            opp = TradeOpportunity(
                strategy_name=self.name,
                symbol=sym,
                side=side,
                quantity=qty,
                entry_price=mid,
                expected_exit_price=mean,
                confidence=min(0.8, float(abs(dev_bps) / Decimal("100"))),
                rationale=f"Equity deviation {dev_bps:.1f} bps from mean",
                market=snap,
                entry_fee_role=FeeRole.TAKER,
                exit_fee_role=FeeRole.TAKER,
                metadata={
                    "asset_class": "equity",
                    "deviation_bps": str(dev_bps),
                    "buy_exchange": snap.exchange or "equity_stub",
                },
            )
            est = await self._profitability.evaluate(opp)
            if not est.trade_allowed:
                self._scan_rejections += 1
                continue
            self._opportunities_emitted += 1
            out.append(opp)
        return out

    def scan_stats(self) -> dict[str, object]:
        return {
            "pairs_evaluated": self._opportunities_emitted + self._scan_rejections,
            "scan_rejections": self._scan_rejections,
            "opportunities_emitted": self._opportunities_emitted,
            "reject_counts": {},
        }

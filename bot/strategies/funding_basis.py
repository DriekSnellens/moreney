"""Crypto funding / basis opportunities from perp funding rates."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.strategies.base import BaseStrategy

_ZERO = Decimal("0")


class FundingBasisStrategy(BaseStrategy):
    """Market-neutral funding capture when |funding| clears fees (paper model)."""

    name = "funding_basis"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = bool(getattr(settings, "global_funding_strategy_enabled", True))
        self._min_funding_bps = Decimal(str(getattr(settings, "global_min_funding_bps", 3) or 3))
        self._profitability = DefaultProfitabilityEngine(settings)
        self._scan_rejections = 0
        self._opportunities_emitted = 0

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
        out: list[TradeOpportunity] = []
        for snap in snapshots:
            rate = snap.funding_rate
            if rate is None:
                continue
            funding_bps = abs(rate) * Decimal("10000")
            if funding_bps < self._min_funding_bps:
                self._scan_rejections += 1
                continue
            if snap.bid <= 0 or snap.ask <= 0:
                continue
            qty = self._size(snap, equity)
            if qty <= 0:
                continue
            side = OpportunitySide.SHORT if rate > 0 else OpportunitySide.LONG
            entry = snap.ask if side == OpportunitySide.LONG else snap.bid
            exit_px = entry
            opp = TradeOpportunity(
                strategy_name=self.name,
                symbol=snap.symbol,
                side=side,
                quantity=qty,
                entry_price=entry,
                expected_exit_price=exit_px,
                confidence=min(0.9, float(funding_bps / Decimal("20"))),
                rationale=f"Funding {funding_bps:.2f} bps clears min threshold",
                market=snap,
                entry_fee_role=FeeRole.MAKER,
                exit_fee_role=FeeRole.MAKER,
                funding_periods=Decimal("1"),
                metadata={
                    "asset_class": "crypto_perp",
                    "funding_rate": str(rate),
                    "funding_bps": str(funding_bps),
                    "buy_exchange": snap.exchange or "",
                },
            )
            est = await self._profitability.evaluate(opp)
            if not est.trade_allowed:
                self._scan_rejections += 1
                continue
            self._opportunities_emitted += 1
            out.append(opp)
        return out

    def _size(self, snap: MarketSnapshot, equity: Decimal | None) -> Decimal:
        cap = Decimal(str(getattr(self._settings, "arbitrage_max_quantity", 1)))
        if equity and equity > 0:
            pct = Decimal(str(getattr(self._settings, "arbitrage_position_pct", 5) or 5))
            ref = snap.ask if snap.ask > 0 else snap.mid
            if ref > 0 and pct > 0:
                cap = min(cap, (equity * pct / Decimal("100")) / ref)
        return cap

    def scan_stats(self) -> dict[str, object]:
        return {
            "pairs_evaluated": self._opportunities_emitted + self._scan_rejections,
            "scan_rejections": self._scan_rejections,
            "opportunities_emitted": self._opportunities_emitted,
            "reject_counts": {},
        }

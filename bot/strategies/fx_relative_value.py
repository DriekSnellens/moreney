"""FX relative-value strategy (stub feed / derived quotes)."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.strategies.base import BaseStrategy

_ZERO = Decimal("0")


class FxRelativeValueStrategy(BaseStrategy):
    """Mean-reversion on FX pairs when z-score vs rolling mean exceeds threshold."""

    name = "fx_relative_value"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = bool(getattr(settings, "global_fx_enabled", False))
        self._profitability = DefaultProfitabilityEngine(settings)
        self._means: dict[str, Decimal] = {}
        self._scan_rejections = 0
        self._opportunities_emitted = 0
        self._z_threshold = Decimal(str(getattr(settings, "global_fx_z_threshold", 1.5) or 1.5))

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
        fx_snaps = [
            s
            for s in snapshots
            if (s.metadata or {}).get("asset_class") == "fx" or s.symbol.endswith("USD")
        ]
        out: list[TradeOpportunity] = []
        for snap in fx_snaps:
            if snap.bid <= 0 or snap.ask <= 0:
                continue
            mid = snap.mid
            sym = snap.symbol.upper()
            prev = self._means.get(sym)
            self._means[sym] = mid if prev is None else (prev + mid) / Decimal("2")
            if prev is None or prev <= 0:
                continue
            z = (mid - prev) / prev * Decimal("100")
            if abs(z) < self._z_threshold:
                self._scan_rejections += 1
                continue
            side = OpportunitySide.SELL if z > 0 else OpportunitySide.BUY
            qty = self._size(equity, mid)
            opp = TradeOpportunity(
                strategy_name=self.name,
                symbol=sym,
                side=side,
                quantity=qty,
                entry_price=mid,
                expected_exit_price=prev,
                confidence=min(0.85, float(abs(z) / Decimal("5"))),
                rationale=f"FX z-score {z:.3f} vs mean",
                market=snap,
                entry_fee_role=FeeRole.TAKER,
                exit_fee_role=FeeRole.TAKER,
                funding_periods=_ZERO,
                metadata={
                    "asset_class": "fx",
                    "z_score": str(z),
                    "buy_exchange": snap.exchange or "fx_stub",
                },
            )
            est = await self._profitability.evaluate(opp)
            if not est.trade_allowed:
                self._scan_rejections += 1
                continue
            self._opportunities_emitted += 1
            out.append(opp)
        return out

    def _size(self, equity: Decimal | None, price: Decimal) -> Decimal:
        cap = Decimal("1000")
        if equity and equity > 0 and price > 0:
            cap = min(cap, equity * Decimal("0.05") / price)
        return max(Decimal("0.01"), cap)

    def scan_stats(self) -> dict[str, object]:
        return {
            "pairs_evaluated": self._opportunities_emitted + self._scan_rejections,
            "scan_rejections": self._scan_rejections,
            "opportunities_emitted": self._opportunities_emitted,
            "reject_counts": {},
        }

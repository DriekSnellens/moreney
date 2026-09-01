"""Market regime detection from observable market features."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from bot.core.enums import MarketRegime
from bot.core.models import MarketSnapshot

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class RegimeDetector:
    """Lightweight regime layer from spread, volatility proxy, and momentum."""

    def __init__(self, *, vol_window: int = 20, momentum_window: int = 10) -> None:
        self._mids: dict[str, deque[Decimal]] = {}
        self._vol_window = vol_window
        self._momentum_window = momentum_window

    def update(self, snapshots: list[MarketSnapshot]) -> dict[str, MarketRegime]:
        out: dict[str, MarketRegime] = {}
        for snap in snapshots:
            sym = snap.symbol.upper()
            mid = snap.mid
            if mid <= 0:
                continue
            buf = self._mids.setdefault(sym, deque(maxlen=self._vol_window))
            buf.append(mid)
            out[sym] = self._classify(sym, snap, buf)
        return out

    def global_regime(self, per_symbol: dict[str, MarketRegime]) -> MarketRegime:
        if not per_symbol:
            return MarketRegime.NORMAL
        counts: dict[MarketRegime, int] = {}
        for r in per_symbol.values():
            counts[r] = counts.get(r, 0) + 1
        return max(counts, key=counts.get)

    def strategy_weight(self, strategy: str, regime: MarketRegime) -> Decimal:
        """Return multiplier for strategy in current regime (0 = disabled)."""
        weights: dict[str, dict[MarketRegime, Decimal]] = {
            "maker_inventory": {
                MarketRegime.RANGE_BOUND: Decimal("1.2"),
                MarketRegime.LOW_VOLATILITY: Decimal("0.8"),
                MarketRegime.HIGH_VOLATILITY: Decimal("1.3"),
                MarketRegime.MOMENTUM: Decimal("1.2"),
                MarketRegime.LIQUIDITY_STRESSED: Decimal("0.3"),
                MarketRegime.NORMAL: Decimal("1.0"),
            },
            "triangle_bridge": {
                MarketRegime.RANGE_BOUND: Decimal("1.0"),
                MarketRegime.HIGH_VOLATILITY: Decimal("0.8"),
                MarketRegime.NORMAL: Decimal("1.0"),
            },
            "cross_exchange_arbitrage": {
                MarketRegime.HIGH_VOLATILITY: Decimal("1.2"),
                MarketRegime.LIQUIDITY_STRESSED: Decimal("0.5"),
                MarketRegime.NORMAL: Decimal("1.0"),
            },
            "funding_basis": {
                MarketRegime.HIGH_VOLATILITY: Decimal("1.3"),
                MarketRegime.RISK_OFF: Decimal("1.1"),
                MarketRegime.NORMAL: Decimal("1.0"),
            },
            "fx_relative_value": {
                MarketRegime.MEAN_REVERTING: Decimal("1.2"),
                MarketRegime.RANGE_BOUND: Decimal("1.1"),
                MarketRegime.NORMAL: Decimal("1.0"),
            },
            "equity_mean_reversion": {
                MarketRegime.MEAN_REVERTING: Decimal("1.3"),
                MarketRegime.RANGE_BOUND: Decimal("1.2"),
                MarketRegime.MOMENTUM: Decimal("0.4"),
                MarketRegime.NORMAL: Decimal("1.0"),
            },
        }
        table = weights.get(strategy, {})
        return table.get(regime, Decimal("1.0"))

    def _classify(
        self,
        symbol: str,
        snap: MarketSnapshot,
        mids: deque[Decimal],
    ) -> MarketRegime:
        if snap.bid <= 0 or snap.ask <= 0:
            return MarketRegime.NORMAL
        spread_bps = (snap.spread / snap.mid) * _HUNDRED
        if spread_bps > Decimal("50"):
            return MarketRegime.LIQUIDITY_STRESSED
        if len(mids) < 5:
            return MarketRegime.NORMAL

        returns = []
        prev = None
        for m in mids:
            if prev is not None and prev > 0:
                returns.append(abs((m - prev) / prev))
            prev = m
        if not returns:
            return MarketRegime.NORMAL
        vol = sum(returns, _ZERO) / Decimal(len(returns))
        if vol > Decimal("0.002"):
            return MarketRegime.HIGH_VOLATILITY
        if vol < Decimal("0.0002"):
            return MarketRegime.LOW_VOLATILITY

        first = mids[0]
        last = mids[-1]
        if first > 0:
            drift = (last - first) / first
            if abs(drift) > Decimal("0.003"):
                return MarketRegime.MOMENTUM if drift > 0 else MarketRegime.RISK_OFF
            if abs(drift) < Decimal("0.0005"):
                return MarketRegime.RANGE_BOUND
        return MarketRegime.NORMAL

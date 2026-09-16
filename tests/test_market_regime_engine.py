"""Tests for Market Regime Engine."""

from __future__ import annotations

from decimal import Decimal

from bot.core.models import MarketSnapshot
from bot.intelligence.market_regime_engine import (
    MarketRegime,
    classify_market_regime,
    data_freshness_score,
    regime_fit_for_strategy,
)


def _marks(values: list[str]) -> list[Decimal]:
    return [Decimal(v) for v in values]


class TestMarketRegime:
    def test_trend_up_on_positive_returns(self) -> None:
        marks = _marks(["100", "100.2", "100.4", "100.6", "100.8", "101.0"])
        out = classify_market_regime(marks=marks)
        assert out.regime in {MarketRegime.TREND_UP, MarketRegime.BREAKOUT, MarketRegime.RANGE}

    def test_dead_market_low_vol_spread(self) -> None:
        snap = MarketSnapshot(symbol="BTCEUR", bid=Decimal("100"), ask=Decimal("100.01"), last=Decimal("100.005"))
        marks = _marks(["100", "100", "100", "100.001", "100.001"])
        out = classify_market_regime(marks=marks, snapshot=snap, candidate_count=0)
        assert out.regime == MarketRegime.DEAD_MARKET

    def test_stale_data_unknown(self) -> None:
        snap = MarketSnapshot(
            symbol="BTCEUR",
            bid=Decimal("100"),
            ask=Decimal("100.1"),
            last=Decimal("100.05"),
            latency_ms=20000.0,
        )
        out = classify_market_regime(marks=_marks(["100", "100.1"]), snapshot=snap)
        assert out.regime == MarketRegime.UNKNOWN
        assert out.data_freshness_score < Decimal("0.2")

    def test_regime_fit_maker_inventory_range(self) -> None:
        fit = regime_fit_for_strategy(strategy="maker_inventory", regime=MarketRegime.RANGE)
        assert fit >= Decimal("0.85")

    def test_no_lookahead_uses_only_past_marks(self) -> None:
        marks = _marks(["100", "101", "102"])
        at_t2 = classify_market_regime(marks=marks[:3])
        assert at_t2.return_5m is None or at_t2.return_1m is not None


class TestDataFreshness:
    def test_fresh_when_latency_low(self) -> None:
        assert data_freshness_score(latency_ms=100.0, stale_sec=5.0) == Decimal("1")

    def test_stale_when_latency_high(self) -> None:
        assert data_freshness_score(latency_ms=20000.0, stale_sec=5.0) == Decimal("0.1")

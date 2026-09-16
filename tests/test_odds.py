"""Highest-odds 24h model: alt-beta inventory lands in the 2–5% band."""

from decimal import Decimal

from bot.core.enums import MarketRegime
from bot.paper.odds import (
    DEFAULT_INVENTORY_FRAC,
    TARGET_HIGH,
    TARGET_LOW,
    TYPICAL_ALT_UP_DAY,
    equity_move,
    hits_24h_band,
    snapshot,
)
from bot.regime.detector import RegimeDetector


def test_spread_only_cannot_hit_2pct_without_huge_turnover() -> None:
    # 8 bps net × 1× turnover = 0.08% — outside the 2–5% band.
    assert not hits_24h_band(Decimal("0.0008"))


def test_alt_beta_inventory_lands_in_2_to_5_pct() -> None:
    move = equity_move(DEFAULT_INVENTORY_FRAC, TYPICAL_ALT_UP_DAY)
    assert hits_24h_band(move)
    assert TARGET_LOW <= move <= TARGET_HIGH


def test_too_little_inventory_misses_the_band() -> None:
    assert not hits_24h_band(equity_move(Decimal("0.20"), TYPICAL_ALT_UP_DAY))


def test_snapshot_25k_in_band() -> None:
    data = snapshot(Decimal("25000"), inventory_pct=Decimal("75"))
    assert data["in_band"] is True
    euro = Decimal(str(data["euro_on_capital"]))
    assert euro >= Decimal("500")  # 2% of 25k
    assert euro <= Decimal("1250")  # 5% of 25k


def test_maker_leans_into_high_vol_and_momentum() -> None:
    det = RegimeDetector()
    assert det.strategy_weight("maker_inventory", MarketRegime.HIGH_VOLATILITY) >= Decimal(
        "1.0"
    )
    assert det.strategy_weight("maker_inventory", MarketRegime.MOMENTUM) >= Decimal("1.0")

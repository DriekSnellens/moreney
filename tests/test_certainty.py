"""High-certainty income: €300/day is a capital floor, not a trading target."""

from decimal import Decimal

from bot.paper.certainty import (
    HIGH_CERTAINTY_APY,
    STAKE_HIGH_EUR,
    STAKE_LOW_EUR,
    TARGET_DAILY_EUR,
    can_hit_target_with_high_certainty,
    daily_income,
    required_capital,
    snapshot,
)


def test_ten_to_twenty_five_k_cannot_pay_300_with_high_certainty() -> None:
    for stake in (STAKE_LOW_EUR, Decimal("15000"), STAKE_HIGH_EUR):
        assert daily_income(stake) < TARGET_DAILY_EUR
        assert not can_hit_target_with_high_certainty(stake)


def test_required_capital_for_300_is_millions() -> None:
    need = required_capital(TARGET_DAILY_EUR)
    assert need == TARGET_DAILY_EUR * Decimal("365") / HIGH_CERTAINTY_APY
    assert need >= Decimal("1000000")
    # 3% APY → €3.65M; 25k is ~146× too small.
    assert need / STAKE_HIGH_EUR > Decimal("100")


def test_snapshot_is_honest_on_25k() -> None:
    data = snapshot(STAKE_HIGH_EUR)
    assert data["target_reachable"] is False
    certain = Decimal(str(data["certain_daily_eur"]))
    assert Decimal("1") < certain < Decimal("10")
    assert Decimal(str(data["required_capital_eur"])) > Decimal("1000000")
    assert "hoge zekerheid" in str(data["note"]).lower()

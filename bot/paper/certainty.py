"""High-certainty daily income from capital — not a trading edge.

€300/day with very high confidence is a *capital* problem. Insured deposits,
short government bills and money-market funds pay a low single-digit APY.
That band is the only return this codebase will call "high certainty".

On €10k–€25k that is a few euros per day, not €300. Reaching €300/day at
3% APY needs about €3.65M. No crypto market-making, arb, or paper fill
model on this desk can be labeled high-certainty at the €300 target.
"""

from __future__ import annotations

from decimal import Decimal

TARGET_DAILY_EUR = Decimal("300")
DAYS_PER_YEAR = Decimal("365")
# Conservative cash / T-bill / insured-deposit band (order of magnitude).
HIGH_CERTAINTY_APY = Decimal("0.03")
HIGH_CERTAINTY_APY_LOW = Decimal("0.02")
HIGH_CERTAINTY_APY_HIGH = Decimal("0.04")
STAKE_LOW_EUR = Decimal("10000")
STAKE_HIGH_EUR = Decimal("25000")


def daily_income(capital_eur: Decimal, *, apy: Decimal = HIGH_CERTAINTY_APY) -> Decimal:
    """Certain-band daily income: capital × APY / 365."""
    if capital_eur <= 0 or apy <= 0:
        return Decimal("0")
    return capital_eur * apy / DAYS_PER_YEAR


def required_capital(
    daily_eur: Decimal = TARGET_DAILY_EUR, *, apy: Decimal = HIGH_CERTAINTY_APY
) -> Decimal:
    """Capital needed so ``daily_eur`` is the high-certainty coupon."""
    if daily_eur <= 0 or apy <= 0:
        return Decimal("0")
    return daily_eur * DAYS_PER_YEAR / apy


def can_hit_target_with_high_certainty(
    capital_eur: Decimal,
    *,
    daily_eur: Decimal = TARGET_DAILY_EUR,
    apy: Decimal = HIGH_CERTAINTY_APY,
) -> bool:
    return daily_income(capital_eur, apy=apy) >= daily_eur


def snapshot(
    capital_eur: Decimal,
    *,
    daily_target: Decimal = TARGET_DAILY_EUR,
) -> dict[str, object]:
    """Dashboard payload: what this stake can and cannot promise."""
    capital = Decimal(str(capital_eur or 0))
    certain = daily_income(capital)
    certain_low = daily_income(capital, apy=HIGH_CERTAINTY_APY_LOW)
    certain_high = daily_income(capital, apy=HIGH_CERTAINTY_APY_HIGH)
    need = required_capital(daily_target)
    reachable = can_hit_target_with_high_certainty(capital, daily_eur=daily_target)
    note = (
        f"€{daily_target:.0f}/dag met hoge zekerheid vraagt ≈ €{need:,.0f} "
        f"tegen {HIGH_CERTAINTY_APY * 100:.0f}% APY (cash/T-bill-band). "
        f"Op €{capital:,.0f} is dat ≈ €{certain:.2f}/dag "
        f"(band €{certain_low:.2f}–€{certain_high:.2f}). "
        "Trading-alpha op deze inleg is geen hoge zekerheid."
    )
    return {
        "apy": str(HIGH_CERTAINTY_APY),
        "apy_band": [str(HIGH_CERTAINTY_APY_LOW), str(HIGH_CERTAINTY_APY_HIGH)],
        "capital_eur": format(capital, "f"),
        "certain_daily_eur": format(certain, "f"),
        "certain_daily_eur_low": format(certain_low, "f"),
        "certain_daily_eur_high": format(certain_high, "f"),
        "target_daily_eur": format(daily_target, "f"),
        "required_capital_eur": format(need, "f"),
        "target_reachable": reachable,
        "note": note,
    }

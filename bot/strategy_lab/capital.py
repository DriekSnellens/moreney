"""Research capital sleeves — isolated budgets for fair comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from bot.strategy_lab import STRATEGY_IDS

_ZERO = Decimal("0")


DEFAULT_SLEEVE_EUR: dict[str, float] = {
    "maker_inventory": 5000.0,
    "executable_cross_venue_arb": 5000.0,
    "lead_lag": 5000.0,
    "order_book_imbalance": 4000.0,
    "funding_basis": 3000.0,
    "control_no_trade": 1000.0,
    "control_reserve": 2000.0,
}


@dataclass
class CapitalSleeve:
    strategy_id: str
    budget_eur: Decimal
    used_eur: Decimal = _ZERO
    peak_used_eur: Decimal = _ZERO
    locks: list[tuple[str, Decimal, float]] = field(default_factory=list)

    @property
    def free_eur(self) -> Decimal:
        return self.budget_eur - self.used_eur

    def try_allocate(self, amount: Decimal, *, lock_ms: float, key: str) -> bool:
        if amount <= 0:
            return True
        if amount > self.free_eur:
            return False
        self.used_eur += amount
        self.peak_used_eur = max(self.peak_used_eur, self.used_eur)
        self.locks.append((key, amount, lock_ms))
        return True

    def release(self, key: str) -> None:
        remaining: list[tuple[str, Decimal, float]] = []
        for k, amt, ms in self.locks:
            if k == key:
                self.used_eur = max(_ZERO, self.used_eur - amt)
            else:
                remaining.append((k, amt, ms))
        self.locks = remaining

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "budget_eur": str(self.budget_eur),
            "used_eur": str(self.used_eur),
            "peak_used_eur": str(self.peak_used_eur),
            "free_eur": str(self.free_eur),
        }


@dataclass
class CapitalLedger:
    """ISOLATED sleeves (default) or COMMON_CAPITAL for later portfolio sims."""

    mode: str = "ISOLATED"  # ISOLATED | COMMON_CAPITAL
    total_eur: Decimal = Decimal("25000")
    sleeves: dict[str, CapitalSleeve] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        *,
        total_eur: float = 25_000.0,
        mode: str = "ISOLATED",
        sleeve_map: Mapping[str, float] | None = None,
    ) -> "CapitalLedger":
        sleeves_cfg = dict(sleeve_map or DEFAULT_SLEEVE_EUR)
        sleeves = {
            sid: CapitalSleeve(strategy_id=sid, budget_eur=Decimal(str(amt)))
            for sid, amt in sleeves_cfg.items()
        }
        # Ensure all known strategies exist
        for sid in STRATEGY_IDS:
            sleeves.setdefault(
                sid, CapitalSleeve(strategy_id=sid, budget_eur=Decimal("1000"))
            )
        return cls(
            mode=mode,
            total_eur=Decimal(str(total_eur)),
            sleeves=sleeves,
        )

    def sleeve(self, strategy_id: str) -> CapitalSleeve:
        if strategy_id not in self.sleeves:
            self.sleeves[strategy_id] = CapitalSleeve(
                strategy_id=strategy_id, budget_eur=Decimal("1000")
            )
        return self.sleeves[strategy_id]

    def budget_for(self, strategy_id: str) -> Decimal:
        if self.mode == "COMMON_CAPITAL":
            return self.total_eur
        return self.sleeve(strategy_id).budget_eur

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total_eur": str(self.total_eur),
            "sleeves": {k: v.as_dict() for k, v in sorted(self.sleeves.items())},
        }


def net_eur_per_capital_second(
    net_eur: Decimal,
    capital_eur: Decimal,
    lock_ms: float,
) -> Decimal:
    if capital_eur <= 0:
        return _ZERO
    lock_s = max(Decimal(str(lock_ms)) / Decimal("1000"), Decimal("0.001"))
    return net_eur / (capital_eur * lock_s)


def net_bps_per_capital_second(
    net_eur: Decimal,
    capital_eur: Decimal,
    lock_ms: float,
) -> Decimal:
    if capital_eur <= 0:
        return _ZERO
    vel = net_eur_per_capital_second(net_eur, capital_eur, lock_ms)
    return vel / capital_eur * Decimal("10000")

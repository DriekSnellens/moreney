"""One-knob neighbors of the armed residual pack, plus two small crosses.

The pack stays the control: 10-day excess, skip one day, floor 3.5%,
SMA50, a 7-day clock, near-high weight 0.25, trail 10%, lock +12%/+6%.
Every other cell changes a time or a percentage that was fixed before
this run. The tape is daily, so a time is a number of days or the
weekday of the clock, not an hour.

Times: lookback 7, 10, 14, 20 and 28 days. Skip 0, 1 and 2. Clock 3, 5,
7, 10 and 14 days. SMA 20, 50 and 100. Weekday Monday through Sunday.

Percentages: excess floor 0, 2%, 3.5%, 5% and 8%. Trail 8, 10, 12, 15
and 20%. Lock arm/floor none, 8/4, 12/6, 16/8 and 20/10. Near-high
weight 0, 0.25 and 0.50.

Two crosses, other knobs left on the pack: lookback against the clock,
and trail against the lock. A cell beats the pack only when it ends
higher, is positive in 2024, 2025 and 2026, has at least 30 round trips,
and its drawdown is at most 15 percentage points worse. Nothing is armed.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.research.clip_exit_lab.policies import ExitPolicy
from bot.research.factor_scoreboard.engine import LIVE_KNOBS

LOOKBACKS = (7, 10, 14, 20, 28)
SKIPS = (0, 1, 2)
CLOCKS = (3, 5, 7, 10, 14)
SMAS = (20, 50, 100)
FLOORS = (0.0, 0.02, 0.035, 0.05, 0.08)
TRAILS = (0.08, 0.10, 0.12, 0.15, 0.20)
LOCKS = ((0.0, 0.0), (0.08, 0.04), (0.12, 0.06), (0.16, 0.08), (0.20, 0.10))
NEARS = (0.0, 0.25, 0.50)
WEEKDAYS = (
    ("maandag", 0),
    ("dinsdag", 1),
    ("woensdag", 2),
    ("donderdag", 3),
    ("vrijdag", 4),
    ("zaterdag", 5),
    ("zondag", 6),
)
MIN_TRADES = 30
DD_SLACK = 0.15
YEARS = ("2024", "2025", "2026")


@dataclass(frozen=True)
class Spec:
    name: str
    family: str
    lookback_days: int = 10
    skip_days: int = 1
    rebalance_days: int = 7
    rebalance_weekday: int | None = None
    sma_n: int = 50
    excess_floor: float = 0.035
    trail: float = 0.10
    lock_arm: float = 0.12
    lock_floor: float = 0.06
    near_high_weight: float = 0.25

    def key(self) -> tuple:
        return (
            self.lookback_days,
            self.skip_days,
            self.rebalance_days,
            self.rebalance_weekday,
            self.sma_n,
            self.excess_floor,
            self.trail,
            self.lock_arm,
            self.lock_floor,
            self.near_high_weight,
        )


def live_spec() -> Spec:
    return Spec("live pakket", "live")


def knobs(spec: Spec) -> dict:
    """Armed knobs with this cell's time and percentage written over them."""
    base = dict(LIVE_KNOBS)
    base.pop("policy", None)
    base.update(
        {
            "lookback_days": spec.lookback_days,
            "skip_days": spec.skip_days,
            "rebalance_days": spec.rebalance_days,
            "rebalance_weekday": spec.rebalance_weekday,
            "sma_n": spec.sma_n,
            "excess_floor": spec.excess_floor,
            "near_high_weight": spec.near_high_weight,
            "policy": ExitPolicy(
                name=spec.name,
                alt_trail_pct=spec.trail,
                alt_lock_arm_pct=spec.lock_arm,
                alt_lock_floor_pct=spec.lock_floor,
            ),
        }
    )
    return base


def _weight(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _lock_name(arm: float, floor: float) -> str:
    if arm == 0.0:
        return "geen lock"
    return f"lock {_pct(arm)}/{_pct(floor)}"


def _pct(value: float) -> str:
    text = f"{value * 100:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return text


def grid() -> list[Spec]:
    specs: list[Spec] = [live_spec()]
    seen = {specs[0].key()}

    def add(spec: Spec) -> None:
        if spec.key() in seen:
            return
        seen.add(spec.key())
        specs.append(spec)

    for n in LOOKBACKS:
        add(Spec(f"terugblik {n} dagen", "terugblik", lookback_days=n))
    for n in SKIPS:
        add(Spec(f"overslaan {n}", "overslaan", skip_days=n))
    for n in CLOCKS:
        add(Spec(f"klok {n} dagen", "klok", rebalance_days=n))
    for n in SMAS:
        add(Spec(f"gemiddelde {n}", "gemiddelde", sma_n=n))
    for floor in FLOORS:
        add(Spec(f"drempel {_pct(floor)}%", "drempel", excess_floor=floor))
    for trail in TRAILS:
        add(Spec(f"trail {_pct(trail)}%", "trail", trail=trail))
    for arm, floor in LOCKS:
        add(Spec(_lock_name(arm, floor), "lock", lock_arm=arm, lock_floor=floor))
    for weight in NEARS:
        add(Spec(f"near-high {_weight(weight)}", "near", near_high_weight=weight))
    for lookback in LOOKBACKS:
        for clock in CLOCKS:
            add(
                Spec(
                    f"terugblik {lookback}, klok {clock}",
                    "kruis_tijd",
                    lookback_days=lookback,
                    rebalance_days=clock,
                )
            )
    for trail in TRAILS:
        for arm, floor in LOCKS:
            lock = _lock_name(arm, floor)
            add(
                Spec(
                    f"trail {_pct(trail)}%, {lock}",
                    "kruis_uitstap",
                    trail=trail,
                    lock_arm=arm,
                    lock_floor=floor,
                )
            )
    for label, weekday in WEEKDAYS:
        add(Spec(f"klok op {label}", "weekdag", rebalance_weekday=weekday))
    return specs


@dataclass
class Row:
    name: str
    family: str
    end: float
    pnl: float
    dd: float
    trades: int
    year: dict[str, float]
    beats: bool = False


def beats(row: Row, control: Row) -> bool:
    if row.name == control.name or row.trades < MIN_TRADES or row.end <= control.end:
        return False
    if row.dd > control.dd + DD_SLACK:
        return False
    return all(row.year.get(year, 0.0) > 0.0 for year in YEARS)

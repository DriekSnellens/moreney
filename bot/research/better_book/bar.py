"""The bar for "much better than the live residual pack".

Locked before the run. A later edit that loosens these numbers to fit a
result is not a find.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Fresh 2026 book and the compounded full path, versus the live pack
# on the same tape. IS is the compounded book through 2025-12-31.
FRESH_MULT = 1.5
FULL_MULT = 1.25
DD_SLACK = 0.05
# Share of the full-path euro delta that one new round trip may explain.
ONE_TRADE_SHARE = 0.60

SCALE_STARTERS = (0.40, 0.50, 0.60)
SCALE_ARMS = (0.08, 0.12, 0.20)
RUNNER_ARMS = (0.30, 0.40, 0.60)
RUNNER_TRAILS = (0.15, 0.20, 0.25)
# Monday and Friday. The lock is not restricted to these days.
TRAIL_WEEKDAYS = (0, 4)
# Consecutive closes through the same 10% trail. Chosen after scale-in,
# the runner trail, and the weekday gate missed: the level stays 10%,
# and one close is no longer enough to sell the whole lot. Not a fit
# to a winning cell.
TRAIL_CONFIRM = (2, 3)
TRAIL_HALF_FRAC = 0.50
# The close rule sells the whole lot on the first close through 10%.
# Waiting for a second close gave the ride back. These cells sell when
# the low trades the level known before the bar, which is earlier than
# the close, not a wider trail and not a second close.
# Rank only. Size, clock, trail and lock stay the live ones.
# Beta windows: the live excess assumes every alt has BTC beta 1.
# IR windows: the same live score, divided by residual volatility.
BETA_WINDOWS = (20, 40, 60)
IR_WINDOWS = (10, 20, 40)


def _pnl(row: Mapping[str, Any], window: str) -> float:
    return float(row[window]["pnl_eur"])


def _dd(row: Mapping[str, Any]) -> float:
    return float(row["full"]["max_dd_pct"])


def beats_both(cell: Mapping[str, Any], base: Mapping[str, Any]) -> bool:
    """Strictly ahead of the live pack in-sample and on a fresh 2026 book."""
    return _pnl(cell, "is") > _pnl(base, "is") and _pnl(cell, "oos") > _pnl(base, "oos")


def hits_margin(cell: Mapping[str, Any], base: Mapping[str, Any]) -> bool:
    """The pre-registered size of "much better"."""
    fresh = _pnl(base, "oos")
    full = _pnl(base, "full")
    fresh_bar = fresh * FRESH_MULT if fresh > 0 else fresh + 1.0
    full_bar = full * FULL_MULT if full > 0 else full + 1.0
    return (
        _pnl(cell, "oos") >= fresh_bar
        and _pnl(cell, "full") >= full_bar
        and _pnl(cell, "is") >= _pnl(base, "is")
        and _dd(cell) <= _dd(base) + DD_SLACK
    )


@dataclass(frozen=True)
class FamilyVerdict:
    name: str
    region: bool
    margin_names: tuple[str, ...]
    cleared: tuple[str, ...]


def region_ok(cells: Sequence[Mapping[str, Any]], base: Mapping[str, Any]) -> bool:
    """At least half the cells, and at least two, beat the pack on both windows."""
    if len(cells) < 2:
        return False
    need = max(2, (len(cells) + 1) // 2)
    return sum(1 for c in cells if beats_both(c, base)) >= need


def one_trade_ok(delta: float, largest_new_trip: float) -> bool:
    """A single new round trip may not be most of the full-path improvement."""
    if delta <= 0:
        return False
    return largest_new_trip < ONE_TRADE_SHARE * delta


def judge_family(
    name: str,
    cells: Sequence[Mapping[str, Any]],
    base: Mapping[str, Any],
    *,
    new_trip: Mapping[str, float] | None = None,
) -> FamilyVerdict:
    """``new_trip`` maps a cell name to the largest round trip the baseline did not book."""
    margin = tuple(str(c["name"]) for c in cells if hits_margin(c, base))
    region = region_ok(cells, base)
    cleared: list[str] = []
    if region:
        for cell_name in margin:
            cell = next(c for c in cells if c["name"] == cell_name)
            delta = _pnl(cell, "full") - _pnl(base, "full")
            trip = 0.0 if new_trip is None else float(new_trip.get(cell_name, 0.0))
            if one_trade_ok(delta, trip):
                cleared.append(cell_name)
    return FamilyVerdict(name=name, region=region, margin_names=margin, cleared=tuple(cleared))

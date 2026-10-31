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
# After a banked exit the live clock sits in cash for seven days.
# These modes open the next leader early only when the exit itself was
# a lock, or the signal close was still above the entry. A losing exit
# keeps the full week. Not a shorter clock on every sell.
REARM_MODES = ("lock", "green", "green_next")
# New rides only while BTC's own lookback return clears the floor.
# A name already held is left to the trail. 0, 2 and 5 percent.
TIDE_FLOORS = (0.0, 0.02, 0.05)
# Buying the next leader sooner after a green exit lost. These cells
# wait longer than the live week, and only after a close that is still
# above the entry. A losing exit keeps the seven-day clock.
# 14, 21 and 28 are one, two and three extra weeks, not a fitted gap.
GREEN_PAUSES = (14, 21, 28)
# The live desk is a fixed list. These cells re-pick that list each day
# as the most-traded names on the whole venue, then run the same book.
# 60 days of history are required. 12, 16 and 24 are the width of the list.
VOLUME_TOPS = (12, 16, 24)
# The live book goes to cash when BTC closes under its SMA50, even if an
# alt still clears the residual floor and its own SMA50. These cells
# require a deeper break, or they drop that veto. The alt's own SMA stays.
# 5% and 10% are round distances. veto_off does not fire while price is positive.
BTC_VETO_GAPS = (
    ("veto_5", 0.05),
    ("veto_10", 0.10),
    ("veto_off", 1.0),
)
# The live book buys the highest score among names that clear the floor.
# These cells buy a cooler qualifier and do not fall back to the leader
# when that slot is empty. "floor" is the smallest raw excess still above it.
RANK_SLOTS = ("second", "third", "floor")
# A fixed hold cuts winners. These cells sell only while the close is still
# under the entry, after 2, 4 or 6 days. A green position keeps the live trail.
RED_CUT_DAYS = (2, 4, 6)
# The two-day red cut beat the fresh-2026 size bar and missed the compounded
# path. These cells keep that same 2, 4 and 6 day grid, and they sell only
# when the name is also no longer the residual leader. A red leader stays
# on the trail. The day count is not moved.
RED_LEADER_DAYS = (2, 4, 6)
# A buy queued at the close is cancelled when the next open gaps up more
# than this versus that close. 3, 5 and 8 percent. Sells still fill.
GAP_CAPS = (0.03, 0.05, 0.08)
# The two-day cut that also requires a lost lead is one cell and misses
# the size bar. These cells drop the day count. They sell when the held
# name is no longer the top qualifier, when that is true and the close is
# under the entry, or when its own excess is no longer above the floor.
# The trail and the lock still run. The clock after the sell is the live week.
LEAD_EXITS = ("rank", "rank_red", "floor")


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

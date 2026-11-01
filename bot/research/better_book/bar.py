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
# Dropping the BTC SMA veto, or waiting for a deep break, raised the
# drawdown and gave the path back. These cells leave that veto on for a
# new ride. They keep a name already held when BTC closes under the
# average: if the close is above the entry, if the name is still the
# residual leader, or both. A flat book stays flat.
KEEP_RIDES = ("green", "leader", "both")
# The live rank is a 10-day excess. These cells also require that name's
# excess over 20, 40 or 60 days to be positive. The rank among the names
# that agree stays the live score. If none agree, the book stays in cash.
AGREE_DAYS = (20, 40, 60)
# After a sell whose close is under the entry, that name is skipped for
# 14, 28 or 56 days. A different leader can still be bought on the clock.
# A sell that is still above the entry does not start the ban.
LOSS_BANS = (14, 28, 56)
# The live book buys the residual leader even when its own recent trading
# is quieter than the twenty days before. These cells keep a name only
# when the last twenty days of quote volume are at least 1.0, 1.5 or 2.0
# times the twenty days before that. The rank among the names that pass
# stays the live score.
VOL_MULTS = (1.0, 1.5, 2.0)
# The live book can buy a name that has already been above the floor for
# days. These cells buy only when the excess was still at or below the
# floor 1, 2 or 3 days earlier. The day count is the age of the cross,
# not a pause fitted to a losing trade.
CROSS_DAYS = (1, 2, 3)
# While the held name is still the residual leader, a close has to be this
# far under the peak before the trail sells. 15, 20 and 25 percent. A name
# that has lost the lead keeps the live 10% trail. The lock is unchanged.
LEAD_WIDE = (0.15, 0.20, 0.25)
# How many desk names clear the residual floor. The live book buys the
# leader whether one name qualifies or the whole desk does. max cells open
# a new ride only when the count is at most 2, 3 or 4. min cells open only
# when at least that many qualify. A name already held is left to the trail.
CROWD = (
    ("max", 2),
    ("max", 3),
    ("max", 4),
    ("min", 2),
    ("min", 3),
    ("min", 4),
)
# A buy is cancelled when the next open gaps down more than this versus
# the signal close. 2, 4 and 6 percent. Gap-up cancels at 3 percent and
# above changed no fill. Sells still fill.
GAP_DOWNS = (0.02, 0.04, 0.06)
# The live book rotates on the clock when a different name ranks first,
# even if the excess lead is a tenth of a percent. These cells keep the
# held name unless the challenger's excess is ahead by 2, 5 or 8 points.
# A fresh open from cash, and a flatten to cash, stay as they are.
ROTATE_GAPS = (0.02, 0.05, 0.08)
# Distance of the BTC close above its SMA50. max cells open a new ride
# only when that distance is at most 5, 10 or 20 percent. min cells open
# only when it is at least 2, 5 or 10 percent. A held name stays. A
# flatten to cash stays cash.
BTC_EXT = (
    ("max", 0.05),
    ("max", 0.10),
    ("max", 0.20),
    ("min", 0.02),
    ("min", 0.05),
    ("min", 0.10),
)
# Dropping a name whose own 10-day return is below 0, 3 or 6 percent.
# The rank among the names that pass stays the live score. If none pass,
# the book stays in cash.
OWN_FLOORS = (0.0, 0.03, 0.06)
# Among names that already clear the floor, rank by how much the excess
# changed over 3, 5 or 10 days. accel prefers a rising excess. decel
# prefers a fading one. Both directions are in the family. The floor,
# the SMA and the exit stay live.
ACCEL_WINDOWS = (3, 5, 10)
# One- to four-week formation. Published crypto momentum is strongest
# around three weeks and fades by eight. These cells keep the live score,
# the 3.5% floor, the SMA and the trail, and change only the excess
# window to 7, 14, 21 or 28 days. The one-day skip stays.
FORM_DAYS = (7, 14, 21, 28)
# Largest one-day return over 5, 10 or 20 days, among names that already
# clear the residual floor. max_high prefers the spike. max_low avoids it.
# The two sides are the published disagreement, locked before the run.
MAX_WINDOWS = (5, 10, 20)
# Quote volume inside the fixed desk, over 10, 20 or 40 days. liq_low
# buys the quieter qualifier. liq_high buys the louder one. The residual
# floor still binds. The universe does not change.
LIQ_WINDOWS = (10, 20, 40)
# Sit out a new ride while the book's own equity is under its 20, 40 or
# 60 day average. A name already held stays on the trail. A flat book
# rechecks every day. The window is the whole grid, locked before the run.
EQUITY_SMA = (20, 40, 60)
# A qualifier has to have just printed a high, or it has to have stayed
# inside the recent high. 1, 2 or 3 days. Both sides. The rank among the
# names that pass stays the live score.
BREAK_DAYS = (1, 2, 3)
# A close this far under the entry sells, the same day. 5, 8 and 12
# percent. The 10% trail and the lock still run, so a winner is not cut
# for being old. The grid is locked before the run.
STOP_PCTS = (0.05, 0.08, 0.12)
# How many days BTC has closed above its SMA50. wait cells open a new
# ride only once that streak is at least 2, 4 or 8 days. fresh cells open
# only while the streak is still within that bound. A held name stays.
# A flatten to cash stays cash.
RECLAIM = (
    ("wait", 2),
    ("wait", 4),
    ("wait", 8),
    ("fresh", 2),
    ("fresh", 4),
    ("fresh", 8),
)


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

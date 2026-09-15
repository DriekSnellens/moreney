"""Pure trail + never-loss bag simulator (no exchange I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SimResult:
    realized_eur: float
    remaining_qty: float
    remaining_mtm_eur: float
    soft_partials: int
    hard_partials: int
    trail_exits: int
    be_blocks: int
    armed: bool
    final_mark: float
    cost: float


def break_even_price(cost: float, *, fee_rate: float, sell_buffer_bps: float) -> float:
    denom = 1.0 - max(0.0, fee_rate)
    if denom <= 0:
        return cost * 2.0
    be = cost / denom
    if sell_buffer_bps > 0:
        be *= 1.0 + sell_buffer_bps / 10_000.0
    return be


def simulate_bag(
    marks: list[float],
    *,
    qty: float,
    cost: float,
    soft_arm_pct: float,
    soft_partial_pct: float,
    soft_drawdown_pct: float,
    hard_arm_pct: float = 0.06,
    hard_drawdown_pct: float = 0.03,
    hard_partial_pct: float = 0.25,
    fee_rate: float = 0.0025,
    sell_buffer_bps: float = 10.0,
) -> SimResult:
    """Step marks through soft/hard trail with fee-aware never-loss sells."""
    remaining = float(qty)
    realized = 0.0
    soft_armed = False
    hard_armed = False
    soft_partial_done = False
    hard_partial_done = False
    peak = 0.0
    soft_partials = 0
    hard_partials = 0
    trail_exits = 0
    be_blocks = 0
    last_mark = marks[0] if marks else cost

    def _sell(frac: float, mark: float) -> bool:
        nonlocal remaining, realized, be_blocks
        if remaining <= 0 or frac <= 0:
            return False
        be = break_even_price(
            cost, fee_rate=fee_rate, sell_buffer_bps=sell_buffer_bps
        )
        if mark < be:
            be_blocks += 1
            return False
        sell_qty = remaining * min(1.0, frac)
        # Net proceeds after sell fee.
        net_px = mark * (1.0 - fee_rate)
        realized += sell_qty * (net_px - cost)
        remaining -= sell_qty
        return True

    for mark in marks:
        last_mark = mark
        if remaining <= 1e-12:
            break
        if cost <= 0 or mark <= 0:
            continue
        gain = (mark - cost) / cost

        if not soft_armed and gain >= soft_arm_pct:
            soft_armed = True
            peak = mark
            if soft_partial_pct > 0 and not soft_partial_done:
                if _sell(soft_partial_pct, mark):
                    soft_partial_done = True
                    soft_partials += 1

        if soft_armed and not hard_armed and gain >= hard_arm_pct:
            hard_armed = True
            if mark > peak:
                peak = mark
            if hard_partial_pct > 0 and not hard_partial_done:
                if _sell(hard_partial_pct, mark):
                    hard_partial_done = True
                    hard_partials += 1

        if not soft_armed:
            continue

        if mark > peak:
            peak = mark
        active_dd = hard_drawdown_pct if hard_armed else soft_drawdown_pct
        # Never-loss: do not fire trail exit below unit cost.
        if mark >= cost and peak > 0 and mark <= peak * (1.0 - active_dd):
            if _sell(1.0, mark):
                trail_exits += 1
                break
            # Blocked by fee BE — keep holding; do not clear soft_armed.

    remaining_mtm = remaining * (last_mark - cost)
    return SimResult(
        realized_eur=realized,
        remaining_qty=remaining,
        remaining_mtm_eur=remaining_mtm,
        soft_partials=soft_partials,
        hard_partials=hard_partials,
        trail_exits=trail_exits,
        be_blocks=be_blocks,
        armed=soft_armed,
        final_mark=last_mark,
        cost=cost,
    )


def score_result(result: SimResult) -> dict[str, float]:
    """Primary score = realized; secondary penalize leftover underwater MTM."""
    leftover_penalty = min(0.0, result.remaining_mtm_eur) * 0.25
    return {
        "realized_eur": result.realized_eur,
        "remaining_mtm_eur": result.remaining_mtm_eur,
        "score": result.realized_eur + leftover_penalty,
        "be_blocks": float(result.be_blocks),
        "trail_exits": float(result.trail_exits),
        "soft_partials": float(result.soft_partials),
    }

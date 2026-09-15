"""Cost stress against the canonical execution replay.

Every cell uses the same waterfall:
  stressed_net = gross - fees*fee_mult - slip*slip_mult - adverse*adv_mult
                 - funding - transfer - other_costs

Fill probability does not rescale aggregate replay net.
Break-even is extra EUR and bps of notional.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterator

from bot.research.accounting.protocol import (
    ADVERSE_MULTIPLIERS,
    FEE_MULTIPLIERS,
    NOTIONAL_EUR,
    REPLAY_VERSION,
    SLIPPAGE_MULTIPLIERS,
)
from bot.research.accounting.schema import EconomicWorld
from bot.research.accounting.waterfall import CanonicalEconomics

_BPS = Decimal("10000")


def iter_canonical_stress_grid() -> Iterator[dict[str, Decimal]]:
    for fee_m in FEE_MULTIPLIERS:
        for slip_m in SLIPPAGE_MULTIPLIERS:
            for adv_m in ADVERSE_MULTIPLIERS:
                yield {
                    "fee_multiplier": fee_m,
                    "slippage_multiplier": slip_m,
                    "adverse_multiplier": adv_m,
                }


def apply_canonical_cell(
    econ: CanonicalEconomics,
    *,
    fee_multiplier: Decimal,
    slippage_multiplier: Decimal,
    adverse_multiplier: Decimal,
) -> dict[str, Any]:
    gross = econ.gross.value
    fees = econ.fees.value * fee_multiplier
    slip = econ.slippage.value * slippage_multiplier
    adv = econ.adverse.value * adverse_multiplier
    net = (
        gross
        - fees
        - slip
        - adv
        - econ.funding.value
        - econ.transfer.value
        - econ.other_costs.value
    )
    n = econ.signals.value
    fills = econ.fills.value
    per_sig = (net / Decimal(n)) if n else None
    per_fill = (net / Decimal(fills)) if fills else None
    extra_cost = (fees - econ.fees.value) + (slip - econ.slippage.value) + (adv - econ.adverse.value)
    notional = NOTIONAL_EUR * Decimal(n)
    extra_bps = (extra_cost / notional * _BPS) if notional else None
    return {
        "fee_multiplier": str(fee_multiplier),
        "slippage_multiplier": str(slippage_multiplier),
        "adverse_multiplier": str(adverse_multiplier),
        "replay_net_eur": str(net),
        "replay_net_per_signal_eur": None if per_sig is None else str(per_sig),
        "replay_net_per_fill_eur": None if per_fill is None else str(per_fill),
        "extra_cost_eur": str(extra_cost),
        "notional_eur": str(notional),
        "extra_cost_bps_of_notional": None if extra_bps is None else str(extra_bps),
        "replay_version": REPLAY_VERSION,
        "world": EconomicWorld.EXECUTION_REPLAY.value,
        "fill_count_used_for_per_fill": fills,
        "fill_count_definition": econ.fills.metadata.to_dict(),
        "sign": "positive" if net > 0 else ("zero" if net == 0 else "negative"),
    }


def stress_canonical(econ: CanonicalEconomics) -> dict[str, Any]:
    cells = [
        apply_canonical_cell(
            econ,
            fee_multiplier=sc["fee_multiplier"],
            slippage_multiplier=sc["slippage_multiplier"],
            adverse_multiplier=sc["adverse_multiplier"],
        )
        for sc in iter_canonical_stress_grid()
    ]
    nets = [Decimal(c["replay_net_eur"]) for c in cells]
    order = sorted(range(len(cells)), key=lambda i: nets[i])
    n_pos = sum(1 for x in nets if x > 0)
    n_neg = sum(1 for x in nets if x < 0)
    n_zero = len(nets) - n_pos - n_neg
    mid = order[len(order) // 2]
    return {
        "n_combinations": len(cells),
        "n_positive": n_pos,
        "n_negative": n_neg,
        "n_zero": n_zero,
        "worst_cell": cells[order[0]],
        "median_cell": cells[mid],
        "best_cell": cells[order[-1]],
        "survives_all_cells": n_neg == 0 and n_zero == 0,
        "survives_worst_cell": nets[order[0]] > 0,
        "cells": cells,
        "world": EconomicWorld.EXECUTION_REPLAY.value,
        "replay_version": REPLAY_VERSION,
        "note": (
            "Each cell applies fee/slippage/adverse multipliers to the canonical "
            "waterfall sums. Fill probability is not mixed into replay_net_eur."
        ),
    }


def break_even_canonical(econ: CanonicalEconomics) -> dict[str, Any]:
    """Extra adverse EUR that zeros canonical replay net, holding other costs."""
    net = econ.replay_net.value
    n = econ.signals.value
    notional = NOTIONAL_EUR * Decimal(n)
    extra_cost_eur = net
    extra_bps = (extra_cost_eur / notional * _BPS) if notional else None
    extra_adverse_mult = None
    if econ.adverse.value != 0:
        extra_adverse_mult = Decimal("1") + (net / econ.adverse.value)
    return {
        "extra_cost_eur": str(extra_cost_eur),
        "notional_eur": str(notional),
        "extra_cost_bps_of_notional": None if extra_bps is None else str(extra_bps),
        "notional_basis": f"{NOTIONAL_EUR} EUR * SignalCount={n}",
        "numerator": "RealizedReplayNetEUR",
        "denominator": "CanonicalNotionalEUR * SignalCount",
        "break_even_adverse_multiplier": None if extra_adverse_mult is None else str(extra_adverse_mult),
        "world": EconomicWorld.EXECUTION_REPLAY.value,
        "replay_version": REPLAY_VERSION,
        "warning": (
            "Do not read extra_cost_bps_of_notional as bps of an unknown "
            "denominator, and do not confuse it with mean-edge NET/fill scale."
        ),
    }


def uses_canonical_replay(cell: dict[str, Any]) -> bool:
    return (
        cell.get("world") == EconomicWorld.EXECUTION_REPLAY.value
        and cell.get("replay_version") == REPLAY_VERSION
        and "replay_net_eur" in cell
    )

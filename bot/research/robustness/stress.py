"""Research-only sensitivity matrix and break-even frontier.

Does not change production cost assumptions.
"""

from __future__ import annotations

from typing import Any, Iterator

from bot.research.robustness.protocol import (
    ADVERSE_ADD_BPS,
    ADVERSE_EXTRA_BPS,
    FEE_MULTS,
    FILL_PROBS,
    LATENCY_ADD_MS,
    LATENCY_MS_TO_BPS,
    NOTIONAL_EUR,
    PARTIAL_RATIOS,
    REASONABLE_STRESS,
    SLIP_ADD_BPS,
)
from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.economics import round_trip_fee_rate


def _net(
    *,
    gross_frac: float,
    fee_rate: float,
    fee_mult: float,
    slip_bps: float,
    adverse_bps: float,
    latency_bps: float,
    fill_prob: float,
    partial: float,
) -> dict[str, float]:
    n = NOTIONAL_EUR * float(partial)
    gross = n * float(gross_frac)
    fees = n * float(fee_rate) * float(fee_mult)
    slip = n * float(slip_bps) / 10000.0
    adv = n * float(adverse_bps) / 10000.0
    lat = n * float(latency_bps) / 10000.0
    expected = gross - fees - slip - adv - lat
    extra = n * float(ADVERSE_EXTRA_BPS) / 10000.0
    exec_net = float(fill_prob) * (expected - extra)
    fills_if_one = float(fill_prob)
    net_per_fill = (expected - extra) if fills_if_one else expected
    return {
        "EXPECTED_NET": expected,
        "EXECUTION_NET": exec_net,
        "NET_per_fill": net_per_fill,
        "NET": exec_net,  # per-signal realized under fill/partial; scale by signals outside
    }


def iter_stress_grid() -> Iterator[dict[str, float]]:
    for fee_mult in FEE_MULTS:
        for slip_add in SLIP_ADD_BPS:
            for adv_add in ADVERSE_ADD_BPS:
                for fill in FILL_PROBS:
                    for lat_ms in LATENCY_ADD_MS:
                        for partial in PARTIAL_RATIOS:
                            yield {
                                "fee_mult": float(fee_mult),
                                "slip_add_bps": float(slip_add),
                                "adverse_add_bps": float(adv_add),
                                "fill_prob": float(fill),
                                "latency_add_ms": float(lat_ms),
                                "partial_ratio": float(partial),
                            }


def apply_scenario(
    *,
    gross_frac: float,
    venue: str,
    venue_exit: str | None,
    scenario: dict[str, float],
) -> dict[str, Any]:
    fee_rate = float(round_trip_fee_rate(venue, venue_exit))
    out = _net(
        gross_frac=gross_frac,
        fee_rate=fee_rate,
        fee_mult=float(scenario["fee_mult"]),
        slip_bps=SLIPPAGE_BPS_DEFAULT + float(scenario["slip_add_bps"]),
        adverse_bps=ADVERSE_BPS_DEFAULT + float(scenario["adverse_add_bps"]),
        latency_bps=LATENCY_PENALTY_BPS
        + float(scenario["latency_add_ms"]) * LATENCY_MS_TO_BPS,
        fill_prob=float(scenario["fill_prob"]),
        partial=float(scenario["partial_ratio"]),
    )
    sign = "positive" if out["EXECUTION_NET"] > 0 else (
        "zero" if out["EXECUTION_NET"] == 0 else "negative"
    )
    return {**scenario, **out, "sign": sign, "fee_rate": fee_rate}


def stress_matrix(
    *,
    expected_net: float,
    venue: str,
    venue_exit: str | None,
    signals: int = 1,
) -> dict[str, Any]:
    fee_rate = float(round_trip_fee_rate(venue, venue_exit))
    cost0 = (
        NOTIONAL_EUR * fee_rate
        + NOTIONAL_EUR * SLIPPAGE_BPS_DEFAULT / 10000.0
        + NOTIONAL_EUR * ADVERSE_BPS_DEFAULT / 10000.0
        + NOTIONAL_EUR * LATENCY_PENALTY_BPS / 10000.0
    )
    gross_frac = (float(expected_net) + cost0) / NOTIONAL_EUR
    cells: list[dict[str, Any]] = []
    n_pos = n_neg = n_zero = 0
    worst: dict[str, Any] | None = None
    best: dict[str, Any] | None = None
    for sc in iter_stress_grid():
        cell = apply_scenario(
            gross_frac=gross_frac, venue=venue, venue_exit=venue_exit, scenario=sc
        )
        cell["NET_window"] = float(cell["EXECUTION_NET"]) * max(int(signals), 0)
        cells.append(cell)
        if cell["sign"] == "positive":
            n_pos += 1
        elif cell["sign"] == "negative":
            n_neg += 1
        else:
            n_zero += 1
        if worst is None or cell["EXECUTION_NET"] < worst["EXECUTION_NET"]:
            worst = cell
        if best is None or cell["EXECUTION_NET"] > best["EXECUTION_NET"]:
            best = cell
    reasonable = apply_scenario(
        gross_frac=gross_frac,
        venue=venue,
        venue_exit=venue_exit,
        scenario=REASONABLE_STRESS,
    )
    reasonable["NET_window"] = float(reasonable["EXECUTION_NET"]) * max(int(signals), 0)
    return {
        "n_combinations": len(cells),
        "n_positive": n_pos,
        "n_negative": n_neg,
        "n_zero": n_zero,
        "worst": worst,
        "best": best,
        "reasonable_stress": reasonable,
        "survives_reasonable_stress": bool(reasonable["EXECUTION_NET"] > 0),
        "cells": cells,
        "gross_frac": gross_frac,
        "note": "Research-only overlay on the existing waterfall. Production costs unchanged.",
    }


def break_even_frontier(
    *,
    expected_net: float,
    venue: str,
    venue_exit: str | None,
) -> dict[str, Any]:
    """Min additional cost to drive EXPECTED_NET (per signal) to <= 0."""
    fee_rate = float(round_trip_fee_rate(venue, venue_exit))
    net = float(expected_net)
    be_adverse = (net / NOTIONAL_EUR) * 10000.0
    be_slip = be_adverse
    be_fee = (net / (NOTIONAL_EUR * fee_rate)) if fee_rate else None  # multiplier-1
    be_fee_bps = be_adverse
    be_latency_bps = be_adverse
    be_latency_ms = be_latency_bps / LATENCY_MS_TO_BPS if LATENCY_MS_TO_BPS else None
    # Fill deterioration: EXECUTION_NET = fill * (net - extra); extra is 4 bps * notional.
    extra = NOTIONAL_EUR * ADVERSE_EXTRA_BPS / 10000.0
    per = net - extra
    # Fill cannot make a positive per-fill edge negative except at fill=0, which is
    # non-participation. Report fill that zeros EXECUTION_NET: only 0 if per>0.
    be_fill = 0.0 if per > 0 else 1.0
    return {
        "BREAK_EVEN_ADVERSE_BPS": {
            "value": be_adverse,
            "unit": "bps_of_notional",
            "definition": "Additional adverse_bps so EXPECTED_NET <= 0, holding other costs fixed.",
        },
        "BREAK_EVEN_SLIPPAGE_BPS": {
            "value": be_slip,
            "unit": "bps_of_notional",
            "definition": "Additional slippage_bps so EXPECTED_NET <= 0.",
        },
        "BREAK_EVEN_FEE_BPS": {
            "value": be_fee_bps,
            "unit": "bps_of_notional",
            "definition": "Additional fee_bps so EXPECTED_NET <= 0.",
        },
        "BREAK_EVEN_FEE_MULT": {
            "value": (1.0 + be_fee) if be_fee is not None else None,
            "unit": "multiplier_on_roundtrip_fee",
            "definition": "Fee multiplier that zeros EXPECTED_NET.",
        },
        "BREAK_EVEN_LATENCY_BPS": {
            "value": be_latency_bps,
            "unit": "bps_of_notional",
        },
        "BREAK_EVEN_LATENCY_MS": {
            "value": be_latency_ms,
            "unit": "ms",
            "definition": f"Using frozen LATENCY_MS_TO_BPS={LATENCY_MS_TO_BPS}.",
        },
        "BREAK_EVEN_FILL": {
            "value": be_fill,
            "unit": "fill_probability",
            "definition": (
                "If per-fill edge after extra adverse is positive, only fill=0 zeros "
                "EXECUTION_NET (non-participation, not a cost break-even)."
            ),
        },
        "per_signal_EXPECTED_NET": net,
        "extra_adverse_eur": extra,
    }

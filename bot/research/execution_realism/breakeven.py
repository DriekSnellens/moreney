"""Break-even surface computation: find execution parameter boundaries."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.research.execution_realism.config import (
    BREAKEVEN_ADVERSE_ADD_BPS,
    BREAKEVEN_FEE_MULT,
    BREAKEVEN_FILL_RATE,
    BREAKEVEN_HEDGE_DELAY_MS,
    BREAKEVEN_LATENCY_MS,
    NOTIONAL_EUR,
)

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def compute_breakeven_surface(
    *,
    canonical_net_per_signal: Decimal,
    n_signals: int,
    fill_rate_baseline: float,
    fee_baseline_per_signal: Decimal,
    adverse_baseline_per_signal: Decimal,
    slippage_baseline_per_signal: Decimal,
) -> dict[str, Any]:
    """Compute where NET crosses zero along each execution dimension."""
    net_base = canonical_net_per_signal

    # Max latency before net<=0
    latency_surface = []
    for lat_ms in BREAKEVEN_LATENCY_MS:
        lat_cost = NOTIONAL_EUR * Decimal(str(lat_ms * 0.01)) / _BPS
        adj_net = net_base - lat_cost
        latency_surface.append({
            "latency_ms": lat_ms,
            "net_per_signal": str(adj_net),
            "positive": adj_net > 0,
        })

    # Max adverse add
    adverse_surface = []
    for adv_add in BREAKEVEN_ADVERSE_ADD_BPS:
        adv_cost = NOTIONAL_EUR * Decimal(str(adv_add)) / _BPS
        adj_net = net_base - adv_cost
        adverse_surface.append({
            "adverse_add_bps": adv_add,
            "net_per_signal": str(adj_net),
            "positive": adj_net > 0,
        })

    # Max fee multiplier
    fee_surface = []
    for mult in BREAKEVEN_FEE_MULT:
        extra_fee = fee_baseline_per_signal * (Decimal(str(mult)) - Decimal("1"))
        adj_net = net_base - extra_fee
        fee_surface.append({
            "fee_multiplier": mult,
            "net_per_signal": str(adj_net),
            "positive": adj_net > 0,
        })

    # Minimum fill rate
    fill_surface = []
    for fr in BREAKEVEN_FILL_RATE:
        adj_net = net_base * Decimal(str(fr / fill_rate_baseline)) if fill_rate_baseline > 0 else _ZERO
        fill_surface.append({
            "fill_rate": fr,
            "net_per_signal": str(adj_net),
            "positive": adj_net > 0,
        })

    # Hedge delay
    hedge_surface = []
    for hd_ms in BREAKEVEN_HEDGE_DELAY_MS:
        hedge_cost = NOTIONAL_EUR * Decimal(str(hd_ms * 0.02)) / _BPS
        adj_net = net_base - hedge_cost
        hedge_surface.append({
            "hedge_delay_ms": hd_ms,
            "net_per_signal": str(adj_net),
            "positive": adj_net > 0,
        })

    # Find exact breakeven points
    def _find_breakeven(surface, key):
        for i in range(1, len(surface)):
            if surface[i - 1]["positive"] and not surface[i]["positive"]:
                return surface[i - 1][key]
        if all(s["positive"] for s in surface):
            return f">{surface[-1][key]}"
        return surface[0][key]

    return {
        "latency_surface": latency_surface,
        "adverse_surface": adverse_surface,
        "fee_surface": fee_surface,
        "fill_surface": fill_surface,
        "hedge_surface": hedge_surface,
        "breakeven_latency_ms": _find_breakeven(latency_surface, "latency_ms"),
        "breakeven_adverse_add_bps": _find_breakeven(adverse_surface, "adverse_add_bps"),
        "breakeven_fee_multiplier": _find_breakeven(fee_surface, "fee_multiplier"),
        "breakeven_fill_rate": _find_breakeven(fill_surface, "fill_rate"),
        "breakeven_hedge_delay_ms": _find_breakeven(hedge_surface, "hedge_delay_ms"),
        "canonical_net_per_signal": str(canonical_net_per_signal),
        "n_signals": n_signals,
    }

"""Break-even diagnostics from BASELINE totals. Observed, not interpolated."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.research.robustness.protocol import LATENCY_MS_TO_BPS
from bot.research.tournament.criteria import NOTIONAL_EUR_DEFAULT

_ZERO = Decimal("0")
_BPS = Decimal("10000")
_NOTIONAL = Decimal(str(NOTIONAL_EUR_DEFAULT))
_HEDGE_MS_TO_BPS = Decimal("0.02")


def _d(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def break_even_from_baseline(
    *,
    execution_net: Decimal,
    n_signals: int,
    fee_sum: Decimal,
    fill_prob_grid: tuple[float, ...] = (1.0, 0.90, 0.75, 0.50),
) -> dict[str, Any]:
    """Extra cost on every 100 EUR notional that zeros BASELINE NET."""
    n = Decimal(str(n_signals or 0))
    exposure = n * _NOTIONAL
    out: dict[str, Any] = {
        "method": "analytical_from_baseline_totals",
        "interpolation": False,
    }
    if execution_net <= _ZERO or n_signals <= 0:
        out.update(
            {
                "extra_adverse_required_to_zero_NET_bps": "already_non_positive",
                "extra_slippage_required_to_zero_NET_bps": "already_non_positive",
                "fee_multiplier_required_to_zero_NET": "already_non_positive",
                "fill_rate_required_to_zero_NET": "already_non_positive",
                "latency_degradation_required_to_zero_NET_ms": "already_non_positive",
                "hedge_delay_required_to_zero_NET_ms": "already_non_positive",
            }
        )
        return out

    extra_bps = (execution_net / exposure) * _BPS
    fee_mult = Decimal("1") + (execution_net / fee_sum) if fee_sum > 0 else None
    lat_ms = extra_bps / Decimal(str(LATENCY_MS_TO_BPS)) if LATENCY_MS_TO_BPS else None
    hedge_ms = extra_bps / _HEDGE_MS_TO_BPS

    fill_obs = []
    for p in fill_prob_grid:
        adj = execution_net * Decimal(str(p))
        fill_obs.append(
            {
                "fill_prob": p,
                "expected_net_if_uniform_miss": str(adj),
                "positive": adj > 0,
            }
        )

    out.update(
        {
            "extra_adverse_required_to_zero_NET_bps": str(extra_bps),
            "extra_slippage_required_to_zero_NET_bps": str(extra_bps),
            "fee_multiplier_required_to_zero_NET": None if fee_mult is None else str(fee_mult),
            "fill_rate_required_to_zero_NET": "0_sign_invariant_under_uniform_miss",
            "fill_rate_observed_grid": fill_obs,
            "latency_degradation_required_to_zero_NET_ms": None if lat_ms is None else str(lat_ms),
            "hedge_delay_required_to_zero_NET_ms": str(hedge_ms),
            "note": (
                "Uniform missed fills scale expected NET by p and do not cross zero "
                "while p>0 and BASELINE>0. Partial-fill inventory is in the scenario cells."
            ),
        }
    )
    return out

"""Capital Velocity Desk kill criteria (post-CVD product decision).

Evaluates observational status dicts against the hard gates in
``docs/POST_CVD_VELOCITY_DESK.md``. Does not place or cancel orders.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _dec(value: Any, default: str = "0") -> Decimal:
    try:
        if value is None:
            return Decimal(default)
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def evaluate_kill_criteria(
    *,
    ring_eur: Any = 0,
    free_eur: Any = 0,
    hours_since_unlock: Any = 0,
    fills_per_hour: Any = 0,
    sleeve_net_eur: Any = 0,
    closed_round_trips: Any = 0,
    window_hours: Any = 48,
    mean_adverse_bps: Any | None = None,
    expected_margin_bps: Any | None = None,
    sleeve_daily_pnl_eur: Any = 0,
    weekly_fifo_eur: Any = 0,
    days_unlocked: Any = 0,
    net_per_hour_eur: Any = 0,
) -> dict[str, Any]:
    """Return kill evaluation for Capital Velocity Desk gates.

    Returns keys: ``killed`` (bool), ``reasons`` (list[str]), ``stage_ok`` (dict).
    """
    ring = _dec(ring_eur)
    free = _dec(free_eur)
    hours = float(hours_since_unlock or 0)
    fph = float(fills_per_hour or 0)
    sleeve_net = _dec(sleeve_net_eur)
    rts = int(closed_round_trips or 0)
    window_h = float(window_hours or 48)
    sleeve_day = _dec(sleeve_daily_pnl_eur)
    weekly = _dec(weekly_fifo_eur)
    days = float(days_unlocked or 0)
    nph = _dec(net_per_hour_eur)

    reasons: list[str] = []
    stage_ok = {
        "deploy": True,
        "velocity": True,
        "expectancy": True,
        "toxicity": True,
        "drawdown": True,
        "thesis": True,
    }

    if hours >= 24 and free >= Decimal("500") and ring <= 0:
        stage_ok["deploy"] = False
        reasons.append("DEPLOY_KILL ring=€0 for ≥24h with free≥€500")

    if window_h >= 48 and ring >= Decimal("600") and fph < 2.0:
        stage_ok["velocity"] = False
        reasons.append("VELOCITY_KILL ring≥€600 but fills/hour<2 over 48h")

    if window_h >= 48 and rts >= 30 and sleeve_net < 0:
        stage_ok["expectancy"] = False
        reasons.append("EXPECTANCY_KILL sleeve NET<€0 over 48h with ≥30 RTs")

    if mean_adverse_bps is not None and expected_margin_bps is not None:
        adv = _dec(mean_adverse_bps)
        margin = _dec(expected_margin_bps)
        if margin > 0 and adv > margin:
            stage_ok["toxicity"] = False
            reasons.append(
                f"TOXICITY_KILL mean_adverse={adv}bps > expected_margin={margin}bps"
            )

    if sleeve_day <= Decimal("-50") or weekly <= Decimal("-75"):
        stage_ok["drawdown"] = False
        reasons.append(
            f"DRAWDOWN_KILL sleeve_day={sleeve_day} weekly_fifo={weekly}"
        )

    if days >= 5 and nph < Decimal("0.5"):
        stage_ok["thesis"] = False
        reasons.append("THESIS_KILL NET/hour<€0.50 after ≥5 days unlocked")

    return {
        "killed": bool(reasons),
        "reasons": reasons,
        "stage_ok": stage_ok,
        "product": "capital_velocity_desk",
        "cvd_status": "ABANDONED",
    }

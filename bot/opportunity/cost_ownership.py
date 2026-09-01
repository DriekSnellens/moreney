"""Single-owner table for every economic cost component.

Each cost must have exactly one primary owner in the decision path.
``used_in_*`` flags document where the number is consumed so audits can
spot double subtraction (e.g. buffer in NET and again in EV loss).
"""

from __future__ import annotations

from typing import Any


# Ownership is declarative documentation + runtime snapshot helper.
COST_OWNERS: list[dict[str, Any]] = [
    {
        "component": "gross_opportunity",
        "owner": "strategy_quote_vs_fair_value",
        "raw_source": "entry/exit prices at decision",
        "used_in_net": True,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": True,
        "notes": "Positive gross alone never allows a trade",
    },
    {
        "component": "buy_fees",
        "owner": "fee_calculator",
        "raw_source": "venue fee schedule × notional",
        "used_in_net": True,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": True,
        "notes": "Single subtraction via profitability.fees_usd",
    },
    {
        "component": "sell_fees",
        "owner": "fee_calculator",
        "raw_source": "venue fee schedule × notional",
        "used_in_net": True,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": True,
        "notes": "Bundled into fees_usd with buy_fees",
    },
    {
        "component": "slippage",
        "owner": "slippage_model",
        "raw_source": "depth / impact model",
        "used_in_net": True,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": True,
        "notes": "Maker strategy forces slippage_bps=0 at detect; fills may still slip",
    },
    {
        "component": "execution_buffer",
        "owner": "net_profit_calculator",
        "raw_source": "1 bp + paper_maker_adverse_bps (maker)",
        "used_in_net": True,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": False,
        "notes": "EXPECTED haircut only — never a realized cash line",
    },
    {
        "component": "extra_regime_venue_adverse",
        "owner": "fill_economics",
        "raw_source": "max(0, venue/regime adverse − raw adverse already in buffer)",
        "used_in_net": True,
        "used_in_ev": False,
        "used_in_calibrated_ev": False,
        "used_in_risk": False,
        "used_in_realized": False,
        "notes": "Only the incremental adverse beyond buffer to avoid double count",
    },
    {
        "component": "conditional_fill_adverse",
        "owner": "expected_value_engine",
        "raw_source": "E[adverse|fill] from markout when fill is trade-through conditioned",
        "used_in_net": False,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": False,
        "notes": "Replaces independence assumption EV=p_fill×NET when fills are toxic-conditioned",
    },
    {
        "component": "funding",
        "owner": "net_profit_calculator",
        "raw_source": "funding_rate × periods",
        "used_in_net": True,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": True,
        "notes": "Off for spot maker",
    },
    {
        "component": "transfer_fx",
        "owner": "transfer_cost + strategy extra_cost_eur",
        "raw_source": "cross-exchange transfer / EURUSDT refill",
        "used_in_net": True,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": True,
        "notes": "Triangle FX must be in NET at detection, not only post-trade",
    },
    {
        "component": "inventory_relief",
        "owner": "fill_economics",
        "raw_source": "inventory_skew_score × 1e-4, capped at 50% of positive raw NET",
        "used_in_net": True,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": False,
        "notes": "Ranking bonus only; cannot flip raw NET ≤ 0 to positive",
    },
    {
        "component": "markout_gate_adverse_bps",
        "owner": "markout_tracker → strategy.update_adverse_bps",
        "raw_source": "rolling median 5s adverse, floor/ceiling",
        "used_in_net": True,
        "used_in_ev": True,
        "used_in_calibrated_ev": True,
        "used_in_risk": True,
        "used_in_realized": False,
        "notes": "Feeds buffer; ceiling must not clip below observed toxicity",
    },
    {
        "component": "ev_calibration_capture",
        "owner": "ev_calibrator",
        "raw_source": "sum(realized)/sum(expected) with shrinkage to 1.0",
        "used_in_net": False,
        "used_in_ev": False,
        "used_in_calibrated_ev": True,
        "used_in_risk": False,
        "used_in_realized": False,
        "notes": "Multiplies raw EV; early route stop uses raw capture separately",
    },
]


def ownership_table() -> list[dict[str, Any]]:
    return [dict(row) for row in COST_OWNERS]


def components_used_in(stage: str) -> list[str]:
    key = {
        "net": "used_in_net",
        "ev": "used_in_ev",
        "calibrated_ev": "used_in_calibrated_ev",
        "risk": "used_in_risk",
        "realized": "used_in_realized",
    }.get(stage)
    if not key:
        return []
    return [str(r["component"]) for r in COST_OWNERS if r.get(key)]

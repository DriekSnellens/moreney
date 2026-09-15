"""Edge decomposition aggregates for dashboards and audits."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from bot.opportunity.waterfall import decompose_trade_row

_ZERO = Decimal("0")


def edge_decomposition(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate waterfall contributions across completed trades.

    Returns overall + per strategy / venue / symbol / route breakdowns.
    """
    overall = _empty_agg()
    by: dict[str, dict[str, dict[str, Any]]] = {
        "strategy": defaultdict(_empty_agg),
        "venue_route": defaultdict(_empty_agg),
        "symbol": defaultdict(_empty_agg),
        "route": defaultdict(_empty_agg),
    }

    for trade in trades:
        parts = decompose_trade_row(trade)
        realized = parts["realized"]
        expected = parts["expected"]
        _accumulate(overall, expected, realized, trade)
        route = f"{trade.get('buy_exchange')}->{trade.get('sell_exchange')}"
        keys = {
            "strategy": str(trade.get("strategy") or "unknown"),
            "venue_route": route,
            "symbol": str(trade.get("symbol") or "unknown"),
            "route": route,
        }
        for dim, key in keys.items():
            _accumulate(by[dim][key], expected, realized, trade)

    return {
        "overall": _finalize(overall),
        "by_strategy": {k: _finalize(v) for k, v in by["strategy"].items()},
        "by_route": {k: _finalize(v) for k, v in by["route"].items()},
        "by_symbol": {k: _finalize(v) for k, v in by["symbol"].items()},
        "trade_count": int(overall["n"]),
        "kind": "observed",
    }


def _empty_agg() -> dict[str, Any]:
    return {
        "n": 0,
        "gross": _ZERO,
        "fees": _ZERO,
        "slippage": _ZERO,
        "adverse": _ZERO,
        "inventory": _ZERO,
        "expected_net": _ZERO,
        "realized_net": _ZERO,
        "sum_p_fill": _ZERO,
        "p_fill_n": 0,
    }


def _accumulate(
    agg: dict[str, Any],
    expected: dict[str, Any],
    realized: dict[str, Any],
    trade: dict[str, Any],
) -> None:
    agg["n"] += 1
    agg["gross"] += Decimal(str(expected.get("gross_opportunity") or 0))
    agg["fees"] += Decimal(str(realized.get("buy_fees") or 0)) + Decimal(
        str(realized.get("sell_fees") or 0)
    )
    agg["slippage"] += Decimal(str(realized.get("slippage") or 0))
    agg["adverse"] += Decimal(str(realized.get("adverse_selection") or 0))
    agg["inventory"] += Decimal(str(realized.get("inventory_effect") or 0))
    agg["expected_net"] += Decimal(str(expected.get("net") or 0))
    agg["realized_net"] += Decimal(str(realized.get("net") or 0))
    if trade.get("p_fill") is not None:
        agg["sum_p_fill"] += Decimal(str(trade.get("p_fill") or 0))
        agg["p_fill_n"] += 1


def _finalize(agg: dict[str, Any]) -> dict[str, Any]:
    n = int(agg["n"])
    exp = agg["expected_net"]
    real = agg["realized_net"]
    capture = (real / exp) if exp != 0 else None
    p_n = int(agg["p_fill_n"])
    return {
        "n": n,
        "gross_spread_contribution": str(agg["gross"]),
        "fee_contribution": str(-agg["fees"]),
        "slippage_contribution": str(-agg["slippage"]),
        "adverse_selection_contribution": str(-agg["adverse"]),
        "inventory_contribution": str(-agg["inventory"]),
        "net_alpha": str(real),
        "expected_net": str(exp),
        "realized_net": str(real),
        "ev_capture": str(capture) if capture is not None else None,
        "avg_p_fill": str(agg["sum_p_fill"] / p_n) if p_n else None,
        "e_net_given_fill": str(real / n) if n else None,
        "markout_proxy_adverse_per_fill": str(agg["adverse"] / n) if n else None,
    }

"""Execution degradation attribution for filled live orders."""

from __future__ import annotations

import statistics
from collections import defaultdict
from decimal import Decimal
from typing import Any

from bot.research.live_vs_research_attribution.loaders import LiveFillRecord, LoadedData

_ZERO = Decimal("0")
_DEFAULT_FEE_BPS = Decimal("0.0025")


def _venue_fee_estimate(notional: Decimal) -> Decimal:
    return notional * _DEFAULT_FEE_BPS


def _infer_degradation(
    fill: LiveFillRecord,
    *,
    expected_price: Decimal | None = None,
) -> dict[str, Any]:
    actual_price = fill.price
    categories: list[str] = []
    price_delta = None
    if expected_price is not None and actual_price is not None and expected_price > 0:
        price_delta = actual_price - expected_price
        if fill.side == "buy" and price_delta > 0:
            categories.append("PRICE_DEGRADATION")
        elif fill.side == "sell" and price_delta < 0:
            categories.append("PRICE_DEGRADATION")

    fee_est = _venue_fee_estimate(fill.notional_eur)
    categories.append("FEE_DEGRADATION")

    return {
        "fill_id": fill.event_id,
        "symbol": fill.symbol,
        "venue": fill.venue,
        "side": fill.side,
        "notional_eur": str(fill.notional_eur),
        "actual_price": str(actual_price) if actual_price is not None else None,
        "expected_price": str(expected_price) if expected_price is not None else None,
        "price_delta": str(price_delta) if price_delta is not None else None,
        "estimated_fee_eur": str(fee_est),
        "expected_slippage_eur": None,
        "actual_slippage_eur": None,
        "expected_net_eur": None,
        "realized_net_eur": None,
        "degradation_categories": categories or ["NOT_MEASURED"],
        "adverse_selection_score": None,
        "adverse_selection_class": None,
        "post_fill_markout_1s": None,
        "post_fill_markout_5s": None,
        "post_fill_markout_30s": None,
        "post_fill_markout_60s": None,
    }


def analyze_execution(data: LoadedData) -> dict[str, Any]:
    fills = data.live_fills
    if not fills:
        return {
            "filled_count": 0,
            "insufficient_data": ["No live fills in audit log."],
        }

    by_venue: dict[str, list[LiveFillRecord]] = defaultdict(list)
    by_side: dict[str, list[LiveFillRecord]] = defaultdict(list)
    records: list[dict[str, Any]] = []

    for fill in fills:
        by_venue[fill.venue].append(fill)
        by_side[fill.side].append(fill)
        records.append(_infer_degradation(fill))

    notionals = [float(f.notional_eur) for f in fills]
    buy_count = len(by_side.get("buy", []))
    sell_count = len(by_side.get("sell", []))

    category_counts: dict[str, int] = defaultdict(int)
    for rec in records:
        for cat in rec.get("degradation_categories") or []:
            category_counts[cat] += 1

    bridge = (data.session_status.get("bridge") or data.bridge_state) or {}
    realized = bridge.get("realized_trade_pnl_eur")

    return {
        "filled_count": len(fills),
        "buy_fills": buy_count,
        "sell_fills": sell_count,
        "total_notional_eur": str(sum((f.notional_eur for f in fills), _ZERO)),
        "mean_notional_eur": round(statistics.mean(notionals), 4) if notionals else None,
        "median_notional_eur": round(statistics.median(notionals), 4) if notionals else None,
        "realized_trade_pnl_eur": realized,
        "by_venue": {
            v: {
                "fills": len(fs),
                "notional_eur": str(sum((f.notional_eur for f in fs), _ZERO)),
            }
            for v, fs in sorted(by_venue.items())
        },
        "degradation_category_counts": dict(category_counts),
        "records_sample": records[:20],
        "insufficient_data": [
            "Expected entry/exit prices at decision time not in audit payload.",
            "Round-trip realized NET per fill requires FIFO lot pairing (partial in bridge state).",
            "Post-fill markouts require mark price time series aligned to fill timestamps.",
            "Adverse selection at fill time: intelligence attribution store is empty.",
        ],
    }


def analyze_adverse_selection(data: LoadedData) -> dict[str, Any]:
    attr_state = data.attribution_state
    intel = data.intelligence_state
    records = attr_state.get("records") or []
    outcomes = (intel.get("outcomes") or {}).get("buckets") or {}

    if not records:
        phase21 = data.phase21 or {}
        toxic = phase21.get("baseline", {}).get("toxic_proxy_count")
        avg_adv = phase21.get("baseline", {}).get("avg_adverse_score")
        return {
            "live_attribution_records": 0,
            "observation_mode": intel.get("observation_mode", True),
            "phase21_historical_fills": phase21.get("historical_fills"),
            "phase21_toxic_proxy_count": toxic,
            "phase21_avg_adverse_score": avg_adv,
            "phase21_reject_rate": phase21.get("baseline", {}).get("reject_rate"),
            "good_vs_toxic": None,
            "insufficient_data": [
                "live_micro_attribution_state.json records[] is empty — "
                "post-fill markouts not persisted live.",
                "Phase21 ablation provides proxy adverse scores on historical audit buys only.",
            ],
        }

    good = [r for r in records if not r.get("toxic_fill")]
    toxic = [r for r in records if r.get("toxic_fill")]
    return {
        "live_attribution_records": len(records),
        "good_fill_count": len(good),
        "toxic_fill_count": len(toxic),
        "outcome_buckets": len(outcomes),
        "insufficient_data": [],
    }

"""Pre-trade feature labels for forensic attribution.

Future timestamps are rejected. Outcomes (forward, replay net) are never
used as admission features.
"""

from __future__ import annotations

from typing import Any

from bot.research.forensics.buckets import (
    liquidity_regime,
    density_regime,
    quote_age_regime,
    spread_regime,
    strength_regime,
    utc_hour,
    vol_regime,
)
from bot.research.alpha_attribution.protocol import CONTEXT_NAMES, IMBALANCE_FLAT
from bot.research.regime_lab.features import assert_pretrade, enrich_pretrade
from bot.research.robustness.protocol import FROZEN_H0005_PARAMS

DISLOCATION_THRESHOLD_BPS = float(FROZEN_H0005_PARAMS["dislocation_bps"])

PRETRADE_FEATURES = (
    "symbol",
    "route",
    "venue",
    "side",
    "quote_age_ms",
    "quote_age_regime",
    "spread_bps",
    "spread_regime",
    "cross_venue_divergence_bps",
    "strength_regime",
    "book_imbalance",
    "imbalance_regime",
    "top_of_book_depth_eur",
    "liquidity_regime",
    "event_density",
    "density_regime",
    "volatility_bps",
    "vol_regime",
    "time_of_day_utc_hour",
    "session_utc",
    "fee_burden_route_constant",
    "notional_eur_constant",
)

UNAVAILABLE_PRETRADE = (
    "inventory_state",
    "predicted_adverse_state",  # research uses frozen 8 bps model, not a state predictor
    "fill_probability_state",  # research uses frozen 0.55 model
)

OUTCOME_ONLY = (
    "forward",
    "net",
    "gross",
    "replay_net",
)


def session_utc(hour: int | None) -> str:
    if hour is None:
        return "UNKNOWN"
    if hour < 8:
        return "UTC_00_08"
    if hour < 16:
        return "UTC_08_16"
    return "UTC_16_24"


def imbalance_regime(imb: float | None) -> str:
    if imb is None:
        return "UNKNOWN"
    if abs(float(imb)) < IMBALANCE_FLAT:
        return "FLAT"
    return "BID_HEAVY" if float(imb) > 0 else "ASK_HEAVY"


def _imbalance(event: dict[str, Any]) -> float | None:
    bid = event.get("bid_size")
    ask = event.get("ask_size")
    if bid is None or ask is None:
        return None
    try:
        b = float(bid)
        a = float(ask)
    except (TypeError, ValueError):
        return None
    tot = b + a
    if tot <= 0:
        return None
    return (b - a) / tot


def _side(event: dict[str, Any]) -> str:
    dis = event.get("dislocation")
    if dis is None:
        return "UNKNOWN"
    return "A_RICH" if float(dis) > 0 else "A_CHEAP"


def attach_attribution_features(
    event: dict[str, Any],
    *,
    index,
    views,
    venue: str,
    peer_venue: str | None,
) -> dict[str, Any]:
    row = enrich_pretrade(
        event, index=index, views=views, venue=venue, peer_venue=peer_venue
    )
    ts = row.get("ts_ns")
    if ts is not None:
        assert_pretrade(row, int(ts))
        for key in ("peer_ts_ns", "exchange_timestamp"):
            val = row.get(key)
            if val is not None and int(val) > int(ts):
                raise RuntimeError(f"future data in {key}")
    symbol = str(row.get("symbol") or "")
    view = views.get((venue, symbol)) if views is not None else None
    i = view.index_at_or_before(int(ts)) if view is not None and ts is not None else None
    p = view.points[i] if view is not None and i is not None else None
    if p is not None:
        row["bid_size"] = p.bid_size
        row["ask_size"] = p.ask_size
    imb = _imbalance(row)
    div = row.get("cross_venue_divergence")
    if div is None and row.get("dislocation") is not None:
        div = abs(float(row["dislocation"])) * 10000.0
        row["cross_venue_divergence"] = div
    hour = utc_hour(int(ts)) if ts is not None else None
    age = row.get("quote_age_ms")
    row.update(
        {
            "side": _side(row),
            "book_imbalance": imb,
            "imbalance_regime": imbalance_regime(imb),
            "top_of_book_depth_eur": row.get("depth_eur"),
            "liquidity_regime": liquidity_regime(
                None if row.get("depth_eur") is None else float(row.get("depth_eur"))
            ),
            "quote_age_regime": quote_age_regime(
                None if age is None else float(age)
            ),
            "spread_regime": spread_regime(
                None if row.get("spread_bps") is None else float(row.get("spread_bps"))
            ),
            "strength_regime": strength_regime(
                None if div is None else float(div),
                DISLOCATION_THRESHOLD_BPS,
            ),
            "density_regime": density_regime(
                None if row.get("event_density") is None else int(row.get("event_density"))
            ),
            "vol_regime": vol_regime(
                None if row.get("vol_bps") is None else float(row.get("vol_bps"))
            ),
            "volatility_bps": row.get("vol_bps"),
            "cross_venue_divergence_bps": div,
            "time_of_day_utc_hour": hour,
            "session_utc": session_utc(hour),
            "fee_burden_route_constant": True,
            "notional_eur_constant": 100.0,
            "inventory_state": "UNAVAILABLE_PRETRADE",
            "predicted_adverse_state": "MODEL_CONSTANT_NOT_PRETRADE_STATE",
            "fill_probability_state": "MODEL_CONSTANT_NOT_PRETRADE_STATE",
            "descriptive_only": True,
        }
    )
    row["context"] = named_context(row)
    return row


def named_context(row: dict[str, Any]) -> str:
    age = str(row.get("quote_age_regime") or "UNKNOWN")
    strength = str(row.get("strength_regime") or "UNKNOWN")
    liq = str(row.get("liquidity_regime") or "UNKNOWN")
    strong = strength in {"MEDIUM", "STRONG"}
    if age == "UNKNOWN":
        return "UNKNOWN_AGE"
    if age == "VERY_STALE":
        return "VERY_STALE"
    if age == "STALE":
        return "STALE_STRONG" if strong else "STALE_NOT_STRONG"
    # FRESH
    if not strong:
        return "FRESH_NOT_STRONG"
    if liq == "DEEP":
        return "FRESH_STRONG_DEEP"
    return "FRESH_STRONG_NOT_DEEP"


def classify_membership(*, admission: str) -> str:
    if admission == "ADMITTED":
        return "RETAINED_BY_CHILD"
    if admission == "REJECTED":
        return "EXCLUDED_BY_CHILD"
    return "UNSUPPORTED"


assert set(CONTEXT_NAMES) >= {
    "FRESH_STRONG_DEEP",
    "FRESH_STRONG_NOT_DEEP",
    "FRESH_NOT_STRONG",
    "STALE_STRONG",
    "STALE_NOT_STRONG",
    "VERY_STALE",
    "UNKNOWN_AGE",
}

"""Deterministic candidate-set fingerprint for equivalence after optimization.

Hashes economically relevant fields only — not random UUIDs or wall-clock
created_at. Sort order is stable so set equality is order-independent while
a separate ordering fingerprint checks emit order.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence


def _d(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def candidate_record(opportunity: Any) -> dict[str, Any]:
    """Extract frozen economic fields from a TradeOpportunity-like object."""
    meta = getattr(opportunity, "metadata", None) or {}
    if not isinstance(meta, Mapping):
        meta = dict(meta)
    side = getattr(opportunity, "side", None)
    side_v = side.value if hasattr(side, "value") else str(side or "")
    return {
        "deterministic_key": "|".join(
            [
                str(getattr(opportunity, "strategy_name", "") or ""),
                str(getattr(opportunity, "symbol", "") or "").upper(),
                side_v,
                str(meta.get("buy_exchange") or ""),
                str(meta.get("sell_exchange") or ""),
                _d(getattr(opportunity, "quantity", "")),
                _d(getattr(opportunity, "entry_price", "")),
                _d(getattr(opportunity, "expected_exit_price", "")),
            ]
        ),
        "strategy": str(getattr(opportunity, "strategy_name", "") or ""),
        "route": f"{meta.get('buy_exchange')}|{meta.get('sell_exchange')}",
        "venue": str(meta.get("buy_exchange") or ""),
        "symbol": str(getattr(opportunity, "symbol", "") or "").upper(),
        "side": side_v,
        "quantity": _d(getattr(opportunity, "quantity", "")),
        "entry_price": _d(getattr(opportunity, "entry_price", "")),
        "expected_exit_price": _d(getattr(opportunity, "expected_exit_price", "")),
        "gross_profit_eur": _d(meta.get("gross_profit_eur")),
        "net_profit_eur": _d(meta.get("net_profit_eur")),
        "net_return": _d(meta.get("net_return")),
        "fair_value_eur": _d(meta.get("fair_value_eur")),
        "fair_value_aligned": bool(meta.get("fair_value_aligned")),
        "post_only": bool(meta.get("post_only")),
        "sell_only": bool(meta.get("sell_only")),
        "pricing": str(meta.get("pricing") or ""),
        "buy_maker_fee_rate": _d(meta.get("buy_maker_fee_rate")),
        "sell_maker_fee_rate": _d(meta.get("sell_maker_fee_rate")),
        "adverse_bps": _d(meta.get("adverse_bps")),
        "inventory_skew_score": _d(meta.get("inventory_skew_score")),
        "entry_fee_role": str(
            getattr(getattr(opportunity, "entry_fee_role", None), "value", "")
            or getattr(opportunity, "entry_fee_role", "")
            or ""
        ),
        "exit_fee_role": str(
            getattr(getattr(opportunity, "exit_fee_role", None), "value", "")
            or getattr(opportunity, "exit_fee_role", "")
            or ""
        ),
        "rationale": str(getattr(opportunity, "rationale", "") or ""),
    }


def fingerprint_candidates(opportunities: Sequence[Any]) -> dict[str, Any]:
    """Return sorted records + sha256 of the canonical JSON payload."""
    records = [candidate_record(o) for o in opportunities]
    ordered = sorted(records, key=lambda r: r["deterministic_key"])
    emit_order = [r["deterministic_key"] for r in records]
    payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "count": len(ordered),
        "sha256": digest,
        "emit_order": emit_order,
        "records": ordered,
    }


def fingerprints_equal(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return a.get("sha256") == b.get("sha256") and a.get("count") == b.get("count")


def downstream_decision_fingerprint(
    *,
    reject_counts: Mapping[str, Any] | None = None,
    goe_ranking: Mapping[str, Any] | None = None,
    fills: Iterable[Mapping[str, Any]] | None = None,
    realized_nets: Iterable[Any] | None = None,
    route_states: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stable fingerprint of GOE / fill / NET outcomes for equivalence tests."""

    def _norm_fills(items: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in items or ():
            out.append(
                {
                    "symbol": str(f.get("symbol") or ""),
                    "side": str(f.get("side") or ""),
                    "quantity": _d(f.get("quantity")),
                    "price": _d(f.get("price") or f.get("average_price")),
                    "fee": _d(f.get("fee") or f.get("fees_usd")),
                    "venue": str(f.get("venue") or f.get("exchange") or ""),
                }
            )
        return sorted(out, key=lambda r: json.dumps(r, sort_keys=True))

    body = {
        "reject_counts": dict(sorted((reject_counts or {}).items())),
        "goe_ranking": goe_ranking or {},
        "fills": _norm_fills(fills),
        "realized_nets": [_d(x) for x in (realized_nets or ())],
        "route_states": sorted(
            [dict(sorted(dict(r).items())) for r in (route_states or ())],
            key=lambda r: json.dumps(r, sort_keys=True),
        ),
    }
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return {
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "body": body,
    }

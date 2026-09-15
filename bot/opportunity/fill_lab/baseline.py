"""Extract baseline QuoteEvent / FillEvent streams from frozen paper state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.opportunity.fill_lab.events import FillEvent, QuoteEvent
from bot.opportunity.fill_lab.models import FillModelId

_ZERO = Decimal("0")


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


def _parse_ts_ms(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    except Exception:
        try:
            return float(text)
        except Exception:
            return None


def load_paper(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def extract_quotes(data: dict[str, Any]) -> list[QuoteEvent]:
    orders = data.get("orders") or {}
    if not isinstance(orders, dict):
        return []
    quotes: list[QuoteEvent] = []
    for oid, o in orders.items():
        if not isinstance(o, dict):
            continue
        extra = o.get("extra") or {}
        if not extra.get("post_only"):
            continue
        placed = _parse_ts_ms(extra.get("placed_ms"))
        if placed is None:
            continue
        venue = str(extra.get("venue") or o.get("exchange") or "").lower()
        side = str(o.get("side") or "").lower()
        quotes.append(
            QuoteEvent(
                quote_id=str(o.get("id") or oid),
                opportunity_id=str(o.get("opportunity_id") or ""),
                timestamp_ms=placed,
                symbol=str(o.get("symbol") or "").upper(),
                side=side,
                venue=venue,
                price=_d(o.get("requested_price")),
                quantity=_d(o.get("requested_quantity")),
                strategy=str(o.get("strategy") or extra.get("strategy") or ""),
                post_only=True,
                route="",
                metadata={"status": o.get("status"), "filled_quantity": o.get("filled_quantity")},
            )
        )
    quotes.sort(key=lambda q: q.timestamp_ms)
    return quotes


def extract_baseline_fills(data: dict[str, Any]) -> list[FillEvent]:
    """Observed TRADE_THROUGH fills only — production baseline."""
    orders = data.get("orders") or {}
    fills = data.get("fills") or {}
    trades = list((data.get("tracker") or {}).get("trades") or [])
    trade_by_opp = {str(t.get("opportunity_id")): t for t in trades}

    order_by_id = orders if isinstance(orders, dict) else {}
    out: list[FillEvent] = []
    if not isinstance(fills, dict):
        return out

    for fid, f in fills.items():
        if not isinstance(f, dict):
            continue
        extra = f.get("extra") or {}
        ft = str(extra.get("fill_type") or "").lower()
        if ft and ft != "trade_through":
            # Non-TT fills exist (e.g. taker one-leg) — exclude from TT baseline set
            # but we still only label TT as baseline model fills.
            if ft != "trade_through":
                continue
        if not ft:
            # Infer from linked order
            order = order_by_id.get(str(f.get("order_id") or ""))
            if isinstance(order, dict):
                ft = str((order.get("extra") or {}).get("last_fill_type") or "").lower()
        if ft != "trade_through":
            continue

        order = order_by_id.get(str(f.get("order_id") or ""))
        placed = None
        opp_id = ""
        quote_id = str(f.get("order_id") or "")
        if isinstance(order, dict):
            placed = _parse_ts_ms((order.get("extra") or {}).get("placed_ms"))
            opp_id = str(order.get("opportunity_id") or "")
            quote_id = str(order.get("id") or quote_id)

        trade = trade_by_opp.get(opp_id) or {}
        fill_ts = _parse_ts_ms(f.get("created_at")) or _parse_ts_ms(trade.get("timestamp"))
        if fill_ts is None and placed is not None:
            fill_ts = placed  # last resort
        if fill_ts is None:
            continue
        age = (fill_ts - placed) if placed is not None else 0.0
        # Capital lock: quote post → round-trip trade timestamp when available
        lock = None
        if placed is not None and trade.get("timestamp"):
            t_ms = _parse_ts_ms(trade.get("timestamp"))
            if t_ms is not None and t_ms >= placed:
                lock = t_ms - placed

        out.append(
            FillEvent(
                fill_id=str(f.get("id") or fid),
                quote_id=quote_id,
                opportunity_id=opp_id,
                fill_type="trade_through",
                fill_timestamp_ms=fill_ts,
                fill_price=_d(f.get("price")),
                quantity=_d(f.get("quantity")),
                symbol=str(f.get("symbol") or "").upper(),
                side=str(f.get("side") or "").lower(),
                venue=str(f.get("exchange") or "").lower(),
                quote_age_ms=float(age),
                model_id=FillModelId.TRADE_THROUGH_ONLY.value,
                observational=False,
                realized_net_eur=_d(trade.get("realized_net_profit")) if trade else None,
                fees_eur=_d(f.get("fee")),
                capital_lock_ms=lock,
                notes=("CONSERVATIVE_BASELINE observed fill",),
            )
        )
    out.sort(key=lambda x: x.fill_timestamp_ms)
    return out


def attach_markouts_from_export(
    fills: list[FillEvent],
    data: dict[str, Any],
) -> list[FillEvent]:
    """Attach horizon markouts when per-fill join is unavailable.

    Markout export stores horizon lists without fill_id. We therefore attach
    *distribution-level* stats in the study, not per-fill fabricated joins.
    This function is a no-op identity for honesty.
    """
    return fills


def baseline_fingerprint(path: Path | str) -> dict[str, Any]:
    data = load_paper(path)
    trades = list((data.get("tracker") or {}).get("trades") or [])
    fills = extract_baseline_fills(data)
    quotes = extract_quotes(data)
    nets = [_d(t.get("realized_net_profit")) for t in trades]
    return {
        "model": FillModelId.TRADE_THROUGH_ONLY.value,
        "status": "CONSERVATIVE_BASELINE",
        "quote_count": len(quotes),
        "baseline_fill_count": len(fills),
        "completed_round_trips": len(trades),
        "realized_net_sum": str(sum(nets, _ZERO)),
        "realized_net_per_trade": [str(n) for n in nets],
        "opportunity_ids": [str(t.get("opportunity_id")) for t in trades],
        "fill_ids": [f.fill_id for f in fills],
        "fill_types": sorted({f.fill_type for f in fills}),
        "fees_sum": str(sum((_d(t.get("realized_fees") or t.get("fees")) for t in trades), _ZERO)),
        "adverse_sum": str(sum((_d(t.get("realized_adverse")) for t in trades), _ZERO)),
    }

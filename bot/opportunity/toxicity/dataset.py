"""Build causal pre-trade dataset from frozen paper state.

Labels (markout / realized adverse) are attached only to completed fills.
Rejected decision_log rows never receive adverse labels.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.opportunity.quote_economics import quote_age_bucket
from bot.opportunity.toxicity.types import LabeledEvent, PreTradeFeatures

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


def _spread_bucket(spread_bps: Decimal) -> str:
    x = float(spread_bps)
    if x < 5:
        return "spread_lt_5"
    if x < 15:
        return "spread_5_15"
    if x < 30:
        return "spread_15_30"
    return "spread_gte_30"


def _vol_bucket_from_adverse_proxy(adv_bps: Decimal) -> str:
    """Offline proxy only for bucketing historical rows without live vol.

    Uses |realized| label ONLY when building retrospective buckets for the
    labeled event after the fact — never as a pre-trade feature for prediction
    at decision time. For decision-time features we leave vol_bucket=unknown
    unless an explicit pre-trade vol is present in metadata.
    """
    x = abs(float(adv_bps))
    if x < 10:
        return "vol_lt_10"
    if x < 25:
        return "vol_10_25"
    return "vol_gte_25"


def estimate_notional_eur(trade: dict[str, Any]) -> Decimal:
    fees = _d(trade.get("realized_fees") or trade.get("expected_fees") or trade.get("fees"))
    qty = _d(trade.get("quantity"))
    # Prefer fee/notional inversion with retail maker-ish rates when fees known.
    if fees > 0:
        # Blended guess 10 bps; clamp notional to qty-based sanity when possible.
        notional = fees / Decimal("0.001")
        if qty > 0 and notional > 0:
            return notional
    gross = _d(trade.get("expected_gross"))
    if gross > 0:
        # Rough: assume gross ~ 5–30 bps of notional → use 15 bps mid.
        return gross / Decimal("0.0015")
    return _ZERO


def adverse_bps_from_trade(trade: dict[str, Any], notional: Decimal) -> Decimal:
    adv = _d(trade.get("realized_adverse"))
    if notional <= 0:
        return _ZERO
    return adv / notional * _BPS


def features_from_trade(
    trade: dict[str, Any],
    *,
    decision_meta: dict[str, Any] | None = None,
) -> PreTradeFeatures:
    meta = decision_meta or {}
    buy = str(trade.get("buy_exchange") or meta.get("buy_exchange") or "").lower()
    sell = str(trade.get("sell_exchange") or meta.get("sell_exchange") or "").lower()
    route = f"{buy}->{sell}"
    side = str(meta.get("direction") or meta.get("side") or "buy").lower()
    # Primary venue for trade-through maker: the resting venue that got hit.
    venue = buy if side == "buy" else sell
    if not venue:
        venue = buy or sell
    notional = estimate_notional_eur(trade)
    book_age = _d(meta.get("book_age_ms") or trade.get("book_age_ms") or 0)
    try:
        age_bucket = quote_age_bucket(float(book_age)) if book_age > 0 else "unknown"
    except Exception:
        age_bucket = "unknown"
    # Spread from expected gross / notional when available.
    gross = _d(trade.get("expected_gross"))
    spread_bps = (gross / notional * _BPS) if notional > 0 and gross > 0 else _ZERO
    fill_type = str(
        meta.get("expected_fill_type")
        or meta.get("fill_type")
        or trade.get("fill_type")
        or "trade_through"
    ).lower()
    return PreTradeFeatures(
        timestamp=str(trade.get("timestamp") or ""),
        opportunity_id=str(trade.get("opportunity_id") or ""),
        venue=venue,
        route=route,
        symbol=str(trade.get("symbol") or "").upper(),
        side=side,
        strategy=str(trade.get("strategy") or meta.get("strategy") or ""),
        fill_type=fill_type,
        spread_bps=spread_bps,
        book_age_ms=book_age,
        quote_age_bucket=age_bucket,
        spread_bucket=_spread_bucket(spread_bps),
        vol_bucket=str(meta.get("vol_bucket") or "unknown"),
        regime=str(meta.get("regime") or "unknown"),
        fair_value_deviation_bps=_d(meta.get("fair_value_deviation_bps") or 0),
        inventory_direction=str(meta.get("inventory_direction") or "unknown"),
        expected_gross_eur=gross,
        expected_fees_eur=_d(trade.get("expected_fees") or trade.get("fees")),
        expected_slippage_eur=_d(trade.get("expected_slippage") or trade.get("slippage")),
        expected_buffer_eur=_d(trade.get("expected_adverse")),
        expected_net_eur=_d(trade.get("expected_net_profit")),
        notional_eur=notional,
    )


def features_from_opportunity_metadata(
    *,
    opportunity_id: str,
    timestamp: str,
    strategy: str,
    symbol: str,
    side: str,
    buy_exchange: str,
    sell_exchange: str,
    metadata: dict[str, Any] | None,
    expected_gross: Decimal,
    expected_fees: Decimal,
    expected_slippage: Decimal,
    expected_buffer: Decimal,
    expected_net: Decimal,
    notional: Decimal,
) -> PreTradeFeatures:
    meta = metadata or {}
    buy = buy_exchange.lower()
    sell = sell_exchange.lower()
    side_l = side.lower()
    venue = buy if side_l == "buy" else sell
    book_age = _d(meta.get("book_age_ms") or 0)
    try:
        age_bucket = quote_age_bucket(float(book_age)) if book_age > 0 else "unknown"
    except Exception:
        age_bucket = "unknown"
    spread_bps = (expected_gross / notional * _BPS) if notional > 0 and expected_gross > 0 else _ZERO
    return PreTradeFeatures(
        timestamp=timestamp,
        opportunity_id=opportunity_id,
        venue=venue or buy or sell,
        route=f"{buy}->{sell}",
        symbol=symbol.upper(),
        side=side_l,
        strategy=strategy,
        fill_type=str(meta.get("expected_fill_type") or "trade_through").lower(),
        spread_bps=spread_bps,
        book_age_ms=book_age,
        quote_age_bucket=age_bucket,
        spread_bucket=_spread_bucket(spread_bps),
        vol_bucket=str(meta.get("vol_bucket") or "unknown"),
        regime=str(meta.get("regime") or "unknown"),
        fair_value_deviation_bps=_d(meta.get("fair_value_deviation_bps") or 0),
        inventory_direction=str(meta.get("inventory_direction") or "unknown"),
        expected_gross_eur=expected_gross,
        expected_fees_eur=expected_fees,
        expected_slippage_eur=expected_slippage,
        expected_buffer_eur=expected_buffer,
        expected_net_eur=expected_net,
        notional_eur=notional,
    )


def load_paper_state(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def build_labeled_events(path: Path | str) -> list[LabeledEvent]:
    """Completed round-trips only — rejects never appear as labeled events."""
    data = load_paper_state(path)
    trades = list((data.get("tracker") or {}).get("trades") or [])
    decisions = {
        str(row.get("opportunity_id")): row
        for row in (data.get("decision_log") or [])
        if isinstance(row, dict) and row.get("opportunity_id")
    }
    fills = data.get("fills") or {}
    fill_types_by_opp: dict[str, str] = {}
    if isinstance(fills, dict):
        # Map via orders if needed — fill extras carry fill_type.
        for fill in fills.values():
            if not isinstance(fill, dict):
                continue
            ft = str((fill.get("extra") or {}).get("fill_type") or "").lower()
            if ft:
                # Best-effort: tag by symbol+side+exchange for later join
                key = f"{fill.get('symbol')}|{fill.get('side')}|{fill.get('exchange')}"
                fill_types_by_opp[key] = ft

    # Horizon lists are not per-trade aligned in export — use adverse EUR→bps proxy
    # as primary 5s label; attach horizon means only as diagnostics at report level.
    events: list[LabeledEvent] = []
    for trade in sorted(trades, key=lambda t: t.get("timestamp") or ""):
        opp_id = str(trade.get("opportunity_id") or "")
        decision = decisions.get(opp_id) or {}
        feats = features_from_trade(trade, decision_meta=decision)
        notional = feats.notional_eur
        adv_bps = adverse_bps_from_trade(trade, notional)
        # For bucketed offline fit only, annotate a retrospective vol bucket on a copy
        # used solely after label is known — prediction path uses features.vol_bucket
        # which stays "unknown" unless live meta provides it.
        realized_net = _d(trade.get("realized_net_profit"))
        ft_key = f"{feats.symbol}|{feats.side}|{feats.venue}"
        fill_obs = fill_types_by_opp.get(ft_key) or feats.fill_type or "trade_through"
        events.append(
            LabeledEvent(
                features=feats,
                realized_net_eur=realized_net,
                realized_adverse_eur=_d(trade.get("realized_adverse")),
                adverse_bps_proxy=adv_bps,
                markout_5s_bps=adv_bps,  # proxy; export lacks per-trade horizon join
                fill_type_observed=fill_obs,
                won=realized_net > 0,
            )
        )
    return events


def simulator_fingerprint(path: Path | str) -> dict[str, Any]:
    """Frozen equality surface for fill/PnL assumptions (toxicity must not change)."""
    data = load_paper_state(path)
    trades = list((data.get("tracker") or {}).get("trades") or [])
    fills = data.get("fills") or {}
    fill_types: list[str] = []
    if isinstance(fills, dict):
        for fill in fills.values():
            if isinstance(fill, dict):
                fill_types.append(str((fill.get("extra") or {}).get("fill_type") or ""))
    nets = [_d(t.get("realized_net_profit")) for t in trades]
    return {
        "trade_count": len(trades),
        "completed_round_trips": len(trades),
        "fill_count": len(fills) if isinstance(fills, dict) else 0,
        "fill_types_sorted": sorted(fill_types),
        "realized_net_sum": str(sum(nets, _ZERO)),
        "realized_net_per_trade": [str(n) for n in nets],
        "opportunity_ids": [str(t.get("opportunity_id")) for t in trades],
        "fees_sum": str(sum((_d(t.get("realized_fees") or t.get("fees")) for t in trades), _ZERO)),
        "adverse_sum": str(sum((_d(t.get("realized_adverse")) for t in trades), _ZERO)),
        "slippage_sum": str(
            sum((_d(t.get("realized_slippage") or t.get("slippage")) for t in trades), _ZERO)
        ),
        "gross_sum": str(sum((_d(t.get("expected_gross")) for t in trades), _ZERO)),
    }

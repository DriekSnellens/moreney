"""Live fill/hedge outcome classification. Never fabricate a fill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.shadow_validation.books import CompactL1, L1View
from bot.research.shadow_validation.economics import (
    ExpectedEconomics,
    execution_gap,
    identities_hold,
    market_gap,
    prediction_gap,
    realized_market_net,
    shadow_execution_net,
    total_gap,
)
from bot.research.shadow_validation.protocol import (
    HEDGE_WORSE_BPS,
    MAX_DECISION_STALE_MS,
    NOTIONAL_EUR,
    OUTCOMES,
)

NO_FILL = "NO_FILL"
FULL_FILL = "FULL_FILL"
PARTIAL_FILL = "PARTIAL_FILL"
STALE = "STALE"
QUOTE_DISAPPEARED = "QUOTE_DISAPPEARED"
FOLLOWER_UNAVAILABLE = "FOLLOWER_UNAVAILABLE"
HEDGE_WORSENED = "HEDGE_WORSENED"
DATA_INVALID = "DATA_INVALID"

FILL_OUTCOMES = frozenset({FULL_FILL, PARTIAL_FILL, HEDGE_WORSENED})
NO_FABRICATE_OUTCOMES = frozenset(
    {NO_FILL, STALE, QUOTE_DISAPPEARED, FOLLOWER_UNAVAILABLE, DATA_INVALID}
)


@dataclass(slots=True)
class ObservationResult:
    candidate_id: str
    strategy_fingerprint: str
    outcome: str
    fill_fraction: float
    shadow_fill: bool
    shadow_partial_fill: bool
    shadow_fill_price: float | None
    shadow_hedge_price: float | None
    shadow_execution_net: float
    expected_net: float
    execution_gap: float
    realized_market_net: float | None
    prediction_gap: float
    market_gap: float | None
    total_gap: float | None
    identities_ok: bool
    quote_survival: bool
    follower_availability: bool
    hedge_deterioration_bps: float | None
    adverse_selection_bps: float | None
    markout: float | None
    markouts_bps: dict[str, float | None]
    future_mid: float | None
    future_bid: float | None
    future_ask: float | None
    book_survival: bool
    traded_through: bool
    duration_until_invalidation_ms: float | None
    symbol: str
    record: dict[str, Any]


def _side_available(decision: CompactL1, later: CompactL1, *, side: str) -> bool:
    if side == "BUY":
        return later.ask > 0.0 and later.ask <= decision.ask + 1e-12
    return later.bid > 0.0 and later.bid >= decision.bid - 1e-12


def _traded_through(decision: CompactL1, later: CompactL1, *, side: str) -> bool:
    if side == "BUY":
        return later.bid >= decision.ask - 1e-12
    return later.ask <= decision.bid + 1e-12


def _fill_price(decision: CompactL1, later: CompactL1, *, side: str) -> float:
    if side == "BUY":
        return min(decision.ask, later.ask)
    return max(decision.bid, later.bid)


def _depth_fraction(l1: CompactL1, *, side: str, notional: float) -> float:
    if notional <= 0.0:
        return 0.0
    if side == "BUY":
        available = l1.ask_size * l1.ask
    else:
        available = l1.bid_size * l1.bid
    if available <= 0.0:
        return 0.0
    return max(0.0, min(1.0, available / notional))


def _hedge_deterioration_bps(decision: CompactL1, later: CompactL1, *, side: str) -> float:
    if decision.mid <= 0.0:
        return 0.0
    if side == "BUY":
        return (later.ask - decision.ask) / decision.mid * 10000.0
    return (decision.bid - later.bid) / decision.mid * 10000.0


def _captured_edge(
    *,
    a_rich: bool,
    fill_entry: float,
    fill_hedge: float,
    decision_mid_a: float,
) -> float:
    if decision_mid_a <= 0.0:
        return 0.0
    # A rich: sell A (fill_entry=bid_a), buy B (fill_hedge=ask_b) → locked spread.
    if a_rich:
        return (fill_entry - fill_hedge) / decision_mid_a
    return (fill_hedge - fill_entry) / decision_mid_a


def _as_view(value: CompactL1 | L1View | None) -> L1View:
    if value is None:
        return L1View("MISSING", None)
    if isinstance(value, L1View):
        return value
    return L1View("OK", value)


def classify_observation(
    *,
    candidate_id: str,
    strategy_fingerprint: str,
    signal_time_ms: float,
    now_ms: float,
    a_rich: bool,
    entry_side: str,
    hedge_side: str,
    decision_entry: CompactL1,
    decision_hedge: CompactL1,
    later_entry: CompactL1 | L1View | None,
    later_hedge: CompactL1 | L1View | None,
    future_entry: CompactL1 | L1View | None,
    expected: ExpectedEconomics,
    decision_book_age_ms: float,
    identity: dict[str, Any] | None = None,
    symbol: str = "",
    markouts_fraction: dict[str, float | None] | None = None,
) -> ObservationResult:
    """Classify one completed shadow candidate. Missing data → DATA_INVALID."""

    def _finish(
        outcome: str,
        *,
        fill_fraction: float = 0.0,
        fill_px: float | None = None,
        hedge_px: float | None = None,
        extra_adverse_bps: float = 0.0,
        captured: float = 0.0,
        quote_survival: bool = False,
        follower_availability: bool = False,
        hedge_det: float | None = None,
        traded_through: bool = False,
        invalidation_ms: float | None = None,
    ) -> ObservationResult:
        if outcome not in OUTCOMES:
            raise RuntimeError(f"unknown outcome {outcome}")
        fabricate = outcome in NO_FABRICATE_OUTCOMES
        if fabricate:
            fill_fraction = 0.0
            fill_px = None
            hedge_px = None
            captured = 0.0
        shadow = shadow_execution_net(
            fill_fraction=0.0 if fabricate else fill_fraction,
            captured_edge_fraction=0.0 if fabricate else captured,
            extra_adverse_bps=0.0 if fabricate else extra_adverse_bps,
        )
        is_fill = (not fabricate) and fill_fraction > 0.0
        future_l1 = _as_view(future_entry).l1
        future_mid = future_l1.mid if future_l1 is not None else None
        future_bid = future_l1.bid if future_l1 is not None else None
        future_ask = future_l1.ask if future_l1 is not None else None
        markout = None
        adverse_bps = None
        if future_l1 is not None and decision_entry.mid > 0.0:
            # Signed so positive = dislocation-favourable move of entry mid.
            raw = (future_l1.mid - decision_entry.mid) / decision_entry.mid
            signed = -raw if a_rich else raw
            markout = signed
            adverse_bps = max(0.0, -signed) * 10000.0
        extras = markouts_fraction or {}
        if markout is not None:
            extras = {"5s": markout, **{k: v for k, v in extras.items() if k != "5s"}}
        markouts_bps = {
            k: (None if v is None else float(v) * 10000.0)
            for k, v in (("1s", extras.get("1s")), ("5s", extras.get("5s") if extras.get("5s") is not None else markout), ("30s", extras.get("30s")), ("60s", extras.get("60s")))
        }
        if markouts_bps["5s"] is None and adverse_bps is not None:
            markouts_bps["5s"] = (markout * 10000.0) if markout is not None else None
        real_net = realized_market_net(signed_markout_fraction=markout)
        pred = prediction_gap(shadow["shadow_execution_net"], expected.expected_net)
        mkt = market_gap(real_net, shadow["shadow_execution_net"])
        tot = total_gap(real_net, expected.expected_net)
        ident_ok = identities_hold(
            expected_net=expected.expected_net,
            shadow_execution_net_eur=shadow["shadow_execution_net"],
            realized_market_net_eur=real_net,
            prediction_gap_eur=pred,
            market_gap_eur=mkt,
            total_gap_eur=tot,
            shadow_legs=shadow,
            expected=expected,
        )
        gap = pred
        ident = identity or {}
        book_survival = _as_view(later_entry).ok and _as_view(later_hedge).ok
        record = {
            "candidate_id": candidate_id,
            "strategy_fingerprint": ident.get("strategy_fingerprint") or strategy_fingerprint,
            "config_hash": ident.get("config_hash"),
            "runtime_id": ident.get("runtime_id"),
            "git_commit": ident.get("git_commit"),
            "validation_run_id": ident.get("validation_run_id"),
            "signal_time_ms": signal_time_ms,
            "symbol": symbol or decision_entry.symbol,
            "A_SIGNAL": {
                "label": "A_SIGNAL",
                "a_rich": a_rich,
                "entry_side": entry_side,
                "hedge_side": hedge_side,
                "decision_bid": decision_entry.bid,
                "decision_ask": decision_entry.ask,
                "not_a_fill": True,
            },
            "B_EXPECTED_ECONOMICS": expected.as_dict(),
            "C_SHADOW_EXECUTION": {
                "label": "C_SHADOW_EXECUTION",
                "outcome": outcome,
                "shadow_fill": is_fill and fill_fraction >= 1.0 - 1e-12,
                "shadow_partial_fill": is_fill and 0.0 < fill_fraction < 1.0,
                "shadow_fill_price": fill_px,
                "shadow_hedge_price": hedge_px,
                "shadow_execution_net": shadow["shadow_execution_net"],
                "fill_fraction": shadow["fill_fraction"],
                "not_expected_net": True,
                "not_realized_markout": True,
                "not_profit": True,
            },
            "D_REALIZED_MARKET_OUTCOME": {
                "label": "D_REALIZED_MARKET_OUTCOME",
                "future_mid": future_mid,
                "future_bid": future_bid,
                "future_ask": future_ask,
                "markout": markout,
                "realized_market_net": real_net,
                "markouts_bps": markouts_bps,
                "book_survival": book_survival,
                "quote_survival": quote_survival,
                "follower_availability": follower_availability,
                "adverse_selection_bps": adverse_bps,
                "hedge_deterioration_bps": hedge_det,
                "traded_through": traded_through,
                "not_shadow_execution_net": True,
                "not_a_fill": True,
                "not_profit": True,
            },
            "prediction_gap": pred,
            "execution_gap": gap,
            "market_gap": mkt,
            "total_gap": tot,
            "accounting_identities_ok": ident_ok,
            "outcome": outcome,
        }
        return ObservationResult(
            candidate_id=candidate_id,
            strategy_fingerprint=str(ident.get("strategy_fingerprint") or strategy_fingerprint),
            outcome=outcome,
            fill_fraction=shadow["fill_fraction"],
            shadow_fill=is_fill and fill_fraction >= 1.0 - 1e-12,
            shadow_partial_fill=is_fill and 0.0 < fill_fraction < 1.0,
            shadow_fill_price=fill_px,
            shadow_hedge_price=hedge_px,
            shadow_execution_net=shadow["shadow_execution_net"],
            expected_net=expected.expected_net,
            execution_gap=gap,
            realized_market_net=real_net,
            prediction_gap=pred,
            market_gap=mkt,
            total_gap=tot,
            identities_ok=ident_ok,
            quote_survival=quote_survival,
            follower_availability=follower_availability,
            hedge_deterioration_bps=hedge_det,
            adverse_selection_bps=adverse_bps,
            markout=markout,
            markouts_bps=markouts_bps,
            future_mid=future_mid,
            future_bid=future_bid,
            future_ask=future_ask,
            book_survival=book_survival,
            traded_through=traded_through,
            duration_until_invalidation_ms=invalidation_ms,
            symbol=symbol or decision_entry.symbol,
            record=record,
        )

    if decision_book_age_ms > MAX_DECISION_STALE_MS:
        return _finish(STALE, invalidation_ms=0.0)

    entry_view = _as_view(later_entry)
    hedge_view = _as_view(later_hedge)
    if entry_view.status in {"MISSING", "INVALID"}:
        return _finish(DATA_INVALID)
    if entry_view.status == "EMPTY" or entry_view.l1 is None:
        return _finish(
            QUOTE_DISAPPEARED,
            invalidation_ms=max(0.0, now_ms - signal_time_ms),
        )
    later_entry_l1 = entry_view.l1

    traded = _traded_through(decision_entry, later_entry_l1, side=entry_side)
    # Availability only: a later crossed book is not evidence we filled.
    # Do not fabricate fills from post-move tape.
    available = _side_available(decision_entry, later_entry_l1, side=entry_side)
    if not available:
        return _finish(
            NO_FILL,
            traded_through=False,
            invalidation_ms=max(0.0, now_ms - signal_time_ms),
        )

    if hedge_view.status in {"MISSING", "INVALID"}:
        return _finish(DATA_INVALID)
    if hedge_view.status == "EMPTY" or hedge_view.l1 is None:
        return _finish(
            FOLLOWER_UNAVAILABLE,
            quote_survival=True,
            follower_availability=False,
            traded_through=traded,
            invalidation_ms=max(0.0, now_ms - signal_time_ms),
        )
    later_hedge_l1 = hedge_view.l1

    hedge_det = _hedge_deterioration_bps(decision_hedge, later_hedge_l1, side=hedge_side)
    fill_px = _fill_price(decision_entry, later_entry_l1, side=entry_side)
    hedge_px = _fill_price(decision_hedge, later_hedge_l1, side=hedge_side)
    entry_frac = _depth_fraction(later_entry_l1, side=entry_side, notional=NOTIONAL_EUR)
    hedge_frac = _depth_fraction(later_hedge_l1, side=hedge_side, notional=NOTIONAL_EUR)
    fill_fraction = min(entry_frac, hedge_frac)
    captured = _captured_edge(
        a_rich=a_rich,
        fill_entry=fill_px,
        fill_hedge=hedge_px,
        decision_mid_a=decision_entry.mid,
    )
    extra_adv = max(0.0, hedge_det) if hedge_det > 0.0 else 0.0

    if fill_fraction <= 0.0:
        return _finish(
            NO_FILL,
            quote_survival=True,
            follower_availability=True,
            hedge_det=hedge_det,
            traded_through=traded,
            invalidation_ms=max(0.0, now_ms - signal_time_ms),
        )

    if hedge_det > HEDGE_WORSE_BPS:
        return _finish(
            HEDGE_WORSENED,
            fill_fraction=fill_fraction,
            fill_px=fill_px,
            hedge_px=hedge_px,
            extra_adverse_bps=extra_adv,
            captured=captured,
            quote_survival=True,
            follower_availability=True,
            hedge_det=hedge_det,
            traded_through=traded,
        )

    outcome = FULL_FILL if fill_fraction >= 1.0 - 1e-12 else PARTIAL_FILL
    return _finish(
        outcome,
        fill_fraction=fill_fraction,
        fill_px=fill_px,
        hedge_px=hedge_px,
        extra_adverse_bps=0.0,
        captured=captured,
        quote_survival=True,
        follower_availability=True,
        hedge_det=hedge_det,
        traded_through=traded,
    )

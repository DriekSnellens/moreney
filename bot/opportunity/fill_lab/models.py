"""Experimental fill models — never affect production execution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Sequence

from bot.opportunity.fill_lab.audit import SupportLevel
from bot.opportunity.fill_lab.events import FillEvent, QuoteEvent

_ZERO = Decimal("0")

# Predeclared persistence grid — do not tune on OOS.
PERSISTENCE_MS_GRID: tuple[int, ...] = (100, 250, 500, 1000)


class FillModelId(StrEnum):
    TRADE_THROUGH_ONLY = "TRADE_THROUGH_ONLY"
    TOUCH_ONLY = "TOUCH_ONLY"
    TOUCH_PERSISTENCE_100 = "TOUCH_PERSISTENCE_100"
    TOUCH_PERSISTENCE_250 = "TOUCH_PERSISTENCE_250"
    TOUCH_PERSISTENCE_500 = "TOUCH_PERSISTENCE_500"
    TOUCH_PERSISTENCE_1000 = "TOUCH_PERSISTENCE_1000"
    DEPTH_CONSUMPTION = "DEPTH_CONSUMPTION"


@dataclass(frozen=True, slots=True)
class BookPoint:
    """One causally ordered market observation AFTER quote.t0."""

    timestamp_ms: float
    bid: Decimal
    ask: Decimal
    mid: Decimal
    bid_size: Decimal = _ZERO
    ask_size: Decimal = _ZERO
    traded_volume_since_quote: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class FillModelResult:
    model_id: str
    support: str
    status: str  # CONSERVATIVE_BASELINE | EXPERIMENTAL_COUNTERFACTUAL | UNSUPPORTED
    fills: list[FillEvent]
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "support": self.support,
            "status": self.status,
            "sample_count": len(self.fills),
            "notes": list(self.notes),
            "fills": [f.as_dict() for f in self.fills],
        }


def _unsupported(model_id: FillModelId, why: str) -> FillModelResult:
    return FillModelResult(
        model_id=model_id.value,
        support=SupportLevel.UNSUPPORTED.value,
        status="UNSUPPORTED",
        fills=[],
        notes=(why,),
    )


def run_trade_through_baseline(fills: Sequence[FillEvent]) -> FillModelResult:
    """Production-equivalent observed fills (already classified trade_through)."""
    tt = [f for f in fills if f.fill_type == "trade_through" or f.model_id == FillModelId.TRADE_THROUGH_ONLY.value]
    # Prefer explicit baseline tags
    baseline = [f for f in fills if f.model_id == FillModelId.TRADE_THROUGH_ONLY.value] or tt
    return FillModelResult(
        model_id=FillModelId.TRADE_THROUGH_ONLY.value,
        support=SupportLevel.SUPPORTED.value,
        status="CONSERVATIVE_BASELINE",
        fills=list(baseline),
        notes=("Observed paper fills under production trade-through matching.",),
    )


def run_touch_only(
    quotes: Sequence[QuoteEvent],
    books_after: dict[str, Sequence[BookPoint]],
    *,
    supported: bool,
) -> FillModelResult:
    if not supported:
        return _unsupported(
            FillModelId.TOUCH_ONLY,
            "No post-quote book path; touch eligibility not observable.",
        )
    out: list[FillEvent] = []
    for q in quotes:
        series = books_after.get(q.quote_id) or ()
        for pt in series:
            if pt.timestamp_ms < q.timestamp_ms:
                continue  # causal guard
            # At-touch eligibility (not trade-through): bid joins our buy / ask joins our sell.
            touched = (
                (q.side == "buy" and pt.bid >= q.price)
                or (q.side == "sell" and pt.ask <= q.price)
            )
            if not touched:
                continue
            out.append(
                FillEvent(
                    fill_id=f"touch:{q.quote_id}:{int(pt.timestamp_ms)}",
                    quote_id=q.quote_id,
                    opportunity_id=q.opportunity_id,
                    fill_type="touch_only",
                    fill_timestamp_ms=pt.timestamp_ms,
                    fill_price=q.price,
                    quantity=q.quantity,
                    symbol=q.symbol,
                    side=q.side,
                    venue=q.venue,
                    quote_age_ms=pt.timestamp_ms - q.timestamp_ms,
                    model_id=FillModelId.TOUCH_ONLY.value,
                    observational=True,
                    market_state_at_fill={
                        "bid": str(pt.bid),
                        "ask": str(pt.ask),
                        "mid": str(pt.mid),
                    },
                    notes=("EXPERIMENTAL: touch eligibility ≠ automatic fill",),
                )
            )
            break  # first touch only
    return FillModelResult(
        model_id=FillModelId.TOUCH_ONLY.value,
        support=SupportLevel.SUPPORTED.value,
        status="EXPERIMENTAL_COUNTERFACTUAL",
        fills=out,
        notes=("Observational touch eligibility; not live-equivalent.",),
    )


def run_touch_persistence(
    quotes: Sequence[QuoteEvent],
    books_after: dict[str, Sequence[BookPoint]],
    *,
    persistence_ms: int,
    supported: bool,
) -> FillModelResult:
    model = FillModelId(f"TOUCH_PERSISTENCE_{persistence_ms}")
    if not supported:
        return _unsupported(
            model,
            f"No book path for persistence={persistence_ms}ms evaluation.",
        )
    out: list[FillEvent] = []
    for q in quotes:
        series = [p for p in (books_after.get(q.quote_id) or ()) if p.timestamp_ms >= q.timestamp_ms]
        if not series:
            continue
        touch_start: float | None = None
        for pt in series:
            touching = (
                (q.side == "buy" and pt.bid >= q.price)
                or (q.side == "sell" and pt.ask <= q.price)
            )
            if touching:
                if touch_start is None:
                    touch_start = pt.timestamp_ms
                elif pt.timestamp_ms - touch_start >= persistence_ms:
                    out.append(
                        FillEvent(
                            fill_id=f"persist{persistence_ms}:{q.quote_id}:{int(pt.timestamp_ms)}",
                            quote_id=q.quote_id,
                            opportunity_id=q.opportunity_id,
                            fill_type=f"touch_persistence_{persistence_ms}",
                            fill_timestamp_ms=pt.timestamp_ms,
                            fill_price=q.price,
                            quantity=q.quantity,
                            symbol=q.symbol,
                            side=q.side,
                            venue=q.venue,
                            quote_age_ms=pt.timestamp_ms - q.timestamp_ms,
                            model_id=model.value,
                            observational=True,
                            market_state_at_fill={
                                "bid": str(pt.bid),
                                "ask": str(pt.ask),
                                "mid": str(pt.mid),
                                "persistence_ms": persistence_ms,
                            },
                            notes=("EXPERIMENTAL counterfactual fill eligibility",),
                        )
                    )
                    break
            else:
                touch_start = None
    return FillModelResult(
        model_id=model.value,
        support=SupportLevel.SUPPORTED.value,
        status="EXPERIMENTAL_COUNTERFACTUAL",
        fills=out,
        notes=(f"Predeclared persistence grid value {persistence_ms}ms.",),
    )


def run_depth_consumption(
    quotes: Sequence[QuoteEvent],
    books_after: dict[str, Sequence[BookPoint]],
    *,
    supported: bool,
    queue_position_known: bool = False,
) -> FillModelResult:
    """Depth consumption is UNSUPPORTED unless queue position is known honestly.

    Displayed size + traded volume alone is not enough to invent queue priority.
    """
    _ = (quotes, books_after, supported)  # retained for API symmetry / future data
    if not queue_position_known:
        return _unsupported(
            FillModelId.DEPTH_CONSUMPTION,
            "Queue position cannot be estimated honestly; depth model does not fabricate fills.",
        )
    return _unsupported(
        FillModelId.DEPTH_CONSUMPTION,
        "Queue metadata present but depth-consumption replay not implemented without fabrication.",
    )

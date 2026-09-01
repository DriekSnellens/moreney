"""QuoteEvent / FillEvent — separate quote generation from fill determination."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class QuoteEvent:
    """Information available when a quote is posted (decision-time)."""

    quote_id: str
    opportunity_id: str
    timestamp_ms: float
    symbol: str
    side: str
    venue: str
    price: Decimal
    quantity: Decimal
    strategy: str
    post_only: bool = True
    route: str = ""
    expected_net_eur: Decimal = _ZERO
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = str(v)
        return d


@dataclass(frozen=True, slots=True)
class FillEvent:
    """Fill (or experimental eligibility) after a quote was already posted."""

    fill_id: str
    quote_id: str
    opportunity_id: str
    fill_type: str
    fill_timestamp_ms: float
    fill_price: Decimal
    quantity: Decimal
    symbol: str
    side: str
    venue: str
    quote_age_ms: float
    model_id: str
    observational: bool  # True = experimental counterfactual
    markout_1s_bps: Decimal | None = None
    markout_5s_bps: Decimal | None = None
    markout_30s_bps: Decimal | None = None
    markout_60s_bps: Decimal | None = None
    realized_net_eur: Decimal | None = None
    fees_eur: Decimal | None = None
    capital_lock_ms: float | None = None
    market_state_at_fill: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = str(v)
        d["notes"] = list(self.notes)
        return d

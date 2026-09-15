"""Deterministic market-data replay with causal visibility."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from bot.market_data.research.ordering import sort_events
from bot.market_data.research.schema import ResearchMarketEvent


@dataclass
class ReplayCursor:
    """At time t, only events with clock <= t are visible."""

    events: list[ResearchMarketEvent]
    index: int = 0  # next event to release
    visible: list[ResearchMarketEvent] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.index >= len(self.events)


class MarketDataReplayEngine:
    """Replay recorded research events deterministically.

    Modes:
      - event_by_event: advance one event at a time
      - until_ns(t): release all events with effective clock <= t
    """

    def __init__(self, events: Sequence[ResearchMarketEvent]) -> None:
        self._events = sort_events(events)
        self._cursor = ReplayCursor(events=list(self._events))

    def reset(self) -> None:
        self._cursor = ReplayCursor(events=list(self._events))

    def _clock(self, ev: ResearchMarketEvent) -> int:
        # Causal clock: prefer exchange_ts; else received_ts (explicitly degraded)
        if ev.exchange_ts_available and ev.exchange_ts_ns is not None:
            return ev.exchange_ts_ns
        return ev.received_ts_ns

    def step(self) -> ResearchMarketEvent | None:
        if self._cursor.done:
            return None
        ev = self._cursor.events[self._cursor.index]
        self._cursor.index += 1
        self._cursor.visible.append(ev)
        return ev

    def until_ns(self, t_ns: int) -> list[ResearchMarketEvent]:
        released: list[ResearchMarketEvent] = []
        while not self._cursor.done:
            nxt = self._cursor.events[self._cursor.index]
            if self._clock(nxt) > t_ns:
                break
            released.append(self.step())  # type: ignore[arg-type]
        return released

    def visible_at(self, t_ns: int) -> list[ResearchMarketEvent]:
        """Events a strategy may access at t — never future."""
        return [e for e in self._events if self._clock(e) <= t_ns]

    def iter_event_by_event(self) -> Iterator[ResearchMarketEvent]:
        self.reset()
        while True:
            ev = self.step()
            if ev is None:
                break
            yield ev

    def fingerprint(self) -> str:
        payload = [
            {
                "id": e.event_id,
                "venue": e.venue,
                "symbol": e.symbol,
                "ex_ts": e.exchange_ts_ns,
                "recv": e.received_ts_ns,
                "seq": e.sequence_number,
                "bid": str(e.bid_price) if e.bid_price is not None else None,
                "ask": str(e.ask_price) if e.ask_price is not None else None,
            }
            for e in self._events
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def outcome_available(
        self,
        *,
        decision_ts_ns: int,
        horizon_ms: float,
        now_ns: int,
    ) -> bool:
        """Delayed labels only after predeclared horizon."""
        return now_ns >= decision_ts_ns + int(horizon_ms * 1_000_000)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_events": len(self._events),
            "fingerprint": self.fingerprint(),
            "index": self._cursor.index,
            "visible": len(self._cursor.visible),
        }

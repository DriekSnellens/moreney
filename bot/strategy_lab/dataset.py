"""Research dataset loading + synthetic causal tape for lab tournaments.

Real tape preferred. Synthetic tape is explicitly labeled SYNTHETIC and never
claimed as OBSERVED market data.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from bot.market_data.research.replay import MarketDataReplayEngine
from bot.market_data.research.schema import DepthLevel, ResearchMarketEvent
from bot.strategy_lab.types import CycleSnapshot, MarketEventView

_ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    return Decimal(str(v))


def event_to_view(ev: ResearchMarketEvent) -> MarketEventView:
    bid_levels = tuple(
        (_d(lvl.price), _d(lvl.quantity)) for lvl in (ev.bid_levels or ())
    )
    ask_levels = tuple(
        (_d(lvl.price), _d(lvl.quantity)) for lvl in (ev.ask_levels or ())
    )
    bid = _d(ev.bid_price) if ev.bid_price is not None else (
        bid_levels[0][0] if bid_levels else _ZERO
    )
    ask = _d(ev.ask_price) if ev.ask_price is not None else (
        ask_levels[0][0] if ask_levels else _ZERO
    )
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else None
    ts = ev.exchange_ts_ns if ev.exchange_ts_available and ev.exchange_ts_ns else ev.received_ts_ns
    return MarketEventView(
        event_id=ev.event_id,
        ts_ns=int(ts),
        venue=str(ev.venue).lower(),
        symbol=str(ev.symbol).upper(),
        bid=bid,
        ask=ask,
        bid_size=_d(ev.bid_size or 0),
        ask_size=_d(ev.ask_size or 0),
        bid_levels=bid_levels,
        ask_levels=ask_levels,
        sequence=ev.sequence_number,
        exchange_ts_ns=ev.exchange_ts_ns,
        received_ts_ns=ev.received_ts_ns,
        mid=mid,
    )


def iter_research_events(
    path: Path,
    *,
    max_events: int | None = None,
    stride: int = 1,
    venues: tuple[str, ...] | None = ("binance", "bitvavo", "okx"),
    symbol_suffix: str | None = "EUR",
) -> Iterator[ResearchMarketEvent]:
    """Stream JSONL research events without loading the full tape into RAM.

    Filters match the gated tournament index defaults (EUR × core venues)
    so strategy-lab OBSERVED runs stay comparable and memory-safe.
    """
    if not path.exists():
        return
    files = sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]
    venue_set = {v.lower() for v in venues} if venues else None
    stride = max(1, int(stride))
    seen = 0
    kept = 0
    for fp in files:
        with fp.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                seen += 1
                if stride > 1 and (seen % stride) != 0:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                venue = str(raw.get("venue") or "").lower()
                if venue_set is not None and venue not in venue_set:
                    continue
                symbol = str(raw.get("symbol") or "")
                if symbol_suffix and not symbol.endswith(symbol_suffix):
                    continue
                yield _parse_event(raw)
                kept += 1
                if max_events is not None and kept >= max_events:
                    return


def load_research_events(
    path: Path,
    *,
    max_events: int | None = None,
    stride: int = 1,
    venues: tuple[str, ...] | None = ("binance", "bitvavo", "okx"),
    symbol_suffix: str | None = "EUR",
) -> list[ResearchMarketEvent]:
    """Load a bounded, streamed sample of research events (never slurps multi-GB tapes)."""
    return list(
        iter_research_events(
            path,
            max_events=max_events,
            stride=stride,
            venues=venues,
            symbol_suffix=symbol_suffix,
        )
    )


def _parse_event(raw: dict[str, Any]) -> ResearchMarketEvent:
    def _lvls(key: str) -> tuple[DepthLevel, ...]:
        out = []
        for lvl in raw.get(key) or []:
            out.append(DepthLevel(price=_d(lvl["price"]), quantity=_d(lvl["quantity"])))
        return tuple(out)

    return ResearchMarketEvent(
        schema_version=str(raw.get("schema_version") or "research_md_v1"),
        event_id=str(raw["event_id"]),
        venue=str(raw["venue"]),
        symbol=str(raw["symbol"]),
        channel=str(raw.get("channel") or "book_snapshot"),
        exchange_ts_ns=raw.get("exchange_ts_ns"),
        received_ts_ns=int(raw["received_ts_ns"]),
        local_monotonic_ns=int(raw.get("local_monotonic_ns") or raw["received_ts_ns"]),
        sequence_number=raw.get("sequence_number"),
        bid_price=_d(raw["bid_price"]) if raw.get("bid_price") is not None else None,
        bid_size=_d(raw["bid_size"]) if raw.get("bid_size") is not None else None,
        ask_price=_d(raw["ask_price"]) if raw.get("ask_price") is not None else None,
        ask_size=_d(raw["ask_size"]) if raw.get("ask_size") is not None else None,
        bid_levels=_lvls("bid_levels"),
        ask_levels=_lvls("ask_levels"),
        exchange_ts_available=bool(raw.get("exchange_ts_available")),
        timestamp_quality=str(raw.get("timestamp_quality") or "UNSUPPORTED"),
        is_snapshot=bool(raw.get("is_snapshot", True)),
    )


def build_cycles_from_events(
    events: list[ResearchMarketEvent],
    *,
    bucket_ms: int = 500,
    label: str = "FULL",
) -> list[CycleSnapshot]:
    """Bucket concurrent venue books into cycles (causal: by clock)."""
    if not events:
        return []
    engine = MarketDataReplayEngine(events)
    views = [event_to_view(e) for e in engine._events]  # noqa: SLF001 — sorted
    buckets: dict[int, list[MarketEventView]] = {}
    bucket_ns = bucket_ms * 1_000_000
    for v in views:
        b = (v.ts_ns // bucket_ns) * bucket_ns
        # Keep latest per venue|symbol in bucket
        key_map = {f"{x.venue}|{x.symbol}": x for x in buckets.get(b, [])}
        key_map[f"{v.venue}|{v.symbol}"] = v
        buckets[b] = list(key_map.values())
    cycles: list[CycleSnapshot] = []
    for i, ts in enumerate(sorted(buckets)):
        books = tuple(sorted(buckets[ts], key=lambda x: (x.symbol, x.venue)))
        cycles.append(
            CycleSnapshot(
                cycle_id=f"c{i}_{ts}",
                ts_ns=ts,
                books=books,
                label=label,
            )
        )
    return cycles


def chronological_split(
    cycles: list[CycleSnapshot],
    *,
    development_frac: float = 0.70,
) -> tuple[list[CycleSnapshot], list[CycleSnapshot]]:
    """Strict chronological DEV → OOS. Never shuffle."""
    if not cycles:
        return [], []
    cut = max(1, int(len(cycles) * development_frac))
    cut = min(cut, len(cycles) - 1) if len(cycles) > 1 else len(cycles)
    dev = [
        CycleSnapshot(c.cycle_id, c.ts_ns, c.books, label="DEVELOPMENT")
        for c in cycles[:cut]
    ]
    oos = [
        CycleSnapshot(c.cycle_id, c.ts_ns, c.books, label="OOS")
        for c in cycles[cut:]
    ]
    return dev, oos


def synthetic_research_tape(
    *,
    n_cycles: int = 80,
    seed: int = 42,
) -> list[ResearchMarketEvent]:
    """Deterministic multi-venue tape for lab plumbing when real tape is thin.

    Label: SYNTHETIC — not observed market data.
    """
    venues = ["binance", "okx", "bitvavo"]
    symbols = ["BTCEUR", "ETHEUR", "ATOMEUR"]
    mids = {
        "BTCEUR": Decimal("100000"),
        "ETHEUR": Decimal("3500"),
        "ATOMEUR": Decimal("4.2"),
    }
    events: list[ResearchMarketEvent] = []
    base_ns = 1_700_000_000_000_000_000
    step_ns = 200_000_000  # 200ms
    for i in range(n_cycles):
        # Pseudo-random but deterministic skew from seed+i
        wobble = Decimal(((seed * 17 + i * 13) % 97) - 48) / Decimal("10")
        for vi, venue in enumerate(venues):
            venue_skew = Decimal(vi - 1) * Decimal("8")  # create cross-venue gaps
            for si, symbol in enumerate(symbols):
                mid = mids[symbol] + wobble * (Decimal("1") if mid_scale(symbol) else Decimal("0.01"))
                half = mid * Decimal("0.0012")  # ~24 bps same-venue
                if symbol == "ATOMEUR":
                    half = mid * Decimal("0.0020")
                bid = mid + venue_skew * (mid / Decimal("100000")) - half
                ask = mid + venue_skew * (mid / Decimal("100000")) + half
                # Occasional executable dislocation: binance cheap, bitvavo rich
                if venue == "binance":
                    bid -= mid * Decimal("0.0003")
                    ask -= mid * Decimal("0.0003")
                if venue == "bitvavo":
                    bid += mid * Decimal("0.00035")
                    ask += mid * Decimal("0.00035")
                depth = Decimal("5")
                ts = base_ns + i * step_ns + vi * 1_000_000
                eid = hashlib.sha1(f"{seed}:{i}:{venue}:{symbol}".encode()).hexdigest()[:16]
                events.append(
                    ResearchMarketEvent(
                        schema_version="research_md_v1",
                        event_id=eid,
                        venue=venue,
                        symbol=symbol,
                        channel="book_snapshot",
                        exchange_ts_ns=ts,
                        received_ts_ns=ts + 5_000_000,
                        local_monotonic_ns=ts,
                        sequence_number=i * 10 + vi,
                        bid_price=bid,
                        bid_size=depth,
                        ask_price=ask,
                        ask_size=depth,
                        bid_levels=(
                            DepthLevel(price=bid, quantity=depth),
                            DepthLevel(price=bid - half / 2, quantity=depth),
                        ),
                        ask_levels=(
                            DepthLevel(price=ask, quantity=depth),
                            DepthLevel(price=ask + half / 2, quantity=depth),
                        ),
                        exchange_ts_available=True,
                        timestamp_quality="MEDIUM",
                        is_snapshot=True,
                        notes=("SYNTHETIC",),
                    )
                )
    return events


def mid_scale(symbol: str) -> bool:
    return symbol in {"BTCEUR", "ETHEUR"}


def dataset_fingerprint(cycles: list[CycleSnapshot]) -> str:
    payload = [
        {
            "id": c.cycle_id,
            "ts": c.ts_ns,
            "books": [
                {
                    "v": b.venue,
                    "s": b.symbol,
                    "bid": str(b.bid),
                    "ask": str(b.ask),
                }
                for b in c.books
            ],
        }
        for c in cycles
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def iter_baseline_opportunity_keys(cycle: CycleSnapshot) -> Iterator[str]:
    """Universe of symbol|buy_venue|sell_venue pairs with valid books."""
    by_sym: dict[str, list[MarketEventView]] = {}
    for b in cycle.books:
        if b.bid > 0 and b.ask > 0 and b.ask > b.bid:
            by_sym.setdefault(b.symbol, []).append(b)
    for symbol, venues in by_sym.items():
        for buy in venues:
            for sell in venues:
                yield f"{symbol}|{buy.venue}|{sell.venue}"

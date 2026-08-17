"""Causal pre-trade features. Never use t > decision time."""

from __future__ import annotations

import bisect
from typing import Any

from bot.research.forensics.buckets import (
    DENSITY_LOOKBACK_MS,
    DENSITY_SPARSE,
    VOL_LOOKBACK_MS,
)
from bot.research.tournament.tape_index import TapeIndex, past_return


class TsView:
    __slots__ = ("points", "ts")

    def __init__(self, points: list) -> None:
        self.points = points
        self.ts = [p.ts_ns for p in points]

    def index_at_or_before(self, t: int) -> int | None:
        i = bisect.bisect_right(self.ts, t) - 1
        return i if i >= 0 else None

    def count_in(self, t0: int, t1: int) -> int:
        a = bisect.bisect_left(self.ts, t0)
        b = bisect.bisect_right(self.ts, t1)
        return b - a


def views_for(index: TapeIndex) -> dict[tuple[str, str], TsView]:
    return {k: TsView(v) for k, v in index.series.items()}


def spread_bps(p) -> float | None:
    if p is None or p.mid <= 0:
        return None
    return (p.ask - p.bid) / p.mid * 10000.0


def depth_eur(p) -> float | None:
    if p is None:
        return None
    return (p.bid_size + p.ask_size) * p.mid


def quote_age_ms(ts_ns: int | None, peer_ts_ns: int | None) -> float | None:
    if ts_ns is None or peer_ts_ns is None:
        return None
    if int(peer_ts_ns) > int(ts_ns):
        return None
    return abs(int(ts_ns) - int(peer_ts_ns)) / 1e6


def event_density(view: TsView | None, ts_ns: int) -> int | None:
    if view is None:
        return None
    return view.count_in(ts_ns - DENSITY_LOOKBACK_MS * 1_000_000, ts_ns)


def vol_bps(view: TsView | None, i: int | None) -> float | None:
    if view is None or i is None:
        return None
    r = past_return(view.points, i, VOL_LOOKBACK_MS * 1_000_000)
    if r is None:
        return None
    return abs(r) * 10000.0


def assert_pretrade(event: dict[str, Any], decision_ts: int) -> None:
    ts = event.get("ts_ns")
    peer = event.get("peer_ts_ns")
    if ts is not None and int(ts) > int(decision_ts):
        raise RuntimeError("future timestamp in quote_age/decision")
    if peer is not None and int(peer) > int(decision_ts):
        raise RuntimeError("future peer timestamp leaked into quote_age")


def enrich_pretrade(
    event: dict[str, Any],
    *,
    index: TapeIndex,
    views: dict[tuple[str, str], TsView],
    venue: str,
    peer_venue: str | None,
) -> dict[str, Any]:
    row = dict(event)
    ts = int(event["ts_ns"]) if event.get("ts_ns") is not None else None
    symbol = str(event.get("symbol") or "")
    view = views.get((venue, symbol))
    i = view.index_at_or_before(ts) if view is not None and ts is not None else None
    p = view.points[i] if view is not None and i is not None else None
    peer_ts = event.get("peer_ts_ns")
    if peer_ts is None and peer_venue and ts is not None:
        pv = views.get((peer_venue, symbol))
        if pv is not None:
            j = pv.index_at_or_before(ts)
            if j is not None:
                peer_ts = pv.points[j].ts_ns
                row["peer_ts_ns"] = peer_ts
    age = quote_age_ms(ts, None if peer_ts is None else int(peer_ts))
    if ts is not None:
        assert_pretrade(row, ts)
    ex_ts = p.exchange_ts_ns if p is not None else None
    dens = event_density(view, ts) if ts is not None else None
    mid_hist = past_return(view.points, i, VOL_LOOKBACK_MS * 1_000_000) if view is not None and i is not None else None
    clock = (
        "BITVAVO_EXCHANGE_TS_ABSENT"
        if venue == "bitvavo" and ex_ts is None
        else ("EXCHANGE_TS_PRESENT" if ex_ts is not None else "EXCHANGE_TS_ABSENT")
    )
    sp = spread_bps(p)
    dep = depth_eur(p)
    vb = vol_bps(view, i)
    row.update(
        {
            "venue": venue,
            "symbol": symbol or row.get("symbol"),
            "quote_age_ms": age,
            "spread_bps": sp,
            "spread": sp,
            "depth_eur": dep,
            "depth": dep,
            "event_density": dens,
            "event_rate": dens,
            "event_density_sparse_flag": (
                None if dens is None else dens < DENSITY_SPARSE
            ),
            "vol_bps": vb,
            "volatility": vb,
            "mid_return_history": mid_hist,
            "bid": p.bid if p is not None else None,
            "ask": p.ask if p is not None else None,
            "mid": p.mid if p is not None else None,
            "local_receive_timestamp": ts,
            "exchange_timestamp": ex_ts,
            "exchange_ts_invented": False,
            "bitvavo_exchange_ts_present": (
                bool(ex_ts is not None) if venue == "bitvavo" else None
            ),
            "clock_quality": clock,
            "latency_flag": clock,
        }
    )
    if row.get("dislocation") is not None:
        row["cross_venue_divergence"] = abs(float(row["dislocation"])) * 10000.0
    elif row.get("dev") is not None:
        row["cross_venue_divergence"] = abs(float(row["dev"])) * 10000.0
    return row

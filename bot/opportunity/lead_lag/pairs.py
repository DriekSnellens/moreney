"""Directed same-symbol venue pair universe — no hardcoded leader."""

from __future__ import annotations

from itertools import permutations
from typing import Any


DEFAULT_VENUES: tuple[str, ...] = ("binance", "bitvavo", "okx")


def directed_pairs(venues: tuple[str, ...] | list[str] | None = None) -> list[tuple[str, str]]:
    """All ordered (leader, follower) pairs among venues."""
    vs = tuple(venues) if venues else DEFAULT_VENUES
    return [(a, b) for a, b in permutations(vs, 2)]


def pair_id(leader: str, follower: str, symbol: str = "") -> str:
    base = f"{leader}->{follower}"
    return f"{symbol}|{base}" if symbol else base


def empty_pair_report(leader: str, follower: str, *, horizon_ms: int) -> dict[str, Any]:
    return {
        "leader": leader,
        "follower": follower,
        "horizon_ms": horizon_ms,
        "sample_count": 0,
        "mean_follower_response_bps": None,
        "median_follower_response_bps": None,
        "directional_hit_rate": None,
        "effect_size": None,
        "uncertainty": None,
        "stability": None,
        "status": "INSUFFICIENT_DATA",
        "label": "HYPOTHESIS",
    }

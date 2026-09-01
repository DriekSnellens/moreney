"""Phase A observation collection from cycle books or synthetic tapes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from bot.opportunity.lead_lag.states import DataQuality
from bot.opportunity.lead_lag.types import LeadLagObservation

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def _mid(bid: Decimal, ask: Decimal) -> Decimal:
    return (bid + ask) / Decimal("2")


def _spread_bps(bid: Decimal, ask: Decimal) -> Decimal:
    m = _mid(bid, ask)
    if m <= 0:
        return _ZERO
    return (ask - bid) / m * _BPS


def observation_from_books(
    *,
    timestamp_ms: float,
    local_received_ms: float,
    symbol: str,
    leader_venue: str,
    follower_venue: str,
    leader_bid: Decimal,
    leader_ask: Decimal,
    follower_bid: Decimal,
    follower_ask: Decimal,
    prev_leader_mid: Decimal | None,
    prev_follower_mid: Decimal | None,
    leader_book_age_ms: float = 0.0,
    follower_book_age_ms: float = 0.0,
    leader_depth: Decimal = _ZERO,
    follower_depth: Decimal = _ZERO,
    data_quality: str = DataQuality.UNSUPPORTED.value,
    event_ts_source: str = "unknown",
) -> LeadLagObservation:
    l_mid = _mid(leader_bid, leader_ask)
    f_mid = _mid(follower_bid, follower_ask)
    l_ret = (
        (l_mid - prev_leader_mid) / prev_leader_mid * _BPS
        if prev_leader_mid and prev_leader_mid > 0
        else _ZERO
    )
    f_ret = (
        (f_mid - prev_follower_mid) / prev_follower_mid * _BPS
        if prev_follower_mid and prev_follower_mid > 0
        else _ZERO
    )
    return LeadLagObservation(
        timestamp_ms=timestamp_ms,
        local_received_ms=local_received_ms,
        symbol=symbol,
        leader_venue=leader_venue,
        follower_venue=follower_venue,
        leader_bid=leader_bid,
        leader_ask=leader_ask,
        follower_bid=follower_bid,
        follower_ask=follower_ask,
        leader_return_bps=l_ret,
        follower_return_bps=f_ret,
        spread_leader_bps=_spread_bps(leader_bid, leader_ask),
        spread_follower_bps=_spread_bps(follower_bid, follower_ask),
        leader_book_age_ms=leader_book_age_ms,
        follower_book_age_ms=follower_book_age_ms,
        leader_depth=leader_depth,
        follower_depth=follower_depth,
        data_quality=data_quality,
        event_ts_source=event_ts_source,
        receive_ts_source="local",
        notes=("PHASE_A_OBSERVATION",),
    )


class LeadLagObserver:
    """In-memory Phase A collector — does not place orders or affect ranking."""

    def __init__(self) -> None:
        self._prev_mids: dict[str, Decimal] = {}
        self.observations: list[LeadLagObservation] = []
        self.enabled = True
        self.alters_execution = False

    def _key(self, venue: str, symbol: str) -> str:
        return f"{venue}|{symbol}"

    def observe_pair(
        self,
        *,
        timestamp_ms: float,
        local_received_ms: float,
        symbol: str,
        leader_venue: str,
        follower_venue: str,
        leader_bid: Decimal,
        leader_ask: Decimal,
        follower_bid: Decimal,
        follower_ask: Decimal,
        **kwargs: Any,
    ) -> LeadLagObservation:
        lk = self._key(leader_venue, symbol)
        fk = self._key(follower_venue, symbol)
        obs = observation_from_books(
            timestamp_ms=timestamp_ms,
            local_received_ms=local_received_ms,
            symbol=symbol,
            leader_venue=leader_venue,
            follower_venue=follower_venue,
            leader_bid=leader_bid,
            leader_ask=leader_ask,
            follower_bid=follower_bid,
            follower_ask=follower_ask,
            prev_leader_mid=self._prev_mids.get(lk),
            prev_follower_mid=self._prev_mids.get(fk),
            **kwargs,
        )
        self._prev_mids[lk] = obs.leader_mid
        self._prev_mids[fk] = obs.follower_mid
        if self.enabled:
            self.observations.append(obs)
        return obs

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "alters_execution": False,
            "execution_enabled": False,
            "n_observations": len(self.observations),
            "label": "OBSERVED",
            "research_only": True,
        }


def synthetic_lead_lag_tape(
    *,
    n: int = 200,
    horizon_ms: int = 500,
    lead_bps: float = 20.0,
    noise_bps: float = 5.0,
    symbol: str = "BTCEUR",
    leader: str = "binance",
    follower: str = "bitvavo",
    dt_ms: float = 100.0,
    seed: int = 42,
) -> list[LeadLagObservation]:
    """Deterministic synthetic tape for unit tests (not live-equivalent)."""
    import random

    rng = random.Random(seed)
    out: list[LeadLagObservation] = []
    l_mid = Decimal("100")
    f_mid = Decimal("100")
    observer = LeadLagObserver()
    for i in range(n):
        t = i * dt_ms
        shock = lead_bps if (i % 7 == 0) else 0.0
        shock *= 1 if rng.random() > 0.5 else -1
        l_mid *= Decimal("1") + Decimal(str(shock)) / _BPS
        # Follower lags: applies ~70% of shock after horizon steps
        lag_steps = max(1, int(horizon_ms / dt_ms))
        delayed = 0.0
        if i >= lag_steps and (i - lag_steps) % 7 == 0:
            delayed = lead_bps * 0.7 * (1 if shock >= 0 else -1)
            # recover sign from prior leader move approx
            delayed = lead_bps * 0.7 * (1 if ((i - lag_steps) // 7) % 2 == 0 else -1)
        noise = (rng.random() - 0.5) * 2 * noise_bps
        f_mid *= Decimal("1") + Decimal(str(delayed + noise)) / _BPS
        half = Decimal("0.05")
        obs = observer.observe_pair(
            timestamp_ms=t,
            local_received_ms=t + 1.0,
            symbol=symbol,
            leader_venue=leader,
            follower_venue=follower,
            leader_bid=l_mid - half,
            leader_ask=l_mid + half,
            follower_bid=f_mid - half,
            follower_ask=f_mid + half,
            data_quality=DataQuality.HIGH.value,
            event_ts_source="synthetic",
            leader_depth=Decimal("10"),
            follower_depth=Decimal("10"),
        )
        out.append(obs)
    return out

"""Replay frozen-param OOS events and attach causal pre-trade features."""

from __future__ import annotations

import bisect
from typing import Any

from bot.research.forensics.buckets import (
    DENSITY_LOOKBACK_MS,
    MAJORS,
    MARKET_RETURN_LOOKBACK_MS,
    VOL_LOOKBACK_MS,
    chrono_block_id,
    density_regime,
    holding_regime,
    liquidity_regime,
    market_return_regime,
    quote_age_regime,
    spread_regime,
    strength_regime,
    utc_hour,
    vol_regime,
)
from bot.research.tournament.economics import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    NOTIONAL_EUR_DEFAULT,
    SLIPPAGE_BPS_DEFAULT,
    round_trip_fee_rate,
)
from bot.research.tournament.families import (
    CrossVenueDislocationFamily,
    ShortHorizonMeanReversionFamily,
)
from bot.research.tournament.tape_index import TapeIndex, past_return


def family_for(strategy_id: str):
    if strategy_id == "cross_venue_dislocation":
        return CrossVenueDislocationFamily()
    if strategy_id == "short_horizon_mean_reversion":
        return ShortHorizonMeanReversionFamily()
    raise ValueError(f"unsupported forensics family={strategy_id}")


class _TsView:
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


def _spread_bps(p) -> float | None:
    if p.mid <= 0:
        return None
    return (p.ask - p.bid) / p.mid * 10000.0


def _depth_eur(p) -> float:
    return (p.bid_size + p.ask_size) * p.mid


def _imb(p) -> float | None:
    denom = p.bid_size + p.ask_size
    if denom <= 0:
        return None
    return (p.bid_size - p.ask_size) / denom


def replay_oos_events(
    *,
    index: TapeIndex,
    strategy_id: str,
    frozen_params: dict[str, Any],
    oos_start_ns: int,
    oos_end_ns_inclusive: int,
    supported_horizons: list[int],
) -> list[dict[str, Any]]:
    fam = family_for(strategy_id)
    _stats, events = fam.evaluate_window(
        index,
        start_ns=int(oos_start_ns),
        end_ns_exclusive=None,
        end_ns_inclusive=int(oos_end_ns_inclusive),
        params=frozen_params,
        horizons=supported_horizons,
    )
    return events


def attach_economics(
    events: list[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
) -> list[dict[str, Any]]:
    """Per-event waterfall using the same shared cost rates as the tournament.

    Descriptive only. Tournament EXPECTED_NET remains mean-edge × notional − costs
    once; this sum is not a new gate.
    """
    notional = float(NOTIONAL_EUR_DEFAULT)
    fee_rate = float(round_trip_fee_rate(venue, venue_exit))
    fees = notional * fee_rate
    slip = notional * (float(SLIPPAGE_BPS_DEFAULT) / 10000.0)
    adverse = notional * (float(ADVERSE_BPS_DEFAULT) / 10000.0)
    latency = notional * (float(LATENCY_PENALTY_BPS) / 10000.0)
    out: list[dict[str, Any]] = []
    for e in events:
        fwd = float(e.get("forward") or 0.0)
        gross = notional * fwd
        net = gross - fees - slip - adverse - latency
        row = dict(e)
        row.update(
            {
                "gross": gross,
                "fees": fees,
                "slippage": slip,
                "adverse": adverse,
                "latency": latency,
                "net": net,
            }
        )
        out.append(row)
    return out


def enrich_events(
    events: list[dict[str, Any]],
    *,
    index: TapeIndex,
    strategy_id: str,
    frozen_params: dict[str, Any],
    oos_start_ns: int,
    oos_end_ns_inclusive: int,
) -> list[dict[str, Any]]:
    views: dict[tuple[str, str], _TsView] = {
        key: _TsView(pts) for key, pts in index.series.items()
    }
    horizon_ms = int(frozen_params.get("horizon_ms") or 0)
    horizon_ns = horizon_ms * 1_000_000
    if strategy_id == "cross_venue_dislocation":
        venue = str(frozen_params["venue_a"])
        peer = str(frozen_params["venue_b"])
        thresh = float(frozen_params.get("dislocation_bps") or 0.0)
    else:
        venue = str(frozen_params.get("venue") or "bitvavo")
        peer = None
        thresh = float(frozen_params.get("deviation_bps") or 0.0)
    others = [v for v in ("binance", "okx", "bitvavo") if v != venue]

    out: list[dict[str, Any]] = []
    for e in events:
        row = dict(e)
        ts = int(e["ts_ns"]) if e.get("ts_ns") is not None else None
        symbol = str(e.get("symbol") or "")
        row["hour_utc"] = utc_hour(ts) if ts is not None else None
        row["chrono_block"] = (
            chrono_block_id(ts, oos_start_ns, oos_end_ns_inclusive)
            if ts is not None
            else "UNKNOWN"
        )
        view = views.get((venue, symbol))
        i = view.index_at_or_before(ts) if view is not None and ts is not None else None
        p = view.points[i] if view is not None and i is not None else None
        spread = _spread_bps(p) if p is not None else None
        depth = _depth_eur(p) if p is not None else None
        imb = _imb(p) if p is not None else None
        vol_ret = (
            past_return(view.points, i, VOL_LOOKBACK_MS * 1_000_000)
            if view is not None and i is not None
            else None
        )
        vol_bps = abs(vol_ret) * 10000.0 if vol_ret is not None else None
        density = (
            view.count_in(ts - DENSITY_LOOKBACK_MS * 1_000_000, ts)
            if view is not None and ts is not None
            else None
        )
        mkt = _market_return_bps(views, ts) if ts is not None else None
        holding_ns = _holding_ns(view, i, horizon_ns) if view is not None else None

        if strategy_id == "cross_venue_dislocation":
            dis = float(e.get("dislocation") or 0.0)
            strength = abs(dis) * 10000.0
            direction = "A_RICH" if dis > 0 else "B_RICH"
            peer_ts = e.get("peer_ts_ns")
            if peer_ts is None and peer and ts is not None:
                pv = views.get((peer, symbol))
                if pv is not None:
                    j = pv.index_at_or_before(ts)
                    if j is not None:
                        peer_ts = pv.points[j].ts_ns
            age_ms = abs(ts - int(peer_ts)) / 1e6 if ts is not None and peer_ts is not None else None
            row["cross_venue_divergence_bps"] = strength
        else:
            dev = float(e.get("dev") or 0.0)
            strength = abs(dev) * 10000.0
            direction = "VENUE_RICH" if dev > 0 else "VENUE_CHEAP"
            ages = []
            if ts is not None:
                for v in others:
                    pv = views.get((v, symbol))
                    if pv is None:
                        continue
                    j = pv.index_at_or_before(ts)
                    if j is None:
                        continue
                    ages.append(abs(ts - pv.points[j].ts_ns) / 1e6)
            age_ms = max(ages) if ages else None
            row["cross_venue_divergence_bps"] = strength

        row.update(
            {
                "direction": direction,
                "signal_strength_bps": strength,
                "spread_bps": spread,
                "depth_eur": depth,
                "book_imbalance": imb,
                "vol_bps": vol_bps,
                "event_density": density,
                "market_return_bps": mkt,
                "holding_ns": holding_ns,
                "quote_age_ms": age_ms,
                "vol_regime": vol_regime(vol_bps),
                "spread_regime": spread_regime(spread),
                "liquidity_regime": liquidity_regime(depth),
                "event_density_regime": density_regime(density),
                "market_return_regime": market_return_regime(mkt),
                "holding_regime": holding_regime(holding_ns, horizon_ms),
                "signal_strength_bucket": strength_regime(strength, thresh),
                "quote_age_regime": quote_age_regime(age_ms),
            }
        )
        out.append(row)
    return out


def _holding_ns(view: _TsView | None, i: int | None, horizon_ns: int) -> int | None:
    if view is None or i is None or horizon_ns <= 0:
        return None
    pts = view.points
    if i < 0 or i >= len(pts):
        return None
    t0 = pts[i].ts_ns
    target = t0 + horizon_ns
    j = i + 1
    while j < len(pts) and pts[j].ts_ns < target:
        j += 1
    if j >= len(pts):
        return None
    return int(pts[j].ts_ns - t0)


def _market_return_bps(views: dict[tuple[str, str], _TsView], ts: int) -> float | None:
    rets: list[float] = []
    look = MARKET_RETURN_LOOKBACK_MS * 1_000_000
    for sym in MAJORS:
        view = views.get(("binance", sym)) or views.get(("okx", sym))
        if view is None:
            continue
        i = view.index_at_or_before(ts)
        if i is None:
            continue
        r = past_return(view.points, i, look)
        if r is not None:
            rets.append(r)
    if not rets:
        return None
    return (sum(rets) / len(rets)) * 10000.0

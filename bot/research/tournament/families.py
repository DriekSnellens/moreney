"""Five research-only strategy families for the tournament."""

from __future__ import annotations

from typing import Any

from bot.research.tournament.base import GatedFamily
from bot.research.tournament.criteria import (
    DIRECTED_ROUTES,
    DISLOCATION_BPS_GRID,
    HORIZONS_MS,
    IMBALANCE_THRESH_GRID,
    LOOKBACKS_MS,
    MEAN_REV_BPS_GRID,
    MOMENTUM_THRESH_GRID,
)
from bot.research.tournament.contract import SignalStats
from bot.research.tournament.gates import summarize_forwards
from bot.research.tournament.tape_index import (
    TapeIndex,
    forward_return,
    iter_window,
    past_return,
)


def _ns(ms: int) -> int:
    return int(ms * 1_000_000)


def _align_follower(
    leader_ts: int,
    follower_pts: list,
    *,
    i_hint: int = 0,
) -> tuple[int | None, int]:
    """Latest follower point with ts <= leader_ts. Returns (index, new_hint)."""
    j = min(i_hint, len(follower_pts) - 1) if follower_pts else -1
    while j + 1 < len(follower_pts) and follower_pts[j + 1].ts_ns <= leader_ts:
        j += 1
    if j < 0 or follower_pts[j].ts_ns > leader_ts:
        return None, max(0, j)
    return j, j


class LeadLagFamily(GatedFamily):
    strategy_id = "lead_lag"
    _features = ("leader_return", "follower_forward_return", "route")
    # Includes fast horizons so DATA gate can explicitly reject them.
    _requested = (100, 250, 500, 1000, 5000)

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        grid = []
        for h in horizons:
            for lb in (h, max(h // 2, 50)):
                for leader, follower in DIRECTED_ROUTES:
                    grid.append(
                        {
                            "horizon_ms": h,
                            "lookback_ms": lb,
                            "leader": leader,
                            "follower": follower,
                            "move_thresh": 0.00005,
                        }
                    )
        return grid[:48]  # cap grid size

    def evaluate_window(
        self,
        index: TapeIndex,
        *,
        start_ns: int,
        end_ns_exclusive: int | None,
        end_ns_inclusive: int | None,
        params: dict[str, Any],
        horizons: list[int],
    ) -> tuple[SignalStats, list[dict[str, Any]]]:
        leader = params["leader"]
        follower = params["follower"]
        h_ns = _ns(int(params["horizon_ms"]))
        lb_ns = _ns(int(params["lookback_ms"]))
        thresh = float(params["move_thresh"])
        forwards: list[float] = []
        events: list[dict[str, Any]] = []
        obs = 0
        symbols = sorted(set(index.symbols_for(leader)) & set(index.symbols_for(follower)))
        for symbol in symbols:
            lp = list(
                iter_window(
                    index.points(leader, symbol),
                    start_ns=start_ns,
                    end_ns_exclusive=end_ns_exclusive,
                    end_ns_inclusive=end_ns_inclusive,
                )
            )
            fp = index.points(follower, symbol)
            if len(lp) < 10 or len(fp) < 10:
                continue
            # Need indices into full series for forward_return
            full_l = index.points(leader, symbol)
            full_f = index.points(follower, symbol)
            # Map timestamp -> index for leader window points
            ts_to_i = {p.ts_ns: i for i, p in enumerate(full_l)}
            f_hint = 0
            step = max(1, len(lp) // 2000)
            for k in range(0, len(lp), step):
                p = lp[k]
                i = ts_to_i.get(p.ts_ns)
                if i is None:
                    continue
                obs += 1
                mov = past_return(full_l, i, lb_ns)
                if mov is None or abs(mov) < thresh:
                    continue
                fj, f_hint = _align_follower(p.ts_ns, full_f, i_hint=f_hint)
                if fj is None:
                    continue
                fwd = forward_return(full_f, fj, h_ns)
                if fwd is None:
                    continue
                # Predict follower continues in leader move direction
                signed = fwd if mov > 0 else -fwd
                forwards.append(signed)
                events.append(
                    {
                        "symbol": symbol,
                        "route": f"{leader}->{follower}",
                        "forward": signed,
                        "leader_move": mov,
                    }
                )
        return summarize_forwards(forwards, observations=max(obs, len(forwards))), events


class CrossVenueDislocationFamily(GatedFamily):
    strategy_id = "cross_venue_dislocation"
    _features = ("dislocation_bps", "spread_change")
    _requested = (500, 1000, 2000, 5000)

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        grid = []
        for h in horizons:
            for bps in DISLOCATION_BPS_GRID:
                for a, b in (("binance", "okx"), ("binance", "bitvavo"), ("okx", "bitvavo")):
                    grid.append(
                        {
                            "horizon_ms": h,
                            "dislocation_bps": bps,
                            "venue_a": a,
                            "venue_b": b,
                            "leader": a,
                            "follower": b,
                        }
                    )
        return grid

    def evaluate_window(
        self,
        index: TapeIndex,
        *,
        start_ns: int,
        end_ns_exclusive: int | None,
        end_ns_inclusive: int | None,
        params: dict[str, Any],
        horizons: list[int],
    ) -> tuple[SignalStats, list[dict[str, Any]]]:
        a = params["venue_a"]
        b = params["venue_b"]
        h_ns = _ns(int(params["horizon_ms"]))
        thr = float(params["dislocation_bps"]) / 10000.0
        forwards: list[float] = []
        events: list[dict[str, Any]] = []
        obs = 0
        symbols = sorted(set(index.symbols_for(a)) & set(index.symbols_for(b)))
        for symbol in symbols:
            pa = index.points(a, symbol)
            pb = index.points(b, symbol)
            if len(pa) < 10 or len(pb) < 10:
                continue
            window = list(
                iter_window(
                    pa,
                    start_ns=start_ns,
                    end_ns_exclusive=end_ns_exclusive,
                    end_ns_inclusive=end_ns_inclusive,
                )
            )
            ts_to_i = {p.ts_ns: i for i, p in enumerate(pa)}
            b_hint = 0
            step = max(1, len(window) // 2000)
            for k in range(0, len(window), step):
                p = window[k]
                i = ts_to_i[p.ts_ns]
                obs += 1
                jb, b_hint = _align_follower(p.ts_ns, pb, i_hint=b_hint)
                if jb is None:
                    continue
                mid_a = p.mid
                mid_b = pb[jb].mid
                if mid_a <= 0 or mid_b <= 0:
                    continue
                dis = (mid_a - mid_b) / mid_a
                if abs(dis) < thr:
                    continue
                # Convergence: dislocation shrinks → signed so positive = converged
                fwd_a = forward_return(pa, i, h_ns)
                fwd_b = forward_return(pb, jb, h_ns)
                if fwd_a is None or fwd_b is None:
                    continue
                # Relative mid change of A vs B should oppose dislocation
                rel = fwd_a - fwd_b
                signed = -rel if dis > 0 else rel  # if A rich, expect A underperforms B
                forwards.append(signed)
                events.append(
                    {
                        "symbol": symbol,
                        "route": f"{a}|{b}",
                        "forward": signed,
                        "dislocation": dis,
                        "ts_ns": p.ts_ns,
                        "peer_ts_ns": pb[jb].ts_ns,
                    }
                )
        return summarize_forwards(forwards, observations=max(obs, len(forwards))), events


class ShortHorizonMeanReversionFamily(GatedFamily):
    strategy_id = "short_horizon_mean_reversion"
    _features = ("deviation_from_cross_mid", "forward_return")
    _requested = (500, 1000, 2000, 5000)

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        grid = []
        for h in horizons:
            for bps in MEAN_REV_BPS_GRID:
                for venue in ("binance", "okx", "bitvavo"):
                    grid.append(
                        {
                            "horizon_ms": h,
                            "deviation_bps": bps,
                            "venue": venue,
                        }
                    )
        return grid

    def evaluate_window(
        self,
        index: TapeIndex,
        *,
        start_ns: int,
        end_ns_exclusive: int | None,
        end_ns_inclusive: int | None,
        params: dict[str, Any],
        horizons: list[int],
    ) -> tuple[SignalStats, list[dict[str, Any]]]:
        venue = params["venue"]
        h_ns = _ns(int(params["horizon_ms"]))
        thr = float(params["deviation_bps"]) / 10000.0
        forwards: list[float] = []
        events: list[dict[str, Any]] = []
        obs = 0
        others = [v for v in ("binance", "okx", "bitvavo") if v != venue]
        for symbol in index.symbols_for(venue):
            pts = index.points(venue, symbol)
            if len(pts) < 20:
                continue
            other_series = {
                v: index.points(v, symbol) for v in others if index.points(v, symbol)
            }
            if len(other_series) < 1:
                continue
            window = list(
                iter_window(
                    pts,
                    start_ns=start_ns,
                    end_ns_exclusive=end_ns_exclusive,
                    end_ns_inclusive=end_ns_inclusive,
                )
            )
            ts_to_i = {p.ts_ns: i for i, p in enumerate(pts)}
            hints = {v: 0 for v in other_series}
            step = max(1, len(window) // 2000)
            for k in range(0, len(window), step):
                p = window[k]
                i = ts_to_i[p.ts_ns]
                obs += 1
                mids = []
                for v, series in other_series.items():
                    j, hints[v] = _align_follower(p.ts_ns, series, i_hint=hints[v])
                    if j is not None:
                        mids.append(series[j].mid)
                if not mids:
                    continue
                fair = sum(mids) / len(mids)
                if fair <= 0:
                    continue
                dev = (p.mid - fair) / fair
                if abs(dev) < thr:
                    continue
                fwd = forward_return(pts, i, h_ns)
                if fwd is None:
                    continue
                # Mean reversion: positive when price moves back toward fair
                signed = -fwd if dev > 0 else fwd
                forwards.append(signed)
                events.append(
                    {
                        "symbol": symbol,
                        "venue": venue,
                        "route": venue,
                        "forward": signed,
                        "dev": dev,
                        "ts_ns": p.ts_ns,
                    }
                )
        return summarize_forwards(forwards, observations=max(obs, len(forwards))), events


class OrderBookImbalanceFamily(GatedFamily):
    strategy_id = "order_book_imbalance"
    _features = ("depth_imbalance", "microprice", "spread")
    _requested = (500, 1000, 2000, 5000)

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        grid = []
        for h in horizons:
            for thr in IMBALANCE_THRESH_GRID:
                for venue in ("binance", "okx", "bitvavo"):
                    grid.append({"horizon_ms": h, "imbalance_thresh": thr, "venue": venue})
        return grid

    def evaluate_window(
        self,
        index: TapeIndex,
        *,
        start_ns: int,
        end_ns_exclusive: int | None,
        end_ns_inclusive: int | None,
        params: dict[str, Any],
        horizons: list[int],
    ) -> tuple[SignalStats, list[dict[str, Any]]]:
        venue = params["venue"]
        h_ns = _ns(int(params["horizon_ms"]))
        thr = float(params["imbalance_thresh"])
        forwards: list[float] = []
        events: list[dict[str, Any]] = []
        obs = 0
        for symbol in index.symbols_for(venue):
            pts = index.points(venue, symbol)
            window = list(
                iter_window(
                    pts,
                    start_ns=start_ns,
                    end_ns_exclusive=end_ns_exclusive,
                    end_ns_inclusive=end_ns_inclusive,
                )
            )
            ts_to_i = {p.ts_ns: i for i, p in enumerate(pts)}
            step = max(1, len(window) // 2000)
            for k in range(0, len(window), step):
                p = window[k]
                i = ts_to_i[p.ts_ns]
                obs += 1
                denom = p.bid_size + p.ask_size
                if denom <= 0:
                    continue
                imb = (p.bid_size - p.ask_size) / denom
                if abs(imb) < thr:
                    continue
                fwd = forward_return(pts, i, h_ns)
                if fwd is None:
                    continue
                signed = fwd if imb > 0 else -fwd
                forwards.append(signed)
                events.append(
                    {
                        "symbol": symbol,
                        "venue": venue,
                        "route": venue,
                        "forward": signed,
                        "imbalance": imb,
                    }
                )
        return summarize_forwards(forwards, observations=max(obs, len(forwards))), events


class ShortHorizonMomentumFamily(GatedFamily):
    strategy_id = "short_horizon_momentum"
    _features = ("past_return", "forward_return")
    _requested = (500, 1000, 2000, 5000)

    def param_grid(self, horizons: list[int]) -> list[dict[str, Any]]:
        grid = []
        for h in horizons:
            for lb in LOOKBACKS_MS:
                if lb > h * 2:
                    continue
                for thr in MOMENTUM_THRESH_GRID:
                    for venue in ("binance", "okx"):
                        grid.append(
                            {
                                "horizon_ms": h,
                                "lookback_ms": lb,
                                "momentum_thresh": thr,
                                "venue": venue,
                            }
                        )
        return grid

    def evaluate_window(
        self,
        index: TapeIndex,
        *,
        start_ns: int,
        end_ns_exclusive: int | None,
        end_ns_inclusive: int | None,
        params: dict[str, Any],
        horizons: list[int],
    ) -> tuple[SignalStats, list[dict[str, Any]]]:
        venue = params["venue"]
        h_ns = _ns(int(params["horizon_ms"]))
        lb_ns = _ns(int(params["lookback_ms"]))
        thr = float(params["momentum_thresh"])
        forwards: list[float] = []
        events: list[dict[str, Any]] = []
        obs = 0
        for symbol in index.symbols_for(venue):
            pts = index.points(venue, symbol)
            window = list(
                iter_window(
                    pts,
                    start_ns=start_ns,
                    end_ns_exclusive=end_ns_exclusive,
                    end_ns_inclusive=end_ns_inclusive,
                )
            )
            ts_to_i = {p.ts_ns: i for i, p in enumerate(pts)}
            step = max(1, len(window) // 2000)
            for k in range(0, len(window), step):
                p = window[k]
                i = ts_to_i[p.ts_ns]
                obs += 1
                past = past_return(pts, i, lb_ns)
                if past is None or abs(past) < thr:
                    continue
                fwd = forward_return(pts, i, h_ns)
                if fwd is None:
                    continue
                signed = fwd if past > 0 else -fwd
                forwards.append(signed)
                events.append(
                    {
                        "symbol": symbol,
                        "venue": venue,
                        "route": venue,
                        "forward": signed,
                        "past": past,
                    }
                )
        return summarize_forwards(forwards, observations=max(obs, len(forwards))), events


def all_families() -> list[GatedFamily]:
    return [
        LeadLagFamily(),
        CrossVenueDislocationFamily(),
        ShortHorizonMeanReversionFamily(),
        OrderBookImbalanceFamily(),
        ShortHorizonMomentumFamily(),
    ]

"""Velocity entries: a minute (or 5 minutes) of quote volume far above the section baseline.

Baseline €/minute is the prior-30-day median daily quote volume divided by 1440.
The signal uses only the completed minute. The fill is the next traded minute's
open, at most ``entry_wait_ms`` later. Costs are taker 0.25% plus impact
``0.02 * notional / baseline`` per side, capped at 3%, the same model as the
unusual-volume lab.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from bot.research.volume_velocity.fetch import day_path

DAY_MS = 86_400_000
MIN_MS = 60_000
TAKER = 0.0025
IMPACT_K = 0.02
IMPACT_CAP = 0.03


def minute_qvol(bar: Sequence[float]) -> float:
    close = float(bar[4])
    vol = float(bar[5])
    if close <= 0 or vol <= 0:
        return 0.0
    return vol * close


def side_cost(notional: float, baseline_eur: float) -> float:
    if baseline_eur <= 0 or notional <= 0:
        return TAKER
    impact = min(IMPACT_CAP, IMPACT_K * notional / baseline_eur)
    return TAKER + impact


@dataclass(frozen=True)
class Signal:
    base: str
    day_ms: int
    signal_ms: int
    entry_ms: int
    entry_open: float
    velocity: float
    up: bool
    baseline_eur: float
    # bars at and after the entry minute: [ts, o, h, l, c]
    path: tuple[tuple[int, float, float, float, float], ...]


def scan_day(
    bars: Sequence[Sequence[float]],
    *,
    base: str,
    day_ms: int,
    baseline_eur: float,
    mult: float,
    window_min: int = 1,
    cooldown_ms: int = 60 * MIN_MS,
    entry_wait_ms: int = 5 * MIN_MS,
    horizon_ms: int = 26 * 60 * MIN_MS,
    signal_end_ms: int | None = None,
) -> list[Signal]:
    """Bursts whose quote volume over ``window_min`` minutes is ``>= mult`` times baseline.

    ``window_min`` 1 is a single printed minute. A longer window sums the minutes
    whose open falls inside ``(t - window, t]``; missing minutes count as zero,
    so the ratio is volume per minute of wall-clock time, not per printed bar.
    """
    per_min = baseline_eur / 1440.0
    if per_min <= 0 or mult <= 0 or not bars:
        return []
    ordered = sorted(bars, key=lambda r: int(r[0]))
    span = window_min * MIN_MS
    out: list[Signal] = []
    last = -(10**18)
    qvols = [minute_qvol(r) for r in ordered]
    times = [int(r[0]) for r in ordered]
    j0 = 0
    running = 0.0
    for i, bar in enumerate(ordered):
        running += qvols[i]
        while times[j0] <= times[i] - span:
            running -= qvols[j0]
            j0 += 1
        vel = running / (window_min * per_min)
        if signal_end_ms is not None and times[i] >= signal_end_ms:
            continue
        if vel < mult or times[i] - last < cooldown_ms:
            continue
        nxt = None
        for k in range(i + 1, len(ordered)):
            gap = times[k] - times[i]
            if gap <= 0:
                continue
            if gap > entry_wait_ms:
                break
            nxt = k
            break
        if nxt is None:
            continue
        last = times[i]
        entry = ordered[nxt]
        path = tuple(
            (
                times[k],
                float(ordered[k][1]),
                float(ordered[k][2]),
                float(ordered[k][3]),
                float(ordered[k][4]),
            )
            for k in range(nxt, len(ordered))
            if times[k] - times[nxt] <= horizon_ms
        )
        out.append(
            Signal(
                base=base,
                day_ms=day_ms,
                signal_ms=times[i],
                entry_ms=times[nxt],
                entry_open=float(entry[1]),
                velocity=vel,
                up=float(bar[4]) >= float(bar[1]),
                baseline_eur=baseline_eur,
                path=path,
            )
        )
    return out


def load_signals(
    members: Sequence[dict[str, Any]],
    *,
    mult: float,
    window_min: int,
    lo: float,
    hi: float,
) -> list[Signal]:
    """Scan cached member-days whose baseline sits in ``[lo, hi]``."""
    by_base: dict[str, list[dict[str, Any]]] = {}
    for row in members:
        base_eur = float(row["baseline_eur"])
        if lo <= base_eur <= hi:
            by_base.setdefault(str(row["base"]), []).append(row)
    signals: list[Signal] = []
    for base, rows in by_base.items():
        # Stitch consecutive cached days so a burst near the close can fill and
        # exit on the next day's minutes. Membership still gates the signal day.
        days = {}
        for row in rows:
            path = day_path(base, int(row["day_ms"]))
            if not path.exists():
                continue
            try:
                bars = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(bars, list):
                days[int(row["day_ms"])] = bars
        if not days:
            continue
        # Attach the following cached day when we have it, for the exit path only.
        extra_days = set()
        for day in list(days):
            nxt = day + DAY_MS
            if nxt in days or nxt in extra_days:
                continue
            path = day_path(base, nxt)
            if path.exists():
                try:
                    bars = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(bars, list):
                    days[nxt] = bars
                    extra_days.add(nxt)
        for day, bars in days.items():
            if day in extra_days:
                continue
            baseline = next(float(r["baseline_eur"]) for r in rows if int(r["day_ms"]) == day)
            stitched = list(bars)
            nxt = days.get(day + DAY_MS)
            if nxt:
                stitched.extend(nxt)
            signals.extend(
                scan_day(
                    stitched,
                    base=base,
                    day_ms=day,
                    baseline_eur=baseline,
                    mult=mult,
                    window_min=window_min,
                    signal_end_ms=day + DAY_MS,
                )
            )
    signals.sort(key=lambda s: (s.entry_ms, -s.velocity, s.base))
    return signals


def _exit_fill(
    sig: Signal,
    *,
    hold_ms: int,
    trail_pct: float,
    hard_stop: float,
) -> tuple[float, int, str] | None:
    """Return (exit_px, exit_ms, reason) from the path after the entry open."""
    if sig.entry_open <= 0 or not sig.path:
        return None
    entry = sig.entry_open
    stop_px = entry * (1.0 - hard_stop) if hard_stop > 0 else 0.0
    peak = entry
    deadline = sig.entry_ms + hold_ms
    for i, (ts, op, hi, lo, cl) in enumerate(sig.path):
        if ts < sig.entry_ms:
            continue
        # Gap through the hard stop fills at the open.
        if stop_px and op <= stop_px:
            return op, ts, "gap_stop"
        if stop_px and lo <= stop_px:
            return stop_px, ts, "hard_stop"
        peak = max(peak, hi)
        trail_px = peak * (1.0 - trail_pct) if trail_pct > 0 else 0.0
        if trail_px and cl <= trail_px:
            for nxt in sig.path[i + 1 :]:
                return float(nxt[1]), int(nxt[0]), "trail"
            return cl, ts, "trail_close"
        if ts >= deadline:
            return cl, ts, "time"
    last = sig.path[-1]
    return float(last[4]), int(last[0]), "eod"


def trade_return(
    sig: Signal,
    *,
    notional: float,
    hold_ms: int,
    trail_pct: float = 0.0,
    hard_stop: float = 0.0,
) -> dict[str, Any] | None:
    filled = _exit_fill(sig, hold_ms=hold_ms, trail_pct=trail_pct, hard_stop=hard_stop)
    if filled is None:
        return None
    exit_px, exit_ms, reason = filled
    if exit_px <= 0:
        return None
    cost = side_cost(notional, sig.baseline_eur)
    gross = exit_px / sig.entry_open - 1.0
    # Taker plus impact on the way in and on the way out, same linear charge
    # the unusual-volume lab uses.
    net = gross - 2.0 * cost
    return {
        "base": sig.base,
        "entry_ms": sig.entry_ms,
        "exit_ms": exit_ms,
        "gross": gross,
        "net": net,
        "pnl": notional * net,
        "reason": reason,
        "velocity": sig.velocity,
        "up": sig.up,
        "notional": notional,
    }


def run_book(
    signals: Sequence[Signal],
    *,
    book_eur: float,
    slot_eur: float,
    max_positions: int,
    hold_ms: int,
    trail_pct: float = 0.0,
    hard_stop: float = 0.0,
    up_only: bool = False,
    start_ms: int = 0,
    end_ms: int = 0,
) -> dict[str, Any]:
    """Fixed slots, one position per base, skip when the book is full.

    Ordering is entry time then velocity. No look at the forward return.
    """
    chosen = [
        s
        for s in signals
        if (not up_only or s.up)
        and (start_ms <= 0 or s.entry_ms >= start_ms)
        and (end_ms <= 0 or s.entry_ms < end_ms)
    ]
    open_pos: list[tuple[str, int]] = []  # base, exit_ms
    trades: list[dict[str, Any]] = []
    equity = book_eur
    peak = book_eur
    max_dd = 0.0
    for sig in chosen:
        open_pos = [p for p in open_pos if p[1] > sig.entry_ms]
        if any(p[0] == sig.base for p in open_pos):
            continue
        if len(open_pos) >= max_positions:
            continue
        row = trade_return(
            sig,
            notional=slot_eur,
            hold_ms=hold_ms,
            trail_pct=trail_pct,
            hard_stop=hard_stop,
        )
        if row is None:
            continue
        open_pos.append((sig.base, int(row["exit_ms"])))
        trades.append(row)
    for row in sorted(trades, key=lambda t: int(t["exit_ms"])):
        equity += float(row["pnl"])
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    nets = [float(t["net"]) for t in trades]
    return {
        "n": len(trades),
        "pnl": round(sum(float(t["pnl"]) for t in trades), 2),
        "gross_pnl": round(sum(slot_eur * float(t["gross"]) for t in trades), 2),
        "mean": (sum(nets) / len(nets)) if nets else 0.0,
        "median": _median(nets),
        "win": (sum(1 for x in nets if x > 0) / len(nets)) if nets else 0.0,
        "max_dd": round(max_dd, 2),
        "end": round(equity, 2),
    }


def collect_compact(
    members: Sequence[dict[str, Any]],
    jobs: Sequence[tuple[str, int, float]],
    exits: Sequence[tuple[str, int, float, float]],
    *,
    lo: float,
    hi: float,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """One pass over the 1m cache. Each row is a gross return, not a bar path.

    ``jobs`` entries are ``(name, window_min, mult)``. ``window_min`` 0 is the
    clock control: buy the first traded minute of the section day.
    """
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {
        (name, exit_name): [] for name, _, _ in jobs for exit_name, _, _, _ in exits
    }
    by_base: dict[str, list[dict[str, Any]]] = {}
    for row in members:
        base_eur = float(row["baseline_eur"])
        if lo <= base_eur <= hi:
            by_base.setdefault(str(row["base"]), []).append(row)
    for base, rows in by_base.items():
        days: dict[int, list] = {}
        for row in rows:
            path = day_path(base, int(row["day_ms"]))
            if not path.exists():
                continue
            try:
                bars = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(bars, list):
                days[int(row["day_ms"])] = bars
        extra: set[int] = set()
        for day in list(days):
            nxt = day + DAY_MS
            if nxt in days or nxt in extra:
                continue
            path = day_path(base, nxt)
            if not path.exists():
                continue
            try:
                bars = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(bars, list):
                days[nxt] = bars
                extra.add(nxt)
        baselines = {int(r["day_ms"]): float(r["baseline_eur"]) for r in rows}
        for day, bars in days.items():
            if day in extra:
                continue
            stitched = list(bars)
            nxt_bars = days.get(day + DAY_MS)
            if nxt_bars:
                stitched.extend(nxt_bars)
            baseline = baselines[day]
            for name, window, mult in jobs:
                if window <= 0:
                    sigs = _clock_signal(stitched, base=base, day_ms=day, baseline_eur=baseline)
                else:
                    sigs = scan_day(
                        stitched,
                        base=base,
                        day_ms=day,
                        baseline_eur=baseline,
                        mult=mult,
                        window_min=window,
                        signal_end_ms=day + DAY_MS,
                    )
                for sig in sigs:
                    for exit_name, hold, trail, stop in exits:
                        filled = _exit_fill(sig, hold_ms=hold, trail_pct=trail, hard_stop=stop)
                        if filled is None or sig.entry_open <= 0:
                            continue
                        exit_px, exit_ms, reason = filled
                        if exit_px <= 0:
                            continue
                        buckets[(name, exit_name)].append(
                            {
                                "base": base,
                                "entry_ms": sig.entry_ms,
                                "exit_ms": exit_ms,
                                "gross": exit_px / sig.entry_open - 1.0,
                                "up": sig.up,
                                "baseline_eur": baseline,
                                "velocity": sig.velocity,
                                "reason": reason,
                            }
                        )
    for rows_out in buckets.values():
        rows_out.sort(key=lambda r: (r["entry_ms"], -r["velocity"], r["base"]))
    return buckets


def _clock_signal(
    bars: Sequence[Sequence[float]],
    *,
    base: str,
    day_ms: int,
    baseline_eur: float,
) -> list[Signal]:
    ordered = sorted(
        [r for r in bars if isinstance(r, (list, tuple)) and len(r) >= 5],
        key=lambda r: int(r[0]),
    )
    if not ordered:
        return []
    entry = ordered[0]
    series = tuple(
        (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        for r in ordered
        if int(r[0]) - int(entry[0]) <= 26 * 60 * MIN_MS
    )
    return [
        Signal(
            base=base,
            day_ms=day_ms,
            signal_ms=int(entry[0]),
            entry_ms=int(entry[0]),
            entry_open=float(entry[1]),
            velocity=0.0,
            up=True,
            baseline_eur=baseline_eur,
            path=series,
        )
    ]


def run_compact(
    rows: Sequence[dict[str, Any]],
    *,
    book_eur: float,
    slot_eur: float,
    max_positions: int,
    up_only: bool = False,
    start_ms: int = 0,
    end_ms: int = 0,
) -> dict[str, Any]:
    """Same slot rule as ``run_book``, on precomputed gross returns."""
    chosen = [
        r
        for r in rows
        if (not up_only or r["up"])
        and (start_ms <= 0 or int(r["entry_ms"]) >= start_ms)
        and (end_ms <= 0 or int(r["entry_ms"]) < end_ms)
    ]
    open_pos: list[tuple[str, int]] = []
    taken: list[dict[str, Any]] = []
    for row in chosen:
        entry_ms = int(row["entry_ms"])
        open_pos = [p for p in open_pos if p[1] > entry_ms]
        if any(p[0] == row["base"] for p in open_pos):
            continue
        if len(open_pos) >= max_positions:
            continue
        cost = side_cost(slot_eur, float(row["baseline_eur"]))
        net = float(row["gross"]) - 2.0 * cost
        taken.append(
            {
                "exit_ms": int(row["exit_ms"]),
                "gross": float(row["gross"]),
                "net": net,
                "pnl": slot_eur * net,
            }
        )
        open_pos.append((str(row["base"]), int(row["exit_ms"])))
    equity = book_eur
    peak = book_eur
    max_dd = 0.0
    for row in sorted(taken, key=lambda t: t["exit_ms"]):
        equity += row["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    nets = [float(t["net"]) for t in taken]
    return {
        "n": len(taken),
        "pnl": round(sum(t["pnl"] for t in taken), 2),
        "gross_pnl": round(sum(slot_eur * t["gross"] for t in taken), 2),
        "mean": (sum(nets) / len(nets)) if nets else 0.0,
        "median": _median(nets),
        "win": (sum(1 for x in nets if x > 0) / len(nets)) if nets else 0.0,
        "max_dd": round(max_dd, 2),
        "end": round(equity, 2),
    }


def clock_signals_from_members(
    members: Sequence[dict[str, Any]],
    *,
    lo: float,
    hi: float,
) -> list[Signal]:
    """Control: buy the first traded minute of every section day, no velocity test."""
    out: list[Signal] = []
    for row in members:
        baseline = float(row["baseline_eur"])
        if not (lo <= baseline <= hi):
            continue
        day = int(row["day_ms"])
        path = day_path(str(row["base"]), day)
        nxt_path = day_path(str(row["base"]), day + DAY_MS)
        if not path.exists():
            continue
        try:
            bars = json.loads(path.read_text())
            nxt = json.loads(nxt_path.read_text()) if nxt_path.exists() else []
        except (OSError, json.JSONDecodeError):
            continue
        ordered = sorted(
            [r for r in list(bars) + list(nxt) if isinstance(r, list) and len(r) >= 5],
            key=lambda r: int(r[0]),
        )
        if len(ordered) < 2:
            continue
        entry = ordered[0]
        series = tuple(
            (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            for r in ordered
            if int(r[0]) - int(entry[0]) <= 26 * 60 * MIN_MS
        )
        out.append(
            Signal(
                base=str(row["base"]),
                day_ms=day,
                signal_ms=int(entry[0]),
                entry_ms=int(entry[0]),
                entry_open=float(entry[1]),
                velocity=0.0,
                up=True,
                baseline_eur=baseline,
                path=series,
            )
        )
    out.sort(key=lambda s: (s.entry_ms, s.base))
    return out


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    n = len(ys)
    mid = n // 2
    if n % 2:
        return ys[mid]
    return (ys[mid - 1] + ys[mid]) / 2.0

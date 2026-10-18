"""Entry-quality and peak-cashing lab on the armed ``residual_full`` pack.

Four owner hypotheses, each turned into generic, measurable overlays that
apply to any base (no per-coin branches):

  location   "never buy high": extension above SMA20/SMA50, distance below the
             60d high, position inside the 20d range, RSI14, yesterday's
             retrace, and the opposite thesis (only buy pulled-back names).
  impulse    "news": how much of the lookback return came from one candle, and
             abnormal volume on the signal days (a news day without a feed).
  flow       "buy vs sell orders": Binance taker-buy ratio, its z-score, net
             delta, trade-count surge or drought, over 1/3/10 days.
  cashing    "peaks are not cashed": tighter trails, ATR trails, full and
             partial take-profits, spike give-backs, earlier/later locks,
             time stops — all on top of or instead of trail10 + lock12/6.

Every gate sees data through the signal close only; buys fill next open with
the same WET fill model as the rest of the research stack.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from bot.live.momentum_btc_rs_clip import closes_of
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import rows_through
from bot.research.clip_exit_lab.policies import ExitPolicy
from bot.research.entry_quality_lab.flow import (
    Flow,
    net_delta_share,
    taker_buy_ratio,
    taker_ratio_z,
    trade_count_ratio,
)
from bot.research.factor_scoreboard.engine import LIVE_KNOBS, LIVE_POLICY

Gate = Callable[[str, str], bool]
Scale = Callable[[str, str], float]
Ohlc = Mapping[str, Sequence[Sequence[float]]]

Variant = tuple[str, str, Gate | None, Scale | None, dict[str, Any]]


# ---------- price-location features (all through ``date``) ----------
def _closes(ohlc: Ohlc, base: str, date: str) -> list[float]:
    return [float(c) for c in closes_of(rows_through(ohlc.get(base) or [], date))]


def ext_sma(ohlc: Ohlc, base: str, date: str, n: int) -> float | None:
    cl = _closes(ohlc, base, date)
    if len(cl) < n:
        return None
    return cl[-1] / statistics.fmean(cl[-n:]) - 1.0


def off_high(ohlc: Ohlc, base: str, date: str, n: int = 60) -> float | None:
    rows = rows_through(ohlc.get(base) or [], date)
    if len(rows) < n:
        return None
    hi = max(float(r[2]) for r in rows[-n:])
    return float(rows[-1][4]) / hi - 1.0 if hi > 0 else None


def range_pos(ohlc: Ohlc, base: str, date: str, n: int = 20) -> float | None:
    rows = rows_through(ohlc.get(base) or [], date)
    if len(rows) < n:
        return None
    hi = max(float(r[2]) for r in rows[-n:])
    lo = min(float(r[3]) for r in rows[-n:])
    if hi <= lo:
        return None
    return (float(rows[-1][4]) - lo) / (hi - lo)


def rsi(ohlc: Ohlc, base: str, date: str, n: int = 14) -> float | None:
    cl = _closes(ohlc, base, date)
    if len(cl) < n + 1:
        return None
    ups = downs = 0.0
    for a, b in zip(cl[-n - 1 : -1], cl[-n:], strict=True):
        d = b - a
        if d >= 0:
            ups += d
        else:
            downs -= d
    if downs <= 0:
        return 100.0
    rs = (ups / n) / (downs / n)
    return 100.0 - 100.0 / (1.0 + rs)


def ret_days(ohlc: Ohlc, base: str, date: str, n: int = 1) -> float | None:
    cl = _closes(ohlc, base, date)
    if len(cl) < n + 1 or cl[-n - 1] <= 0:
        return None
    return cl[-1] / cl[-n - 1] - 1.0


def impulse_share(
    ohlc: Ohlc, base: str, date: str, lookback: int = 10, skip: int = 1
) -> float | None:
    """Largest single-day gain inside the ranking window / window return.

    1.0 means one candle did all the work; >1 means the rest of the window
    actually gave back. ``None`` when the window return is not positive."""
    cl = _closes(ohlc, base, date)
    need = lookback + skip + 1
    if len(cl) < need:
        return None
    win = cl[-need : len(cl) - skip] if skip > 0 else cl[-need:]
    total = win[-1] / win[0] - 1.0
    if total <= 0.02:
        return None
    best = max(b / a - 1.0 for a, b in zip(win[:-1], win[1:], strict=True))
    return best / total


def volume_surge(ohlc: Ohlc, base: str, date: str, n: int = 3, base_n: int = 30) -> float | None:
    """Max EUR volume over the last n days / trailing base_n median."""
    rows = rows_through(ohlc.get(base) or [], date)
    if len(rows) < base_n + n:
        return None
    qv = [float(r[5]) * float(r[4]) for r in rows]
    ref = statistics.median(qv[-(base_n + n) : -n])
    if ref <= 0:
        return None
    return max(qv[-n:]) / ref


# ---------- gates ----------
def _gate(fn: Callable[[str, str], float | None], ok: Callable[[float], bool]) -> Gate:
    def f(date: str, base: str) -> bool:
        v = fn(date, base)
        return v is None or ok(v)

    return f


def gate_ext_max(ohlc: Ohlc, n: int, th: float) -> Gate:
    return _gate(lambda d, b: ext_sma(ohlc, b, d, n), lambda v: v <= th)


def gate_off_high_min(ohlc: Ohlc, th: float, n: int = 60) -> Gate:
    """Only buy names at least ``th`` below their n-day high (buy-the-dip thesis)."""
    return _gate(lambda d, b: off_high(ohlc, b, d, n), lambda v: v <= -th)


def gate_off_high_max(ohlc: Ohlc, th: float, n: int = 60) -> Gate:
    """Only buy names within ``th`` of their n-day high (breakout thesis)."""
    return _gate(lambda d, b: off_high(ohlc, b, d, n), lambda v: v >= -th)


def gate_range_pos_max(ohlc: Ohlc, th: float) -> Gate:
    return _gate(lambda d, b: range_pos(ohlc, b, d), lambda v: v <= th)


def gate_rsi_max(ohlc: Ohlc, th: float) -> Gate:
    return _gate(lambda d, b: rsi(ohlc, b, d), lambda v: v <= th)


def gate_ret1_min(ohlc: Ohlc, th: float) -> Gate:
    """Skip when the skipped day (yesterday) already retraced more than ``th``."""
    return _gate(lambda d, b: ret_days(ohlc, b, d, 1), lambda v: v >= -th)


def gate_ret1_max(ohlc: Ohlc, th: float) -> Gate:
    """Skip when yesterday alone ran more than ``th`` (chasing a fresh candle)."""
    return _gate(lambda d, b: ret_days(ohlc, b, d, 1), lambda v: v <= th)


def gate_impulse_max(ohlc: Ohlc, th: float) -> Gate:
    return _gate(lambda d, b: impulse_share(ohlc, b, d), lambda v: v <= th)


def gate_volume_surge_max(ohlc: Ohlc, th: float, n: int = 3) -> Gate:
    return _gate(lambda d, b: volume_surge(ohlc, b, d, n), lambda v: v <= th)


def gate_volume_surge_min(ohlc: Ohlc, th: float, n: int = 3) -> Gate:
    return _gate(lambda d, b: volume_surge(ohlc, b, d, n), lambda v: v >= th)


def gate_taker_min(flow: Flow, n: int, th: float) -> Gate:
    return _gate(lambda d, b: taker_buy_ratio(flow.get(b) or [], d, n), lambda v: v >= th)


def gate_taker_max(flow: Flow, n: int, th: float) -> Gate:
    return _gate(lambda d, b: taker_buy_ratio(flow.get(b) or [], d, n), lambda v: v <= th)


def gate_taker_z(flow: Flow, lo: float | None, hi: float | None, n: int = 3) -> Gate:
    def ok(v: float) -> bool:
        return (lo is None or v >= lo) and (hi is None or v <= hi)

    return _gate(lambda d, b: taker_ratio_z(flow.get(b) or [], d, n), ok)


def gate_delta_min(flow: Flow, n: int, th: float) -> Gate:
    return _gate(lambda d, b: net_delta_share(flow.get(b) or [], d, n), lambda v: v >= th)


def gate_trades_min(flow: Flow, th: float, n: int = 3) -> Gate:
    return _gate(lambda d, b: trade_count_ratio(flow.get(b) or [], d, n), lambda v: v >= th)


def gate_trades_max(flow: Flow, th: float, n: int = 3) -> Gate:
    return _gate(lambda d, b: trade_count_ratio(flow.get(b) or [], d, n), lambda v: v <= th)


def scale_ext(ohlc: Ohlc, n: int, lo: float, hi: float, floor: float = 0.25) -> Scale:
    """Full size at/below ``lo`` extension, ``floor`` size at/above ``hi``."""

    def f(date: str, base: str) -> float:
        v = ext_sma(ohlc, base, date, n)
        if v is None or v <= lo:
            return 1.0
        if v >= hi:
            return floor
        return 1.0 - (1.0 - floor) * (v - lo) / (hi - lo)

    return f


def scale_taker(flow: Flow, n: int, lo: float, hi: float, floor: float = 0.25) -> Scale:
    def f(date: str, base: str) -> float:
        v = taker_buy_ratio(flow.get(base) or [], date, n)
        if v is None or v >= hi:
            return 1.0
        if v <= lo:
            return floor
        return floor + (1.0 - floor) * (v - lo) / (hi - lo)

    return f


# ---------- exit policies ----------
def _p(name: str, **kw: Any) -> ExitPolicy:
    return replace(LIVE_POLICY, name=name, **kw)


def cashing_policies() -> list[ExitPolicy]:
    out = [
        LIVE_POLICY,
        _p("trail6", alt_trail_pct=0.06),
        _p("trail8", alt_trail_pct=0.08),
        _p("trail12", alt_trail_pct=0.12),
        _p("trail15", alt_trail_pct=0.15),
        _p("trail10_nolock", alt_lock_arm_pct=0.0, alt_lock_floor_pct=0.0),
        _p("lock8_3", alt_lock_arm_pct=0.08, alt_lock_floor_pct=0.03),
        _p("lock10_5", alt_lock_arm_pct=0.10, alt_lock_floor_pct=0.05),
        _p("lock15_10", alt_lock_arm_pct=0.15, alt_lock_floor_pct=0.10),
        _p("lock20_12", alt_lock_arm_pct=0.20, alt_lock_floor_pct=0.12),
        _p("atr2.5", alt_atr_k=2.5),
        _p("atr3.5", alt_atr_k=3.5),
        _p("tp15", alt_tp_pct=0.15),
        _p("tp20", alt_tp_pct=0.20),
        _p("tp30", alt_tp_pct=0.30),
        _p("tp50", alt_tp_pct=0.50),
        _p("half_at15", alt_partial_tp_pct=0.15, alt_partial_frac=0.5),
        _p("half_at20", alt_partial_tp_pct=0.20, alt_partial_frac=0.5),
        _p("half_at30", alt_partial_tp_pct=0.30, alt_partial_frac=0.5),
        _p("third_at25", alt_partial_tp_pct=0.25, alt_partial_frac=0.34),
        _p(
            "spike8_30_gb4",
            alt_spike_arm_pct=0.08,
            alt_spike_max_pct=0.30,
            alt_spike_giveback_pct=0.04,
        ),
        _p(
            "spike10_40_gb5_sameday",
            alt_spike_arm_pct=0.10,
            alt_spike_max_pct=0.40,
            alt_spike_giveback_pct=0.05,
            alt_spike_same_day=True,
        ),
        _p(
            "spike15_gb6_half",
            alt_spike_arm_pct=0.15,
            alt_spike_max_pct=0.0,
            alt_spike_giveback_pct=0.06,
            alt_spike_frac=0.5,
        ),
        _p("time5", alt_max_days=5),
        _p("time10", alt_max_days=10),
        _p("donch10", alt_donch_n=10),
    ]
    return out


# ---------- variant grid ----------
def default_variants(ohlc: Ohlc, flow: Flow | None) -> list[Variant]:
    rows: list[Variant] = [("baseline", "baseline", None, None, {})]
    rows += [
        (f"ext20_max{int(t * 100)}", "location", gate_ext_max(ohlc, 20, t), None, {})
        for t in (0.08, 0.12, 0.16, 0.20, 0.30)
    ]
    rows += [
        (f"ext50_max{int(t * 100)}", "location", gate_ext_max(ohlc, 50, t), None, {})
        for t in (0.15, 0.25, 0.40)
    ]
    rows += [
        (f"offhigh60_atleast{int(t * 100)}", "location", gate_off_high_min(ohlc, t), None, {})
        for t in (0.05, 0.10, 0.20)
    ]
    rows += [
        (f"offhigh60_within{int(t * 100)}", "location", gate_off_high_max(ohlc, t), None, {})
        for t in (0.05, 0.10)
    ]
    rows += [
        (f"range20_max{int(t * 100)}", "location", gate_range_pos_max(ohlc, t), None, {})
        for t in (0.60, 0.80, 0.90)
    ]
    rows += [(f"rsi_max{t}", "location", gate_rsi_max(ohlc, t), None, {}) for t in (60, 70, 80)]
    rows += [
        (f"yday_retrace_max{int(t * 100)}", "location", gate_ret1_min(ohlc, t), None, {})
        for t in (0.03, 0.05, 0.08)
    ]
    rows += [
        (f"yday_run_max{int(t * 100)}", "location", gate_ret1_max(ohlc, t), None, {})
        for t in (0.05, 0.10)
    ]
    rows += [
        ("ext20_scale8_25", "location", None, scale_ext(ohlc, 20, 0.08, 0.25), {}),
        ("nearhigh0", "location", None, None, {"near_high_weight": 0.0}),
        (
            "nearhigh0_offhigh10",
            "location",
            gate_off_high_min(ohlc, 0.10),
            None,
            {"near_high_weight": 0.0},
        ),
        ("skip0", "location", None, None, {"skip_days": 0}),
        ("skip2", "location", None, None, {"skip_days": 2}),
    ]
    rows += [
        (f"impulse_max{int(t * 100)}", "impulse", gate_impulse_max(ohlc, t), None, {})
        for t in (0.50, 0.70, 0.90)
    ]
    rows += [
        (f"volsurge3_max{t}", "impulse", gate_volume_surge_max(ohlc, t), None, {})
        for t in (2.0, 3.0, 5.0)
    ]
    rows += [("volsurge3_min2", "impulse", gate_volume_surge_min(ohlc, 2.0), None, {})]
    if flow:
        rows += [
            (f"taker{n}_min{int(t * 100)}", "flow", gate_taker_min(flow, n, t), None, {})
            for n in (1, 3, 10)
            for t in (0.48, 0.50, 0.52)
        ]
        rows += [
            (f"taker{n}_max{int(t * 100)}", "flow", gate_taker_max(flow, n, t), None, {})
            for n in (1, 3)
            for t in (0.50, 0.55)
        ]
        rows += [
            ("takerz3_min0.5", "flow", gate_taker_z(flow, 0.5, None), None, {}),
            ("takerz3_min1", "flow", gate_taker_z(flow, 1.0, None), None, {}),
            ("takerz3_max1", "flow", gate_taker_z(flow, None, 1.0), None, {}),
            ("takerz3_band", "flow", gate_taker_z(flow, -1.0, 1.0), None, {}),
            ("delta10_pos", "flow", gate_delta_min(flow, 10, 0.0), None, {}),
            ("delta3_min2", "flow", gate_delta_min(flow, 3, 0.02), None, {}),
            ("trades3_min1.2", "flow", gate_trades_min(flow, 1.2), None, {}),
            ("trades3_min1.5", "flow", gate_trades_min(flow, 1.5), None, {}),
            ("trades3_max1.5", "flow", gate_trades_max(flow, 1.5), None, {}),
            ("trades3_max2.5", "flow", gate_trades_max(flow, 2.5), None, {}),
            ("taker3_scale46_54", "flow", None, scale_taker(flow, 3, 0.46, 0.54), {}),
        ]
    rows += [
        (f"exit_{p.name}", "cashing", None, None, {"policy": p})
        for p in cashing_policies()
        if p.name != LIVE_POLICY.name
    ]
    return rows


def run_variant(
    ohlc: Ohlc,
    *,
    name: str,
    gate: Gate | None,
    scale: Scale | None,
    extra: Mapping[str, Any],
    windows: Sequence[tuple[str, str, str]],
    book_eur: float = 20_000.0,
    keep_trades: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name}
    knobs = {**LIVE_KNOBS, **dict(extra)}
    for tag, start, end in windows:
        row = run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            entry_gate=gate,
            alt_size_scale=scale,
            strategy=name,
            keep_trades=keep_trades,
            **knobs,
        )
        out[tag] = {
            "pnl_eur": round(float(row["pnl_eur"]), 0),
            "max_dd_pct": round(float(row["max_dd_pct"]), 4),
            "n_trades": int(row["n_trades"]),
            "year_pnl": row.get("year_pnl"),
            "overlay_reasons": row.get("overlay_reasons"),
            **({"trades": row.get("trades")} if keep_trades else {}),
        }
    return out


def run_grid(
    ohlc: Ohlc,
    *,
    flow: Flow | None,
    windows: Sequence[tuple[str, str, str]],
    book_eur: float = 20_000.0,
    log: Callable[[str], None] | None = None,
    only_family: str | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for name, family, gate, scale, extra in default_variants(ohlc, flow):
        if only_family and family != only_family and family != "baseline":
            continue
        row = run_variant(
            ohlc, name=name, gate=gate, scale=scale, extra=extra, windows=windows, book_eur=book_eur
        )
        row["family"] = family
        rows.append(row)
        if log:
            full = row.get("full") or {}
            log(f"{family:9s} {name:28s} full {full.get('pnl_eur'):>9} dd {full.get('max_dd_pct')}")
    return rows


def features_for(ohlc: Ohlc, flow: Flow | None, base: str, date: str) -> dict[str, float | None]:
    """All entry features as the gates saw them on ``date`` (signal close)."""
    out: dict[str, float | None] = {
        "ext_sma20": ext_sma(ohlc, base, date, 20),
        "ext_sma50": ext_sma(ohlc, base, date, 50),
        "off_high60": off_high(ohlc, base, date, 60),
        "range_pos20": range_pos(ohlc, base, date),
        "rsi14": rsi(ohlc, base, date),
        "ret_1d": ret_days(ohlc, base, date, 1),
        "ret_10d": ret_days(ohlc, base, date, 10),
        "impulse_share": impulse_share(ohlc, base, date),
        "volume_surge3": volume_surge(ohlc, base, date),
    }
    if flow and base in flow:
        rows = flow[base]
        out.update(
            {
                "taker1": taker_buy_ratio(rows, date, 1),
                "taker3": taker_buy_ratio(rows, date, 3),
                "taker10": taker_buy_ratio(rows, date, 10),
                "taker_z3": taker_ratio_z(rows, date),
                "trades_ratio3": trade_count_ratio(rows, date),
            }
        )
    return out

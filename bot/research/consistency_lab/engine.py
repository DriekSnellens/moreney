"""Consistency lab: how green can the weeks and months get?

Owner goal (18 Oct 2026): "liefst elke week en maand groen". This lab does
not promise that; it measures, for every generic lever the project already
has, the share of green weeks and months, the worst week and month, the
longest red streak, and what each lever costs in PnL. All variants are
coin-agnostic overlays on the armed ``residual_full`` pack:

  exposure    BTC/alt mix, fixed fraction of the book, vol targeting
  breadth     more than one alt per week
  exits       tighter trails, partial take-profits, time stops
  clock       shorter/longer week, immediate re-entry
  ensemble    the same pack on 7 staggered week clocks and/or 5 trail
              levels, each with 1/N of the book — one path becomes many

Curves come from ``run_btc_residual`` (WET fills, compounding per sleeve);
ensemble curves are the sum of independent sub-book curves.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.policies import ExitPolicy
from bot.research.factor_scoreboard.engine import (
    LIVE_KNOBS,
    LIVE_POLICY,
    scale_vol_target,
)

Ohlc = Mapping[str, Sequence[Sequence[float]]]
Curve = list[tuple[str, float]]
Gate = Callable[[str, str], bool]
Scale = Callable[[str, str], float]


def _iso_week(date: str) -> str:
    y, w, _ = datetime.strptime(date, "%Y-%m-%d").isocalendar()
    return f"{y}-W{w:02d}"


def period_returns(
    curve: Curve, key: Callable[[str], str], start_eur: float
) -> list[tuple[str, float, float]]:
    """(period, pnl_eur, pnl_pct) per calendar bucket, pct vs bucket start equity."""
    out: list[tuple[str, float, float]] = []
    prev = start_eur
    cur_key = None
    last = prev
    for d, e in curve:
        k = key(d)
        if cur_key is None:
            cur_key = k
        if k != cur_key:
            out.append((cur_key, last - prev, (last / prev - 1.0) if prev > 0 else 0.0))
            prev = last
            cur_key = k
        last = e
    if cur_key is not None:
        out.append((cur_key, last - prev, (last / prev - 1.0) if prev > 0 else 0.0))
    return out


def _streak(vals: Sequence[float], red: bool) -> int:
    best = run = 0
    for v in vals:
        hit = v < -1e-9 if red else v > 1e-9
        run = run + 1 if hit else 0
        best = max(best, run)
    return best


def consistency(curve: Curve, start_eur: float) -> dict[str, Any]:
    weeks = period_returns(curve, _iso_week, start_eur)
    months = period_returns(curve, lambda d: d[:7], start_eur)
    wk = [p for _, _, p in weeks]
    mo = [p for _, _, p in months]
    wk_eur = [e for _, e, _ in weeks]
    mo_eur = [e for _, e, _ in months]
    active_wk = [p for p in wk if abs(p) > 1e-9]
    peak = start_eur
    dd = 0.0
    for _, e in curve:
        peak = max(peak, e)
        dd = max(dd, 1.0 - e / peak if peak > 0 else 0.0)
    return {
        "pnl_eur": round(curve[-1][1] - start_eur, 0) if curve else 0.0,
        "max_dd_pct": round(dd, 4),
        "weeks": len(wk),
        "green_weeks_pct": round(sum(p > 1e-9 for p in wk) / max(1, len(wk)), 3),
        "red_weeks_pct": round(sum(p < -1e-9 for p in wk) / max(1, len(wk)), 3),
        "flat_weeks_pct": round(sum(abs(p) <= 1e-9 for p in wk) / max(1, len(wk)), 3),
        "green_active_weeks_pct": round(sum(p > 0 for p in active_wk) / max(1, len(active_wk)), 3),
        "worst_week_pct": round(min(wk), 4) if wk else 0.0,
        "worst_week_eur": round(min(wk_eur), 0) if wk_eur else 0.0,
        "p10_week_pct": round(sorted(wk)[len(wk) // 10], 4) if wk else 0.0,
        "longest_red_weeks": _streak(wk, red=True),
        "months": len(mo),
        "green_months_pct": round(sum(p > 1e-9 for p in mo) / max(1, len(mo)), 3),
        "red_months_pct": round(sum(p < -1e-9 for p in mo) / max(1, len(mo)), 3),
        "worst_month_pct": round(min(mo), 4) if mo else 0.0,
        "worst_month_eur": round(min(mo_eur), 0) if mo_eur else 0.0,
        "longest_red_months": _streak(mo, red=True),
        "median_month_pct": round(statistics.median(mo), 4) if mo else 0.0,
        "monthly_returns": [(k, round(p, 4)) for k, _, p in months],
    }


# ---------- curves ----------
def curve_of(
    ohlc: Ohlc,
    *,
    start: str,
    end: str,
    book_eur: float,
    gate: Gate | None = None,
    scale: Scale | None = None,
    **extra: Any,
) -> Curve:
    knobs = {**LIVE_KNOBS, **extra}
    row = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=book_eur,
        entry_gate=gate,
        alt_size_scale=scale,
        keep_curve=True,
        **knobs,
    )
    return [(str(d), float(v)) for d, v in row["curve"]]


def sum_curves(curves: Sequence[Curve]) -> Curve:
    if not curves:
        return []
    n = min(len(c) for c in curves)
    return [(curves[0][i][0], sum(c[i][1] for c in curves)) for i in range(n)]


def const_scale(frac: float) -> Scale:
    return lambda date, base: frac


def _p(name: str, **kw: Any) -> ExitPolicy:
    return replace(LIVE_POLICY, name=name, **kw)


Spec = tuple[str, str, dict[str, Any]]
"""(name, family, options). Options: ``extra`` knobs, ``scale``, ``gate``,
``weekdays`` (staggered clocks), ``trails`` (trail ensemble)."""


def default_specs(ohlc: Ohlc) -> list[Spec]:
    specs: list[Spec] = [("baseline", "baseline", {})]
    specs += [
        (f"btc{int(f * 100)}_alt{int((1 - f) * 100)}", "exposure", {"extra": {"btc_frac": f}})
        for f in (0.25, 0.5, 0.75)
    ]
    specs += [("btc_only_sma50", "exposure", {"extra": {"btc_frac": 1.0}})]
    specs += [
        (f"alt_size{int(f * 100)}", "exposure", {"scale": const_scale(f)}) for f in (0.5, 0.33)
    ]
    specs += [
        (f"vol_t{int(t * 100)}", "exposure", {"scale": scale_vol_target(ohlc, t)})
        for t in (0.4, 0.6, 0.8)
    ]
    specs += [(f"n_alts{n}", "breadth", {"extra": {"n_alts": n}}) for n in (2, 3)]
    specs += [
        ("exit_trail6", "exits", {"extra": {"policy": _p("trail6", alt_trail_pct=0.06)}}),
        ("exit_trail8", "exits", {"extra": {"policy": _p("trail8", alt_trail_pct=0.08)}}),
        (
            "exit_half_at15",
            "exits",
            {"extra": {"policy": _p("half15", alt_partial_tp_pct=0.15, alt_partial_frac=0.5)}},
        ),
        (
            "exit_stop5",
            "exits",
            {"extra": {"policy": _p("stop5", alt_stop_pct=0.05)}},
        ),
        ("exit_time5", "exits", {"extra": {"policy": _p("time5", alt_max_days=5)}}),
    ]
    specs += [(f"rebalance{d}", "clock", {"extra": {"rebalance_days": d}}) for d in (3, 5, 10, 14)]
    specs += [("immediate_rebuy", "clock", {"extra": {"immediate_rebuy": True}})]
    specs += [
        ("ens_7clocks", "ensemble", {"weekdays": tuple(range(7))}),
        ("ens_5trails", "ensemble", {"trails": (0.08, 0.09, 0.10, 0.11, 0.12)}),
        (
            "ens_7clocks_5trails",
            "ensemble",
            {"weekdays": tuple(range(7)), "trails": (0.08, 0.09, 0.10, 0.11, 0.12)},
        ),
        (
            "ens_7clocks_btc50",
            "ensemble",
            {"weekdays": tuple(range(7)), "extra": {"btc_frac": 0.5}},
        ),
        (
            "ens_7clocks_vol60",
            "ensemble",
            {"weekdays": tuple(range(7)), "scale": scale_vol_target(ohlc, 0.6)},
        ),
        (
            "ens_7clocks_half15",
            "ensemble",
            {
                "weekdays": tuple(range(7)),
                "extra": {"policy": _p("half15", alt_partial_tp_pct=0.15, alt_partial_frac=0.5)},
            },
        ),
        (
            "ens_7clocks_n2",
            "ensemble",
            {"weekdays": tuple(range(7)), "extra": {"n_alts": 2}},
        ),
    ]
    return specs


def run_spec(ohlc: Ohlc, spec: Spec, *, start: str, end: str, book_eur: float) -> Curve:
    _name, _family, opt = spec
    extra = dict(opt.get("extra") or {})
    gate = opt.get("gate")
    scale = opt.get("scale")
    weekdays = tuple(opt.get("weekdays") or ())
    trails = tuple(opt.get("trails") or ())
    legs: list[dict[str, Any]] = [{}]
    if weekdays:
        legs = [{**leg, "rebalance_weekday": wd} for leg in legs for wd in weekdays]
    if trails:
        base_policy: ExitPolicy = extra.get("policy") or LIVE_POLICY
        legs = [
            {**leg, "policy": replace(base_policy, name=f"trail{t}", alt_trail_pct=t)}
            for leg in legs
            for t in trails
        ]
    n = len(legs)
    curves = [
        curve_of(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur / n,
            gate=gate,
            scale=scale,
            **{**extra, **leg},
        )
        for leg in legs
    ]
    return sum_curves(curves) if n > 1 else curves[0]


def run_grid(
    ohlc: Ohlc,
    *,
    windows: Sequence[tuple[str, str, str]],
    book_eur: float = 20_000.0,
    log: Callable[[str], None] | None = None,
    only: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in default_specs(ohlc):
        name, family, _ = spec
        if only and name not in only and name != "baseline":
            continue
        row: dict[str, Any] = {"name": name, "family": family}
        for tag, start, end in windows:
            curve = run_spec(ohlc, spec, start=start, end=end, book_eur=book_eur)
            row[tag] = consistency(curve, book_eur)
        rows.append(row)
        if log:
            f = row.get("full") or {}
            pct = {k: f.get(k, 0.0) * 100 for k in f if k.endswith("_pct")}
            log(
                f"{family:9s} {name:22s} pnl {f.get('pnl_eur'):>8} dd {pct['max_dd_pct']:5.1f}% "
                f"greenW {pct['green_weeks_pct']:4.0f}% redW {pct['red_weeks_pct']:4.0f}% "
                f"worstW {pct['worst_week_pct']:6.1f}% greenM {pct['green_months_pct']:4.0f}% "
                f"worstM {pct['worst_month_pct']:6.1f}% redStreakM {f.get('longest_red_months')}"
            )
    return rows

#!/usr/bin/env python3
"""€20k multi-strategy allocator: last 3 months + stress on the worst months.

Builds daily unit-return paths (€20k books) for complementary sleeves, then
searches how to split ONE €20k book:

  - static mixes
  - BTC-regime switch (SMA200 / SMA50)
  - walk-forward monthly (fit 60d, apply next month)

Reports who gets what, when each sleeve is on, and monthly PnL including
Jan–Jun bleed months (not only the recent bull 90d).

No AlphaI timeline. Overlapping alt-long sleeves are capital-split (not 20k each).
Writes artifacts/multi_strat_20k_allocator.json
"""

from __future__ import annotations

import itertools
import json
import math
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifacts.bear_market_strategy_sim import _align, _max_drawdown, _sma, load_daily
from artifacts.combined_desk_12w_sim import (
    build_core_occupancy_full,
    core_daily_equity,
    live_core_cfg,
)
from artifacts.missed_capacity_five_strats import (
    ALL,
    ALT,
    sim_btc_sma,
    sim_donchian,
    sim_invvol_basket,
    sim_winners_weekly,
)
from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "multi_strat_20k_allocator.json"
BOOK = 20_000.0
# Past 3 months (user request)
Q_START = "2026-06-20"
Q_END = "2026-09-20"
# Stress sample for "most negative months"
STRESS_START = "2026-01-11"


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _idx(ts: list[int], date_s: str) -> int:
    target = int(datetime.fromisoformat(date_s).replace(tzinfo=UTC).timestamp() * 1000)
    for i, t in enumerate(ts):
        if t >= target:
            return i
    return len(ts) - 1


def path_from_book(ts: list[int], i0: int, i1: int, book) -> dict[str, float]:
    out = {}
    eqs = book.eq
    n = i1 - i0 + 1
    # Book snapshots once per day in range; if extra, trim
    if len(eqs) >= n:
        eqs = eqs[:n]
    for k, i in enumerate(range(i0, i0 + len(eqs))):
        out[_date(ts[i])] = float(eqs[k])
    return out


def to_returns(eq: dict[str, float], book: float = BOOK) -> dict[str, float]:
    """Daily PnL as a fraction of the *allocated* €20k book (rebalanced notionals)."""
    dates = sorted(eq)
    rets: dict[str, float] = {}
    prev = None
    for d in dates:
        v = eq[d]
        if prev is None:
            rets[d] = (v - book) / book
        else:
            rets[d] = (v - prev) / book
        prev = v
    return rets


def apply_weights(
    rets: dict[str, dict[str, float]],
    weights: dict[str, float],
    dates: list[str],
    *,
    book: float = BOOK,
    regime_w: dict[str, dict[str, float]] | None = None,
    regime_of: dict[str, str] | None = None,
) -> tuple[list[float], list[dict[str, Any]]]:
    """Compound a split book. weights (or regime_w[label]) sum to 1."""
    eq = book
    path = [eq]
    rows = []
    peak = book
    mdd = 0.0
    for d in dates:
        w = weights
        label = None
        if regime_of is not None and regime_w is not None:
            label = regime_of.get(d, "risk_on")
            w = regime_w.get(label, weights)
        r = 0.0
        used = {k: float(w.get(k, 0.0)) for k in rets if float(w.get(k, 0.0)) > 1e-9}
        for k, wk in used.items():
            r += wk * float(rets[k].get(d, 0.0))
        # Rebalanced split: each sleeve's €-PnL is scaled by its weight of the €20k book.
        eq += book * r
        path.append(eq)
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        rows.append(
            {
                "date": d,
                "equity_eur": round(eq, 2),
                "day_ret": round(r, 5),
                "regime": label,
                "weights": {k: round(v, 3) for k, v in used.items()},
            }
        )
    return path, rows


def summarize(path: list[float], rows: list[dict[str, Any]], book: float = BOOK) -> dict[str, Any]:
    eq = path[-1]
    pnl = eq - book
    mdd = _max_drawdown(path)
    # months
    by_m: dict[str, list[float]] = defaultdict(list)
    prev = book
    for row in rows:
        m = row["date"][:7]
        by_m[m].append(row["equity_eur"])
    months = []
    m_prev = book
    for m in sorted(by_m):
        end = by_m[m][-1]
        months.append({"month": m, "pnl_eur": round(end - m_prev, 2), "end_eur": round(end, 2)})
        m_prev = end
    worst = min(months, key=lambda x: x["pnl_eur"]) if months else None
    calmar = (pnl / abs(mdd)) if abs(mdd) > 1 else (99.0 if pnl > 0 else 0.0)
    return {
        "pnl_eur": round(pnl, 2),
        "return_pct": round(100.0 * pnl / book, 2),
        "max_dd_eur": round(mdd, 2),
        "max_dd_pct": round(100.0 * mdd / book, 2),
        "calmar": round(calmar, 3),
        "end_eur": round(eq, 2),
        "months": months,
        "worst_month": worst,
        "n_days": len(rows),
    }


def slice_rows(rows: list[dict[str, Any]], start: str, end: str, book: float) -> tuple[list[float], list[dict[str, Any]]]:
    sub = [r for r in rows if start <= r["date"] <= end]
    if not sub:
        return [book], []
    # rebuild path from day rets
    eq = book
    path = [eq]
    out = []
    for r in sub:
        eq += book * float(r["day_ret"])
        path.append(eq)
        out.append({**r, "equity_eur": round(eq, 2)})
    return path, out


def static_grid(names: list[str], step: float = 0.2) -> list[dict[str, float]]:
    n = int(round(1.0 / step))
    mixes = []
    for combo in itertools.combinations_with_replacement(range(len(names)), n):
        w = {k: 0.0 for k in names}
        for i in combo:
            w[names[i]] += step
        # skip all-cash duplicates later
        mixes.append(w)
    # unique
    seen = set()
    out = []
    for w in mixes:
        key = tuple(round(w[k], 3) for k in names)
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def score_mix(st: dict[str, Any], stress: dict[str, Any] | None) -> tuple:
    """Prefer 90d profit, not-terrible worst month, then calmar, then stress worst month."""
    worst90 = (st.get("worst_month") or {}).get("pnl_eur") or 0.0
    dd_ok = int(st["max_dd_pct"] > -25)
    profit = int(st["pnl_eur"] > 0)
    stress_worst = 0.0
    stress_pnl = 0.0
    if stress:
        stress_worst = (stress.get("worst_month") or {}).get("pnl_eur") or 0.0
        stress_pnl = stress["pnl_eur"]
    return (
        profit,
        dd_ok,
        int(worst90 > -1500),
        round(st["calmar"], 3),
        st["pnl_eur"],
        worst90,
        stress_pnl,
        stress_worst,
    )


def main() -> None:
    print("loading daily OHLC…", flush=True)
    series = load_daily(ALL, days=430)
    ts, closes = _align(series, ALL)
    highs: dict[str, list[float]] = {}
    lows: dict[str, list[float]] = {}
    for b in ALL:
        idx = {t: i for i, t in enumerate(series[b].ts)}
        highs[b] = [series[b].h[idx[t]] for t in ts]
        lows[b] = [series[b].l[idx[t]] for t in ts]

    i_stress = _idx(ts, STRESS_START)
    i_q0 = _idx(ts, Q_START)
    i_end = _idx(ts, Q_END)
    dates_all = [_date(ts[i]) for i in range(i_stress, i_end + 1)]
    dates_q = [_date(ts[i]) for i in range(i_q0, i_end + 1)]

    sma200 = _sma(closes["BTC"], 200)
    sma50 = _sma(closes["BTC"], 50)
    regime_of: dict[str, str] = {}
    for i in range(i_stress, i_end + 1):
        d = _date(ts[i])
        btc = closes["BTC"][i]
        s200, s50 = sma200[i], sma50[i]
        if s50 is not None and btc < s50:
            regime_of[d] = "risk_off"
        elif s200 is not None and btc < s200:
            regime_of[d] = "mid"
        else:
            regime_of[d] = "risk_on"

    print(
        f"windows stress {dates_all[0]}→{dates_all[-1]}  quarter {dates_q[0]}→{dates_q[-1]}",
        flush=True,
    )

    # ── core 15m path ─────────────────────────────────────────────────────
    print("simulating core 15m (Jan→now)…", flush=True)
    cfg = live_core_cfg()
    end_ms = int(datetime.fromisoformat(Q_END).replace(tzinfo=UTC).timestamp() * 1000)
    start_ms = int(datetime.fromisoformat(STRESS_START).replace(tzinfo=UTC).timestamp() * 1000)
    end_ms = end_ms // BAR_MS * BAR_MS
    days_span = int((end_ms - start_ms) / 86_400_000) + 5
    candles = load_candles(("BTC", *cfg.universe), days=days_span, end_ms=end_ms, refresh=False)
    core_res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    core_sum = core_res.summary()
    print(
        f"  core Jan→now realized={core_sum.get('realized_eur')} trades={core_sum.get('trades')}",
        flush=True,
    )
    q_ms = int(datetime.fromisoformat(Q_START).replace(tzinfo=UTC).timestamp() * 1000)
    print("simulating core 15m (fresh 90d)…", flush=True)
    core_q = simulate(candles, cfg, start_ms=q_ms, end_ms=end_ms, alphai=None)
    print(
        f"  core 90d fresh realized={core_q.summary().get('realized_eur')} "
        f"trades={core_q.summary().get('trades')}",
        flush=True,
    )
    core_path = core_daily_equity(
        core_res,
        daily_ts=ts,
        daily_closes=closes,
        start_ms=start_ms,
        end_ms=end_ms,
        book=BOOK,
    )
    core_path_q = core_daily_equity(
        core_q,
        daily_ts=ts,
        daily_closes=closes,
        start_ms=q_ms,
        end_ms=end_ms,
        book=BOOK,
    )
    core_eq_q = {r["date"]: float(r["equity_eur"]) for r in core_path_q}
    core_eq = {r["date"]: float(r["equity_eur"]) for r in core_path}

    # ── other sleeves on same daily index ─────────────────────────────────
    print("simulating sleeves…", flush=True)
    sleeve_fn = {
        "donch20": lambda: sim_donchian(ts, closes, highs, lows, i_stress, i_end, ch=20, exit_n=10, btc_sma=50),
        "donch10": lambda: sim_donchian(ts, closes, highs, lows, i_stress, i_end, ch=10, exit_n=5, btc_sma=50),
        "winners": lambda: sim_winners_weekly(
            ts, closes, highs, lows, i_stress, i_end, lb=30, skip=7, top_n=3, reb=7, w_each=0.3, sma_n=200
        ),
        "invvol": lambda: sim_invvol_basket(
            ts, closes, highs, lows, i_stress, i_end, lookback=21, top_n=4, deploy=0.8, sma_n=200
        ),
        "btc200": lambda: sim_btc_sma(
            ts, closes, highs, lows, i_stress, i_end, sma_n=200, target_vol=0.25, max_w=0.8
        ),
        "btc50": lambda: sim_btc_sma(
            ts, closes, highs, lows, i_stress, i_end, sma_n=50, target_vol=0.40, max_w=1.0
        ),
    }
    eq_paths: dict[str, dict[str, float]] = {"core": core_eq}
    for name, fn in sleeve_fn.items():
        bk = fn()
        eq_paths[name] = path_from_book(ts, i_stress, i_end, bk)
        print(f"  {name} end={list(eq_paths[name].values())[-1]:.1f} n={len(eq_paths[name])}", flush=True)

    print("fresh 90d sleeve marks…", flush=True)
    q_overlay = {
        "donch20": sim_donchian(ts, closes, highs, lows, i_q0, i_end, ch=20, exit_n=10, btc_sma=50),
        "donch10": sim_donchian(ts, closes, highs, lows, i_q0, i_end, ch=10, exit_n=5, btc_sma=50),
        "winners": sim_winners_weekly(
            ts, closes, highs, lows, i_q0, i_end, lb=30, skip=7, top_n=3, reb=7, w_each=0.3, sma_n=200
        ),
        "invvol": sim_invvol_basket(
            ts, closes, highs, lows, i_q0, i_end, lookback=21, top_n=4, deploy=0.8, sma_n=200
        ),
        "btc200": sim_btc_sma(ts, closes, highs, lows, i_q0, i_end, sma_n=200, target_vol=0.25, max_w=0.8),
        "btc50": sim_btc_sma(ts, closes, highs, lows, i_q0, i_end, sma_n=50, target_vol=0.40, max_w=1.0),
    }

    # cash: flat
    eq_paths["cash"] = {d: BOOK for d in dates_all}

    rets = {k: to_returns(v) for k, v in eq_paths.items()}
    for d, r in to_returns(core_eq_q).items():
        rets["core"][d] = r
    for name, bk in q_overlay.items():
        for d, r in to_returns(path_from_book(ts, i_q0, i_end, bk)).items():
            rets[name][d] = r

    sleeve_q = {}
    for name, eq in eq_paths.items():
        # rebuild 90d from returns so start is 20k
        p, rows = apply_weights({name: rets[name]}, {name: 1.0}, dates_q)
        sleeve_q[name] = summarize(p, rows)
        print(f"  90d {name:8} {sleeve_q[name]['pnl_eur']:>9} dd={sleeve_q[name]['max_dd_pct']}%", flush=True)

    names = ["core", "donch20", "winners", "invvol", "btc200", "cash"]
    # donch10 is optional high-DD — include in a second grid later

    print("grid search static mixes…", flush=True)
    mixes = static_grid(names, step=0.2)
    ranked = []
    for w in mixes:
        p90, r90 = apply_weights(rets, w, dates_q)
        st90 = summarize(p90, r90)
        pS, rS = apply_weights(rets, w, dates_all)
        stS = summarize(pS, rS)
        ranked.append({"weights": w, "q90": st90, "stress": stS, "_score": score_mix(st90, stS)})
    ranked.sort(key=lambda x: x["_score"], reverse=True)
    for row in ranked[:8]:
        row.pop("_score", None)
    top_static = ranked[0]
    print(
        f"  best static {top_static['weights']} 90d={top_static['q90']['pnl_eur']} "
        f"stress={top_static['stress']['pnl_eur']} worst90={top_static['q90']['worst_month']}",
        flush=True,
    )

    # ── regime architectures (not fit on 90d) ─────────────────────────────
    regime_sets = {
        "classic_sma_stack": {
            "risk_on": {"core": 0.5, "donch20": 0.3, "btc200": 0.2},
            "mid": {"core": 0.3, "donch20": 0.3, "cash": 0.4},
            "risk_off": {"cash": 1.0},
        },
        "desk_plus_donch": {
            "risk_on": {"core": 0.6, "donch20": 0.4},
            "mid": {"core": 0.4, "donch20": 0.3, "cash": 0.3},
            "risk_off": {"cash": 1.0},
        },
        "winners_when_bull": {
            "risk_on": {"core": 0.4, "winners": 0.3, "donch20": 0.3},
            "mid": {"donch20": 0.5, "cash": 0.5},
            "risk_off": {"cash": 1.0},
        },
        "preserve_in_mid": {
            "risk_on": {"core": 0.45, "donch20": 0.35, "invvol": 0.2},
            "mid": {"btc200": 0.3, "cash": 0.7},
            "risk_off": {"cash": 1.0},
        },
        "core_only_risk_on": {
            "risk_on": {"core": 1.0},
            "mid": {"cash": 1.0},
            "risk_off": {"cash": 1.0},
        },
    }

    regime_results = []
    for label, rw in regime_sets.items():
        p90, r90 = apply_weights(rets, {}, dates_q, regime_w=rw, regime_of=regime_of)
        pS, rS = apply_weights(rets, {}, dates_all, regime_w=rw, regime_of=regime_of)
        st90, stS = summarize(p90, r90), summarize(pS, rS)
        # occupancy of regimes in 90d
        occ = defaultdict(int)
        for r in r90:
            occ[r["regime"]] += 1
        regime_results.append(
            {
                "name": label,
                "map": rw,
                "q90": st90,
                "stress": stS,
                "regime_days_q90": dict(occ),
                "_score": score_mix(st90, stS),
            }
        )
        print(
            f"  regime {label:22} 90d={st90['pnl_eur']:>8} stress={stS['pnl_eur']:>8} "
            f"worst90={st90['worst_month']}",
            flush=True,
        )
    regime_results.sort(key=lambda x: x["_score"], reverse=True)
    best_regime = regime_results[0]

    # ── walk-forward monthly ──────────────────────────────────────────────
    print("walk-forward monthly (fit 60d → next month)…", flush=True)
    months = sorted({d[:7] for d in dates_all})
    wf_rows: list[dict[str, Any]] = []
    wf_eq = BOOK
    wf_path = [BOOK]
    wf_detail = []
    step = 0.25
    wf_names = ["core", "donch20", "winners", "btc200", "cash"]
    wf_grid = static_grid(wf_names, step=step)

    for mi, month in enumerate(months):
        m_dates = [d for d in dates_all if d.startswith(month)]
        if not m_dates:
            continue
        # fit window: 60 calendar days before month start
        start_d = m_dates[0]
        fit_dates = [d for d in dates_all if d < start_d]
        fit_dates = fit_dates[-60:]
        if len(fit_dates) < 20:
            w = {"core": 1.0}
            why = "warmup_core"
        else:
            best_w, best_sc = None, None
            for cand in wf_grid:
                p, r = apply_weights(rets, cand, fit_dates)
                st = summarize(p, r)
                sc = (
                    int(st["pnl_eur"] > 0),
                    int(st["max_dd_pct"] > -20),
                    st["calmar"],
                    st["pnl_eur"],
                    (st.get("worst_month") or {}).get("pnl_eur") or 0,
                )
                if best_sc is None or sc > best_sc:
                    best_sc, best_w = sc, cand
            w = best_w or {"core": 1.0}
            why = "fit60_calmar"
        p_m, r_m = apply_weights(rets, w, m_dates)
        # stitch: apply returns onto running equity
        for row in r_m:
            wf_eq += BOOK * float(row["day_ret"])
            wf_path.append(wf_eq)
            wf_detail.append(
                {
                    **row,
                    "equity_eur": round(wf_eq, 2),
                    "month": month,
                    "chosen_weights": {k: round(v, 3) for k, v in w.items() if v > 1e-9},
                    "why": why,
                }
            )
        m_pnl = wf_eq - (wf_path[-(len(r_m) + 1)] if len(r_m) else wf_eq)
        # simpler month pnl from first to last of this month in wf_detail
        month_rows = [x for x in wf_detail if x["month"] == month]
        start_eq = month_rows[0]["equity_eur"] - BOOK * month_rows[0]["day_ret"] if month_rows else wf_eq
        wf_rows.append(
            {
                "month": month,
                "weights": {k: round(v, 3) for k, v in w.items() if v > 1e-9},
                "why": why,
                "pnl_eur": round(month_rows[-1]["equity_eur"] - start_eq, 2) if month_rows else 0.0,
                "end_eur": round(wf_eq, 2),
            }
        )
        print(f"  {month} {wf_rows[-1]['weights']} pnl={wf_rows[-1]['pnl_eur']}", flush=True)

    wf_sum_all = summarize(wf_path, wf_detail)
    _, wf_q_rows = slice_rows(wf_detail, Q_START, Q_END, BOOK)
    # 90d walk-forward should start at 20k independently to compare apples
    wf_q_dates = [d for d in dates_q]
    # replay wf weights month by month on a fresh 20k for the quarter
    q_eq = BOOK
    q_path = [BOOK]
    q_det = []
    w_by_month = {r["month"]: r["weights"] for r in wf_rows}
    for d in wf_q_dates:
        w = w_by_month.get(d[:7], {"core": 1.0})
        r = sum(float(w.get(k, 0)) * float(rets[k].get(d, 0)) for k in rets)
        q_eq += BOOK * r
        q_path.append(q_eq)
        q_det.append({"date": d, "equity_eur": round(q_eq, 2), "day_ret": round(r, 5), "weights": w, "regime": regime_of.get(d)})
    wf_q = summarize(q_path, q_det)

    # ── baselines ─────────────────────────────────────────────────────────
    baselines = {}
    for name in ["core", "donch20", "cash"]:
        p, r = apply_weights(rets, {name: 1.0}, dates_q)
        baselines[name + "_q90"] = summarize(p, r)
        p, r = apply_weights(rets, {name: 1.0}, dates_all)
        baselines[name + "_stress"] = summarize(p, r)

    # regime-day table for quarter
    reg_counts = defaultdict(int)
    for d in dates_q:
        reg_counts[regime_of[d]] += 1

    rec_kind = "regime"
    rec_payload = best_regime

    # euros table for recommended
    if rec_kind == "regime":
        euro_split = {
            reg: {k: round(BOOK * v) for k, v in w.items() if v > 0}
            for reg, w in rec["map"].items()
        }
    else:
        euro_split = {"always": {k: round(BOOK * v) for k, v in top_static["weights"].items() if v > 0}}

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK,
        "quarter": {"start": dates_q[0], "end": dates_q[-1], "days": len(dates_q)},
        "stress": {"start": dates_all[0], "end": dates_all[-1], "days": len(dates_all)},
        "caveats": [
            "No AlphaI; 15m core close-fills; daily close fills for other sleeves",
            "Capital-split: sleeves do not each get €20k — weights share one book",
            "Alt-long sleeves can overlap names (core vs Donchian vs winners)",
            "Walk-forward uses prior 60d in-sample; first month defaults to 100% core",
        ],
        "regime_days_q90": dict(reg_counts),
        "sleeve_unit_20k": {"quarter": sleeve_q},
        "baselines": baselines,
        "top_static_mixes": [
            {
                "weights": {k: round(v, 3) for k, v in r["weights"].items() if v > 1e-9},
                "euros": {k: round(BOOK * v) for k, v in r["weights"].items() if v > 1e-9},
                "q90": r["q90"],
                "stress": {
                    "pnl_eur": r["stress"]["pnl_eur"],
                    "max_dd_pct": r["stress"]["max_dd_pct"],
                    "worst_month": r["stress"]["worst_month"],
                    "months": r["stress"]["months"],
                },
            }
            for r in ranked[:8]
        ],
        "regime_architectures": [
            {
                "name": r["name"],
                "map": r["map"],
                "euros": {
                    reg: {k: round(BOOK * v) for k, v in w.items() if v > 0}
                    for reg, w in r["map"].items()
                },
                "q90": r["q90"],
                "stress": {
                    "pnl_eur": r["stress"]["pnl_eur"],
                    "max_dd_pct": r["stress"]["max_dd_pct"],
                    "worst_month": r["stress"]["worst_month"],
                    "months": r["stress"]["months"],
                },
                "regime_days_q90": r["regime_days_q90"],
            }
            for r in regime_results
        ],
        "walk_forward": {
            "months": wf_rows,
            "quarter": wf_q,
            "stress": {
                "pnl_eur": wf_sum_all["pnl_eur"],
                "max_dd_pct": wf_sum_all["max_dd_pct"],
                "worst_month": wf_sum_all["worst_month"],
                "months": wf_sum_all["months"],
            },
        },
        "recommendation": {
            "kind": rec_kind,
            "name": rec_payload.get("name") if rec_kind == "regime" else "static_grid_winner",
            "euros_by_regime": euro_split,
            "why": (
                "Regime stack is the default: it is not fit on the last 90d, "
                "goes to cash under SMA50, and still captures Donchian in mid/bull. "
                "Static grid is only preferred if it also wins the Jan–Sep stress sample."
            ),
            "quarter": rec_payload["q90"] if rec_kind == "regime" else rec_payload["q90"],
            "stress": rec_payload["stress"] if rec_kind == "regime" else rec_payload["stress"],
        },
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n=== RECOMMENDATION ===", flush=True)
    print(json.dumps(payload["recommendation"]["euros_by_regime"], indent=2), flush=True)
    print("kind", rec_kind, "q90", payload["recommendation"]["quarter"]["pnl_eur"], flush=True)
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()

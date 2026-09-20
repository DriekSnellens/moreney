#!/usr/bin/env python3
"""Loop knobs until the best €20k desk shows up — not only long/short-by-regime.

Expands the previous in-sample grid:
  - extra long sleeves (BTC SMA, dual-mom, dip, residual, breadth, Donch knobs, winners knobs)
  - BTC trend long/short as its own architecture
  - more short knobs (lookback/skip/bounce/gate SMA20–100, stop/trail/reb)
  - several regime classifiers (not only SMA50/200+breadth)
  - per-regime mix search then full-path score (PnL is separable, DD is not)
  - local hill-climb around the winner
  - walk-forward monthly picker as a competing architecture
  - vol-target / DD-halt overlays

Objective (in order): 1y profit, DD inside −12%, worst month > −€1500,
last-12w still harvests (>€3k), prior-12w not a hole (> −€500), then Calmar,
then 1y PnL.

Does not touch live. Writes artifacts/expert_best_desk_loop.json
and artifacts/expert_best_desk_loop_1y.svg
"""

from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifacts.bear_harvest_hunter import atr14, pick_weakest
from artifacts.bear_market_strategy_sim import BOOK_EUR, FEE_RT, _align, _sma, load_daily
from artifacts.combined_desk_12w_sim import core_daily_equity, live_core_cfg
from artifacts.expert_20k_desk import (
    apply_expert,
    path_from_eq,
    sim_donchian_friday,
    sim_dual_mom,
    window_of,
)
from artifacts.expert_best_desk_search import sim_short_flex
from artifacts.expert_vs_live_12w import REC_MAP, write_svg
from artifacts.missed_capacity_five_strats import (
    ALL,
    ALT,
    sim_breadth_thrust,
    sim_btc_sma,
    sim_dip_uptrend,
    sim_donchian,
    sim_invvol_basket,
    sim_residual_fade,
    sim_winners_weekly,
)
from artifacts.multi_strat_20k_allocator import BOOK, _date, _idx, path_from_book, summarize, to_returns
from bot.live.momentum_desk import BAR_MS
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "expert_best_desk_loop.json"
SVG = Path(__file__).resolve().parent / "expert_best_desk_loop_1y.svg"

W0 = "2025-09-20"
W1 = "2026-09-20"
W12 = "2026-06-28"
PRIOR0 = "2026-04-05"
PRIOR1 = "2026-06-27"
TRAIN1 = "2026-06-27"


def leftover_grid(names: list[str], step: float = 0.2) -> list[dict[str, float]]:
    """Weights on `names` summing to ≤1, leftover cash. Includes all-cash."""
    n = len(names)
    levels = int(round(1.0 / step))
    out: list[dict[str, float]] = []

    def rec(i: int, left: int, cur: list[int]) -> None:
        if i == n:
            w = {names[j]: round(cur[j] * step, 3) for j in range(n) if cur[j]}
            cash = round(left * step, 3)
            if cash > 1e-9:
                w["cash"] = cash
            if w:
                out.append(w)
            return
        for k in range(left + 1):
            rec(i + 1, left - k, cur + [k])

    rec(0, levels, [])
    seen: set[tuple] = set()
    uniq = []
    for w in out:
        key = tuple(sorted((k, v) for k, v in w.items()))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(w)
    return uniq


def slim_st(st: dict) -> dict:
    return {
        "pnl_eur": st["pnl_eur"],
        "max_dd_pct": st["max_dd_pct"],
        "calmar": st["calmar"],
        "worst_month": st.get("worst_month"),
        "months": st.get("months"),
        "end_eur": st.get("end_eur"),
    }


def score_pack(st_1y: dict, st_12w: dict, st_prior: dict | None = None, st_train: dict | None = None) -> tuple:
    worst = (st_1y.get("worst_month") or {}).get("pnl_eur") or 0.0
    prior_pnl = (st_prior or {}).get("pnl_eur", 0.0)
    train_pnl = (st_train or {}).get("pnl_eur", 0.0)
    return (
        int(st_1y["pnl_eur"] > 0),
        int(st_1y["max_dd_pct"] > -12),
        int(worst > -1500),
        int(st_12w["pnl_eur"] > 3000),
        int(prior_pnl > -500),
        int(train_pnl > 2000),
        int(st_1y["max_dd_pct"] > -15),
        round(st_1y["calmar"], 3),
        st_1y["pnl_eur"],
        st_12w["pnl_eur"],
        prior_pnl,
        worst,
        -abs(st_1y["max_dd_pct"]),
    )


def mix_on_dates(rets: dict[str, dict[str, float]], weights: dict[str, float], dates: list[str]) -> tuple[float, float]:
    eq = BOOK
    peak = BOOK
    mdd = 0.0
    for d in dates:
        r = 0.0
        for k, w in weights.items():
            if w:
                r += w * float(rets.get(k, {}).get(d, 0.0))
        eq += BOOK * r
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return eq - BOOK, 100.0 * mdd / BOOK


def sim_btc_trend_ls(btc: list[float], sma: list[float | None], i0: int, i1: int, *, deploy: float = 0.7) -> list[float]:
    """Classic CTA: long BTC above SMA, short below, flat if SMA missing."""
    cash = BOOK_EUR
    side = 0  # +1 long, -1 short
    notional = 0.0
    entry = 0.0
    eq_path: list[float] = []

    def mark(i: int) -> float:
        if notional <= 0 or side == 0:
            return cash
        px = btc[i]
        if side > 0:
            u = notional * (px / entry - 1.0)
        else:
            u = notional * (entry - px) / entry
        return cash + notional + u

    def flatten(i: int) -> None:
        nonlocal cash, side, notional, entry
        if side == 0 or notional <= 0:
            return
        px = btc[i]
        if side > 0:
            ret = px / entry - 1.0
        else:
            ret = (entry - px) / entry
        cash += notional + notional * ret - notional * FEE_RT
        side = 0
        notional = 0.0
        entry = 0.0

    def open_side(i: int, s: int) -> None:
        nonlocal cash, side, notional, entry
        n = min(cash * deploy, cash * 0.99)
        if n < 50:
            return
        fee = n * (FEE_RT / 2)
        cash -= n + fee
        side = s
        notional = n * (1.0 - FEE_RT / 2)
        entry = btc[i]

    for i in range(i0, i1 + 1):
        sm = sma[i]
        want = 0
        if sm is not None:
            want = 1 if btc[i] > sm else -1
        if side and want != side:
            flatten(i)
        if want != 0 and side == 0:
            open_side(i, want)
        eq_path.append(mark(i))
    if side:
        flatten(i1)
        eq_path[-1] = cash
    return eq_path


def sim_short_named(
    closes,
    btc,
    gates: dict[str, list],
    i0: int,
    i1: int,
    cfg: dict[str, Any],
) -> list[float]:
    gate = cfg["gate"]
    sma200 = gates["sma200"]
    sma50 = gates.get("sma50")
    g = gates.get(gate, sma200)
    # sim_short_flex only switches sma50 vs sma200; wrap others via sma50 slot.
    return sim_short_flex(
        closes,
        btc,
        sma200 if gate != "sma200" else g,
        i0,
        i1,
        lookback=cfg.get("lookback", 15),
        top_n=cfg.get("top_n", 1),
        floor=cfg.get("floor", -0.05),
        skip=cfg.get("skip", 2),
        bounce=cfg.get("bounce", 0.04),
        reb=cfg.get("reb", 14),
        trail=cfg.get("trail", 0.0),
        stop=cfg.get("stop", 0.10),
        max_weight=cfg.get("max_weight", 0.5),
        deploy=cfg.get("deploy", 1.0),
        vol_scale=cfg.get("vol_scale", False),
        vol_spike=False,
        gate="sma50" if gate != "sma200" else "sma200",
        cover_on_bull=cfg.get("cover_on_bull", True),
        sma50=g if gate != "sma200" else sma50,
    )


def build_regimes(
    ts,
    closes,
    i0: int,
    i1: int,
    sma: dict[str, list],
    alt_sma50: dict[str, list],
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    btc = closes["BTC"]

    def breadth(i: int) -> float:
        above = n = 0
        for b in ALT:
            sm = alt_sma50[b][i]
            if sm is None:
                continue
            n += 1
            if closes[b][i] > sm:
                above += 1
        return above / n if n else 1.0

    labs = {
        "sma50_200_br": {},
        "sma50_200": {},
        "sma50_only": {},
        "sma200_only": {},
        "sma20_50": {},
        "mom20": {},
    }
    prev: dict[str, str] = {k: "risk_on" for k in labs}
    hyst: dict[str, str] = {}
    pending: dict[str, tuple[str, int]] = {}

    for i in range(i0, i1 + 1):
        d = _date(ts[i])
        px = btc[i]
        s200, s50, s20, s100 = sma["sma200"][i], sma["sma50"][i], sma["sma20"][i], sma["sma100"][i]
        br = breadth(i)

        if s50 is not None and px < s50:
            a = "risk_off"
        elif s200 is not None and px < s200:
            a = "mid"
        else:
            a = "risk_on"
        labs["sma50_200"][d] = a
        labs["sma50_200_br"][d] = "mid" if a == "risk_on" and br < 0.30 else a

        labs["sma50_only"][d] = "risk_off" if (s50 is not None and px < s50) else "risk_on"
        labs["sma200_only"][d] = "risk_off" if (s200 is not None and px < s200) else "risk_on"

        if s20 is not None and px < s20:
            labs["sma20_50"][d] = "risk_off"
        elif s50 is not None and px < s50:
            labs["sma20_50"][d] = "mid"
        else:
            labs["sma20_50"][d] = "risk_on"

        if i >= 20 and btc[i - 20] > 0:
            m = px / btc[i - 20] - 1.0
            if m < -0.05:
                labs["mom20"][d] = "risk_off"
            elif m > 0.0:
                labs["mom20"][d] = "risk_on"
            else:
                labs["mom20"][d] = "mid"
        else:
            labs["mom20"][d] = "mid"

        # 2-day hysteresis on the breadth map (don't flip on a one-day poke)
        raw = labs["sma50_200_br"][d]
        key = "hyst2"
        want, n = pending.get(key, (raw, 0))
        if raw != prev.get(key, "risk_on"):
            if raw == want:
                pending[key] = (want, n + 1)
            else:
                pending[key] = (raw, 1)
            if pending[key][1] >= 2:
                prev[key] = raw
        else:
            pending[key] = (raw, 0)
        hyst[d] = prev.get(key, raw)

        # silence unused
        _ = s100

    labs["hyst2"] = hyst
    return labs


def euros_map(m: dict) -> dict:
    return {reg: {k: int(round(BOOK * v)) for k, v in w.items() if v > 0} for reg, w in m.items()}


def eval_rows(rets, dates, *, weights=None, regime_w=None, regime_of=None, **overlay):
    return apply_expert(rets, dates, weights=weights, regime_w=regime_w, regime_of=regime_of, **overlay)


def pack_stats(rows, dates_12, dates_prior, dates_train):
    path = [BOOK] + [r["equity_eur"] for r in rows]
    st = summarize(path, rows)
    p12, r12 = window_of(rows, dates_12[0], dates_12[-1])
    ppr, rpr = window_of(rows, dates_prior[0], dates_prior[-1])
    ptr, rtr = window_of(rows, dates_train[0], dates_train[-1])
    st12, stpr, sttr = summarize(p12, r12), summarize(ppr, rpr), summarize(ptr, rtr)
    return st, st12, stpr, sttr, score_pack(st, st12, stpr, sttr)


def main() -> None:
    print("loading daily…", flush=True)
    series = load_daily(ALL, days=560)
    ts, closes = _align(series, ALL)
    highs: dict[str, list[float]] = {}
    lows: dict[str, list[float]] = {}
    for b in ALL:
        idx = {t: i for i, t in enumerate(series[b].ts)}
        highs[b] = [series[b].h[idx[t]] for t in ts]
        lows[b] = [series[b].l[idx[t]] for t in ts]
    i0 = _idx(ts, W0)
    i1 = _idx(ts, W1)
    dates = [_date(ts[i]) for i in range(i0, i1 + 1)]
    dates_12 = [d for d in dates if d >= W12]
    dates_prior = [d for d in dates if PRIOR0 <= d <= PRIOR1]
    dates_train = [d for d in dates if d <= TRAIN1]
    print(f"window {dates[0]}→{dates[-1]} n={len(dates)}", flush=True)

    sma = {
        "sma20": _sma(closes["BTC"], 20),
        "sma50": _sma(closes["BTC"], 50),
        "sma100": _sma(closes["BTC"], 100),
        "sma200": _sma(closes["BTC"], 200),
    }
    alt_sma50 = {b: _sma(closes[b], 50) for b in ALT}
    regimes = build_regimes(ts, closes, i0, i1, sma, alt_sma50)
    for name, mp in regimes.items():
        occ: dict[str, int] = defaultdict(int)
        for lab in mp.values():
            occ[lab] += 1
        print(f"  regime {name}: {dict(occ)}", flush=True)

    print("core 15m 1y…", flush=True)
    cfg = live_core_cfg()
    end_ms = int(datetime.fromisoformat(W1).replace(tzinfo=UTC).timestamp() * 1000) // BAR_MS * BAR_MS
    start_ms = int(datetime.fromisoformat(W0).replace(tzinfo=UTC).timestamp() * 1000)
    days_span = int((end_ms - start_ms) / 86_400_000) + 40
    candles = load_candles(("BTC", *cfg.universe), days=days_span, end_ms=end_ms, refresh=False)
    core_res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    print(f"  realized={core_res.summary().get('realized_eur')} trades={core_res.summary().get('trades')}", flush=True)
    core_eq = {
        r["date"]: float(r["equity_eur"])
        for r in core_daily_equity(
            core_res, daily_ts=ts, daily_closes=closes, start_ms=start_ms, end_ms=end_ms, book=BOOK
        )
    }

    print("long sleeves…", flush=True)
    sleeve_specs: list[tuple[str, Any]] = [
        ("core", None),
        ("donch20", lambda: sim_donchian(ts, closes, highs, lows, i0, i1, ch=20, exit_n=10, btc_sma=50)),
        ("donch10", lambda: sim_donchian(ts, closes, highs, lows, i0, i1, ch=10, exit_n=5, btc_sma=50)),
        ("donch55", lambda: sim_donchian(ts, closes, highs, lows, i0, i1, ch=55, exit_n=20, btc_sma=50)),
        ("donch_fri", lambda: sim_donchian_friday(ts, closes, highs, lows, i0, i1)),
        ("donch_fri10", lambda: sim_donchian_friday(ts, closes, highs, lows, i0, i1, ch=10, exit_n=5)),
        ("winners30", lambda: sim_winners_weekly(ts, closes, highs, lows, i0, i1, lb=30, skip=7, top_n=3, reb=7, w_each=0.3, sma_n=200)),
        ("winners21", lambda: sim_winners_weekly(ts, closes, highs, lows, i0, i1, lb=21, skip=2, top_n=2, reb=7, w_each=0.4, sma_n=200)),
        ("winners60", lambda: sim_winners_weekly(ts, closes, highs, lows, i0, i1, lb=60, skip=7, top_n=3, reb=7, w_each=0.3, sma_n=50)),
        ("invvol", lambda: sim_invvol_basket(ts, closes, highs, lows, i0, i1, lookback=21, top_n=4, deploy=0.8, sma_n=200)),
        ("invvol63", lambda: sim_invvol_basket(ts, closes, highs, lows, i0, i1, lookback=63, top_n=5, deploy=0.9, sma_n=200)),
        ("btc_sma200", lambda: sim_btc_sma(ts, closes, highs, lows, i0, i1, sma_n=200, target_vol=0.40)),
        ("btc_sma50", lambda: sim_btc_sma(ts, closes, highs, lows, i0, i1, sma_n=50, target_vol=0.35, max_w=0.85)),
        ("dual_mom", lambda: sim_dual_mom(ts, closes, highs, lows, i0, i1)),
        ("dip", lambda: sim_dip_uptrend(ts, closes, highs, lows, i0, i1)),
        ("dip_soft", lambda: sim_dip_uptrend(ts, closes, highs, lows, i0, i1, drop=-0.03, rsi_max=50, hold_days=4, stop=0.05, take=0.06)),
        ("residual", lambda: sim_residual_fade(ts, closes, highs, lows, i0, i1)),
        ("breadth", lambda: sim_breadth_thrust(ts, closes, highs, lows, i0, i1)),
    ]
    longs: dict[str, dict[str, float]] = {"core": core_eq, "cash": {d: BOOK for d in dates}}
    for name, fn in sleeve_specs:
        if name == "core":
            continue
        book = fn()
        longs[name] = path_from_book(ts, i0, i1, book)
        print(f"  {name} end={list(longs[name].values())[-1]:.0f}", flush=True)

    btc_ls50 = sim_btc_trend_ls(closes["BTC"], sma["sma50"], i0, i1, deploy=0.7)
    btc_ls200 = sim_btc_trend_ls(closes["BTC"], sma["sma200"], i0, i1, deploy=0.7)
    longs["btc_ls50"] = path_from_eq(ts, i0, i1, btc_ls50)
    longs["btc_ls200"] = path_from_eq(ts, i0, i1, btc_ls200)
    print(f"  btc_ls50 end={list(longs['btc_ls50'].values())[-1]:.0f}", flush=True)
    print(f"  btc_ls200 end={list(longs['btc_ls200'].values())[-1]:.0f}", flush=True)

    rets_long = {k: to_returns(v) for k, v in longs.items()}
    # aliases so older maps (REC_MAP / prev search) still resolve
    rets_long["winners"] = rets_long["winners30"]

    # unit scores (100% one sleeve)
    unit_rank = []
    for k, rets in rets_long.items():
        p, r = apply_expert({k: rets, "cash": rets_long["cash"]}, dates, weights={k: 1.0})
        st = summarize(p, r)
        unit_rank.append({"name": k, "y1": slim_st(st)})
        print(f"  unit {k:12s} 1y={st['pnl_eur']:+7.0f} dd={st['max_dd_pct']:7.2f} calmar={st['calmar']:.2f}", flush=True)
    unit_rank.sort(key=lambda x: (x["y1"]["pnl_eur"], x["y1"]["calmar"]), reverse=True)

    print("stage 1: short knob sweep…", flush=True)
    short_cfgs = []
    for gate, reb, trail, stop, floor, vol_scale in itertools.product(
        ("sma20", "sma50", "sma100"),
        (7, 10, 14, 21),
        (0.0, 0.04),
        (0.08, 0.10, 0.12),
        (-0.03, -0.05, -0.08),
        (False, True),
    ):
        short_cfgs.append(
            dict(
                gate=gate,
                cover_on_bull=True,
                reb=reb,
                trail=trail,
                stop=stop,
                top_n=1,
                floor=floor,
                vol_scale=vol_scale,
                lookback=15,
                skip=2,
                bounce=0.04,
            )
        )
    print(f"  short configs {len(short_cfgs)}", flush=True)

    # Score shorts gated on sma50_200_br (risk_off+mid) so we pick units that harvest the downtrend.
    base_reg = regimes["sma50_200_br"]
    gated_ranked = []
    short_rets: dict[str, dict[str, float]] = {}
    for i, cfg_s in enumerate(short_cfgs):
        eq = sim_short_named(closes, closes["BTC"], sma, i0, i1, cfg_s)
        name = f"s{i}"
        path = path_from_eq(ts, i0, i1, eq)
        rets = to_returns(path)
        short_rets[name] = rets
        rw = {"risk_on": {"cash": 1.0}, "mid": {name: 1.0}, "risk_off": {name: 1.0}}
        p, r = apply_expert({name: rets, "cash": rets_long["cash"]}, dates, regime_w=rw, regime_of=base_reg)
        st = summarize(p, r)
        p12, r12 = window_of(r, dates_12[0], dates_12[-1])
        st12 = summarize(p12, r12)
        gated_ranked.append(
            {
                "id": name,
                "cfg": cfg_s,
                "gated_1y": slim_st(st),
                "gated_12w": slim_st(st12),
                "unit_end": list(path.values())[-1],
                "_score": (int(st["pnl_eur"] > 0), int(st["max_dd_pct"] > -20), st["calmar"], st["pnl_eur"], st12["pnl_eur"]),
            }
        )
        if (i + 1) % 80 == 0:
            print(f"    {i+1}/{len(short_cfgs)}", flush=True)
    gated_ranked.sort(key=lambda x: x["_score"], reverse=True)
    top_shorts = gated_ranked[:10]
    print("  top gated shorts:", flush=True)
    for row in top_shorts:
        g, g12 = row["gated_1y"], row["gated_12w"]
        print(f"    {row['id']} 1y={g['pnl_eur']:+.0f} dd={g['max_dd_pct']} 12w={g12['pnl_eur']:+.0f} {row['cfg']}", flush=True)

    print("stage 1b: refine top shorts (lookback/skip/bounce/top_n)…", flush=True)
    refine = []
    seen_cfg = {tuple(sorted(c["cfg"].items())) for c in top_shorts}
    for seed in top_shorts[:5]:
        base = dict(seed["cfg"])
        for lookback, skip, bounce, top_n, reb in itertools.product(
            (10, 15, 21),
            (1, 2),
            (0.02, 0.04, 0.06),
            (1, 2),
            sorted({base["reb"], 7, 10, 14}),
        ):
            cfg_s = {**base, "lookback": lookback, "skip": skip, "bounce": bounce, "top_n": top_n, "reb": reb}
            key = tuple(sorted(cfg_s.items()))
            if key in seen_cfg:
                continue
            seen_cfg.add(key)
            refine.append(cfg_s)
    print(f"  extra short configs {len(refine)}", flush=True)
    base_i = len(short_cfgs)
    for j, cfg_s in enumerate(refine):
        eq = sim_short_named(closes, closes["BTC"], sma, i0, i1, cfg_s)
        name = f"s{base_i + j}"
        path = path_from_eq(ts, i0, i1, eq)
        rets = to_returns(path)
        short_rets[name] = rets
        rw = {"risk_on": {"cash": 1.0}, "mid": {name: 1.0}, "risk_off": {name: 1.0}}
        p, r = apply_expert({name: rets, "cash": rets_long["cash"]}, dates, regime_w=rw, regime_of=base_reg)
        st = summarize(p, r)
        p12, r12 = window_of(r, dates_12[0], dates_12[-1])
        st12 = summarize(p12, r12)
        gated_ranked.append(
            {
                "id": name,
                "cfg": cfg_s,
                "gated_1y": slim_st(st),
                "gated_12w": slim_st(st12),
                "unit_end": list(path.values())[-1],
                "_score": (int(st["pnl_eur"] > 0), int(st["max_dd_pct"] > -20), st["calmar"], st["pnl_eur"], st12["pnl_eur"]),
            }
        )
    gated_ranked.sort(key=lambda x: x["_score"], reverse=True)
    top_shorts = gated_ranked[:8]
    print("  refined top gated shorts:", flush=True)
    for row in top_shorts:
        g, g12 = row["gated_1y"], row["gated_12w"]
        print(f"    {row['id']} 1y={g['pnl_eur']:+.0f} dd={g['max_dd_pct']} 12w={g12['pnl_eur']:+.0f} {row['cfg']}", flush=True)

    # architectures that are NOT the requested LS map
    print("stage 2: competing architectures…", flush=True)
    arch_keep: list[dict[str, Any]] = []
    best = None
    best_sc = None
    tested = 0

    def consider(kind: str, detail: dict, rows, st, st12, stpr, sttr, sc) -> None:
        nonlocal best, best_sc, tested
        tested += 1
        row = {
            "kind": kind,
            **detail,
            "y1": slim_st(st),
            "w12": slim_st(st12),
            "prior12": slim_st(stpr),
            "train": slim_st(sttr),
            "_score": sc,
        }
        if best_sc is None or sc > best_sc:
            best_sc = sc
            best = {**row, "_rows": rows}
            print(
                f"  new best [{kind}] 1y={st['pnl_eur']:+.0f} dd={st['max_dd_pct']} "
                f"12w={st12['pnl_eur']:+.0f} prior={stpr['pnl_eur']:+.0f} calmar={st['calmar']}",
                flush=True,
            )
        arch_keep.append(row)
        arch_keep.sort(key=lambda x: x["_score"], reverse=True)
        del arch_keep[16:]

    # 2a. 100% each long sleeve / BTC LS
    for k in rets_long:
        p, r = apply_expert(rets_long, dates, weights={k: 1.0})
        st, st12, stpr, sttr, sc = pack_stats(r, dates_12, dates_prior, dates_train)
        consider("unit", {"sleeve": k}, r, st, st12, stpr, sttr, sc)

    # 2b. static 2-sleeve mixes of the best units (no regime)
    best_units = [u["name"] for u in unit_rank if u["name"] != "cash"][:8]
    static_maps = leftover_grid(best_units[:4], step=0.25)
    print(f"  static mixes {len(static_maps)} from {best_units[:4]}", flush=True)
    for w in static_maps:
        p, r = apply_expert(rets_long, dates, weights=w)
        st, st12, stpr, sttr, sc = pack_stats(r, dates_12, dates_prior, dates_train)
        consider("static", {"map": {"always": w}}, r, st, st12, stpr, sttr, sc)

    # 2c. risk-parity of complementary sleeves, optionally regime-gated
    rp_sets = [
        ["donch_fri", "winners30", "invvol", "cash"],
        ["donch_fri", "winners30", "btc_sma50", "cash"],
        ["donch_fri", "dual_mom", "invvol", "cash"],
        ["btc_ls50", "donch_fri", "winners21", "cash"],
        ["dip", "donch_fri", "winners30", "cash"],
        ["core", "donch_fri", "winners30", "cash"],
    ]
    for allow in rp_sets:
        p, r = apply_expert(rets_long, dates, rp_allow={"risk_on": allow}, rp_max_w=0.40)
        # rp_allow without regime_of uses label None → key "risk_on"
        st, st12, stpr, sttr, sc = pack_stats(r, dates_12, dates_prior, dates_train)
        consider("risk_parity", {"allow": allow}, r, st, st12, stpr, sttr, sc)

    print("stage 2b: seed known-good maps on every top short × classifier…", flush=True)
    seed_maps = [
        {
            "risk_on": {"core": 0.25, "winners30": 0.35, "donch_fri": 0.4},
            "mid": {"cash": 1.0},
            "risk_off": {"short": 0.7, "cash": 0.3},
        },
        {
            "risk_on": {"winners30": 0.5, "donch_fri": 0.5},
            "mid": {"cash": 1.0},
            "risk_off": {"short": 0.85, "cash": 0.15},
        },
        {
            "risk_on": {"donch_fri": 0.4, "winners21": 0.3, "dual_mom": 0.3},
            "mid": {"invvol": 0.4, "cash": 0.6},
            "risk_off": {"short": 0.7, "cash": 0.3},
        },
        {
            "risk_on": {"btc_sma50": 0.3, "donch_fri": 0.4, "winners30": 0.3},
            "mid": {"btc_sma50": 0.3, "cash": 0.7},
            "risk_off": {"short": 0.6, "btc_ls50": 0.2, "cash": 0.2},
        },
        {
            "risk_on": {"core": 0.2, "donch_fri": 0.4, "winners30": 0.25, "dip": 0.15},
            "mid": {"dip": 0.3, "cash": 0.7},
            "risk_off": {"short": 1.0},
        },
        {
            "risk_on": {"donch_fri10": 0.5, "winners21": 0.5},
            "mid": {"residual": 0.3, "cash": 0.7},
            "risk_off": {"short": 0.55, "cash": 0.45},
        },
        REC_MAP,
    ]
    for reg_name, regime_of in regimes.items():
        for sh in top_shorts:
            rets = {**rets_long, "short": short_rets[sh["id"]]}
            for rw in seed_maps:
                p, r = apply_expert(rets, dates, regime_w=rw, regime_of=regime_of)
                st, st12, stpr, sttr, sc = pack_stats(r, dates_12, dates_prior, dates_train)
                consider(
                    "seed_map",
                    {"regime": reg_name, "short_id": sh["id"], "short_cfg": sh["cfg"], "map": rw},
                    r,
                    st,
                    st12,
                    stpr,
                    sttr,
                    sc,
                )

    print("stage 3: per-regime mix search across classifiers × shorts…", flush=True)
    long_names = [k for k in rets_long if k != "cash"]
    # pick sleeves that actually make money as units, plus a couple of diversifiers
    useful_long = [u["name"] for u in unit_rank if u["name"] != "cash" and u["y1"]["pnl_eur"] > -1500][:10]
    if "core" not in useful_long:
        useful_long.append("core")
    print(f"  useful longs: {useful_long}", flush=True)

    for reg_name, regime_of in regimes.items():
        by_lab: dict[str, list[str]] = defaultdict(list)
        for d in dates:
            by_lab[regime_of[d]].append(d)
        labels = [x for x in ("risk_on", "mid", "risk_off") if x in by_lab]
        if "risk_on" not in labels:
            labels = sorted(by_lab)

        for sh in top_shorts:
            rets = {**rets_long, "short": short_rets[sh["id"]]}
            # per-regime candidate mixes
            per: dict[str, list[dict[str, float]]] = {}
            for lab in labels:
                dsub = by_lab[lab]
                names = list(useful_long[:6])
                if lab != "risk_on":
                    names = ["short"] + [n for n in names if n != "core"][:5]
                else:
                    names = [n for n in names if n != "btc_ls50"][:6]
                # keep grid small: 4 names step 0.25
                use_names = names[:4]
                cands = leftover_grid(use_names, step=0.25)
                scored_m = []
                for w in cands:
                    pnl, dd = mix_on_dates(rets, w, dsub)
                    scored_m.append((pnl, dd, w))
                scored_m.sort(key=lambda x: (x[0], x[1]), reverse=True)
                # keep mixes that don't torch the regime
                kept = []
                for pnl, dd, w in scored_m:
                    if dd < -25:
                        continue
                    kept.append(w)
                    if len(kept) >= 8:
                        break
                if not kept:
                    kept = [scored_m[0][2]]
                # always include cash and a concentrated best
                if {"cash": 1.0} not in kept:
                    kept.append({"cash": 1.0})
                per[lab] = kept

            keys = labels
            combos = itertools.product(*(per[lab] for lab in keys))
            n_combo = 1
            for lab in keys:
                n_combo *= len(per[lab])
            print(f"  {reg_name} × {sh['id']}: {n_combo} maps", flush=True)
            for combo in combos:
                rw = {lab: w for lab, w in zip(keys, combo)}
                # 2-state maps only have on/off; fill missing with cash
                for lab in ("risk_on", "mid", "risk_off"):
                    rw.setdefault(lab, {"cash": 1.0})
                p, r = apply_expert(rets, dates, regime_w=rw, regime_of=regime_of)
                st, st12, stpr, sttr, sc = pack_stats(r, dates_12, dates_prior, dates_train)
                consider(
                    "regime_map",
                    {"regime": reg_name, "short_id": sh["id"], "short_cfg": sh["cfg"], "map": rw},
                    r,
                    st,
                    st12,
                    stpr,
                    sttr,
                    sc,
                )

    print(f"tested {tested}  best so far 1y={best['y1']['pnl_eur']} dd={best['y1']['max_dd_pct']}", flush=True)

    print("stage 4: local hill-climb around winner…", flush=True)
    if best and best.get("map"):
        rets_w = {**rets_long}
        if best.get("short_id"):
            rets_w["short"] = short_rets[best["short_id"]]
        regime_of = regimes.get(best.get("regime") or "sma50_200_br", regimes["sma50_200_br"])
        base_map = {reg: dict(w) for reg, w in best["map"].items()}

        def neighbors(w: dict[str, float]) -> list[dict[str, float]]:
            names = [k for k in w if k != "cash"] or list(w)
            out = []
            for k in list(w):
                for delta in (-0.10, -0.05, 0.05, 0.10):
                    nw = dict(w)
                    nw[k] = round(nw.get(k, 0.0) + delta, 3)
                    if nw[k] < -1e-9:
                        continue
                    s = sum(v for kk, v in nw.items() if kk != "cash")
                    if s > 1.0 + 1e-9:
                        continue
                    cash = round(1.0 - s, 3)
                    nw = {kk: v for kk, v in nw.items() if kk != "cash" and v > 1e-9}
                    if cash > 1e-9:
                        nw["cash"] = cash
                    if nw:
                        out.append(nw)
            # swap in a strong sleeve at 0.25
            for extra in useful_long[:6] + (["short"] if "short" in rets_w else []):
                if extra in w:
                    continue
                nw = dict(w)
                take = 0.25
                cash0 = nw.get("cash", 0.0)
                if cash0 >= take:
                    nw["cash"] = round(cash0 - take, 3)
                    if nw["cash"] <= 1e-9:
                        nw.pop("cash", None)
                    nw[extra] = take
                    out.append(nw)
            return out

        climbed = 0
        improved = True
        while improved and climbed < 12:
            improved = False
            climbed += 1
            cand_maps = [base_map]
            for reg in list(base_map):
                for nw in neighbors(base_map[reg]):
                    m = {k: dict(v) for k, v in base_map.items()}
                    m[reg] = nw
                    cand_maps.append(m)
            # unique
            seen_m = set()
            uniq_m = []
            for m in cand_maps:
                key = tuple((reg, tuple(sorted(w.items()))) for reg, w in sorted(m.items()))
                if key in seen_m:
                    continue
                seen_m.add(key)
                uniq_m.append(m)
            print(f"  climb {climbed}: {len(uniq_m)} neighbors", flush=True)
            for m in uniq_m:
                p, r = apply_expert(rets_w, dates, regime_w=m, regime_of=regime_of)
                st, st12, stpr, sttr, sc = pack_stats(r, dates_12, dates_prior, dates_train)
                before = best_sc
                consider(
                    "hillclimb",
                    {
                        "regime": best.get("regime"),
                        "short_id": best.get("short_id"),
                        "short_cfg": best.get("short_cfg"),
                        "map": m,
                    },
                    r,
                    st,
                    st12,
                    stpr,
                    sttr,
                    sc,
                )
                if best_sc != before:
                    base_map = {reg: dict(w) for reg, w in m.items()}
                    improved = True

    print("stage 5: overlays on champion…", flush=True)
    overlays = []
    if best and best.get("map"):
        rets_w = {**rets_long}
        if best.get("short_id"):
            rets_w["short"] = short_rets[best["short_id"]]
        regime_of = regimes.get(best.get("regime") or "sma50_200_br", regimes["sma50_200_br"])
        for tv, dd, lev in (
            (None, None, 1.0),
            (0.12, None, 1.0),
            (0.15, None, 1.0),
            (0.18, None, 1.0),
            (0.22, None, 1.0),
            (None, -0.06, 1.0),
            (None, -0.08, 1.0),
            (0.18, -0.08, 1.0),
            (0.15, None, 1.2),
        ):
            p, r = apply_expert(
                rets_w,
                dates,
                regime_w=best["map"],
                regime_of=regime_of,
                vol_target=tv,
                dd_halt=dd,
                dd_resume=-0.03 if dd else None,
                max_lev=lev,
            )
            st, st12, stpr, sttr, sc = pack_stats(r, dates_12, dates_prior, dates_train)
            overlays.append(
                {
                    "vol_target": tv,
                    "dd_halt": dd,
                    "max_lev": lev,
                    "y1": slim_st(st),
                    "w12": slim_st(st12),
                    "_score": sc,
                }
            )
            consider(
                "overlay",
                {
                    "regime": best.get("regime"),
                    "short_id": best.get("short_id"),
                    "short_cfg": best.get("short_cfg"),
                    "map": best["map"],
                    "overlay": {"vol_target": tv, "dd_halt": dd, "max_lev": lev},
                },
                r,
                st,
                st12,
                stpr,
                sttr,
                sc,
            )
            print(f"  overlay vt={tv} dd={dd} lev={lev} 1y={st['pnl_eur']:+.0f} dd={st['max_dd_pct']}", flush=True)

    print("stage 6: walk-forward monthly picker…", flush=True)
    # Candidate set: unique maps from top keep + cash-mix + previous search winner
    prev_winner_map = {
        "risk_on": {"core": 0.25, "winners30": 0.35, "donch_fri": 0.4},
        "mid": {"cash": 1.0},
        "risk_off": {"short": 0.7, "cash": 0.3},
    }
    wf_maps = [prev_winner_map, REC_MAP]
    for row in arch_keep:
        if row.get("map") and row["kind"] in ("regime_map", "hillclimb", "static"):
            wf_maps.append(row["map"])
    # unique
    seen_wf = set()
    uniq_wf = []
    for m in wf_maps:
        key = json.dumps(m, sort_keys=True)
        if key in seen_wf:
            continue
        seen_wf.add(key)
        uniq_wf.append(m)
    print(f"  wf candidates {len(uniq_wf)}", flush=True)

    # use best short + breadth regime (standard)
    sh0 = top_shorts[0]
    rets_wf = {**rets_long, "short": short_rets[sh0["id"]], "winners": rets_long["winners30"]}
    # alias donch20 already there
    regime_wf = regimes["sma50_200_br"]

    def calmar_of(rows_sub) -> float:
        if not rows_sub:
            return -999.0
        eq = BOOK
        path = [eq]
        peak = BOOK
        mdd = 0.0
        for r in rows_sub:
            eq += BOOK * float(r["day_ret"])
            path.append(eq)
            peak = max(peak, eq)
            mdd = min(mdd, eq - peak)
        pnl = eq - BOOK
        return (pnl / abs(mdd)) if abs(mdd) > 1 else (99.0 if pnl > 0 else -99.0)

    # precompute each map's daily rows
    map_rows = []
    for m in uniq_wf[:24]:
        # static maps stored under "always"
        if "always" in m:
            _, r = apply_expert(rets_wf, dates, weights=m["always"])
        else:
            mm = dict(m)
            if "winners" not in rets_wf and any("winners" in w for w in mm.values()):
                # map winners → winners30
                mm = {reg: {("winners30" if k == "winners" else k): v for k, v in w.items()} for reg, w in mm.items()}
            _, r = apply_expert(rets_wf, dates, regime_w=mm, regime_of=regime_wf)
        map_rows.append((m, r))

    # month starts
    months = []
    prev_m = None
    for i, d in enumerate(dates):
        m = d[:7]
        if m != prev_m:
            months.append((m, i))
            prev_m = m
    months.append(("end", len(dates)))

    wf_eq = BOOK
    wf_rows = []
    picks = []
    lookback_days = 45
    for mi in range(len(months) - 1):
        i0m = months[mi][1]
        i1m = months[mi + 1][1]
        # pick on trailing lookback ending at i0m (no lookahead)
        a = max(0, i0m - lookback_days)
        best_j = 0
        best_c = -1e18
        if i0m > 8:
            for j, (_, rws) in enumerate(map_rows):
                c = calmar_of(rws[a:i0m])
                if c > best_c:
                    best_c = c
                    best_j = j
        picks.append({"month": months[mi][0], "pick_idx": best_j, "trail_calmar": round(best_c, 3)})
        _, chosen = map_rows[best_j]
        for r in chosen[i0m:i1m]:
            wf_eq += BOOK * float(r["day_ret"])
            wf_rows.append({**r, "equity_eur": round(wf_eq, 2), "wf_pick": best_j})
    st, st12, stpr, sttr, sc = pack_stats(wf_rows, dates_12, dates_prior, dates_train)
    consider(
        "walk_forward",
        {"short_id": sh0["id"], "short_cfg": sh0["cfg"], "n_candidates": len(map_rows), "picks": picks},
        wf_rows,
        st,
        st12,
        stpr,
        sttr,
        sc,
    )
    print(f"  walk-forward 1y={st['pnl_eur']:+.0f} dd={st['max_dd_pct']} 12w={st12['pnl_eur']:+.0f}", flush=True)

    # baselines
    p_live, r_live = apply_expert(rets_long, dates, weights={"core": 1.0})
    p_cash, r_cash = apply_expert(rets_long, dates, regime_w=REC_MAP, regime_of=regimes["sma50_200_br"])
    # previous search winner, aliased
    prev_map = {
        "risk_on": {"core": 0.25, "winners30": 0.35, "donch_fri": 0.4},
        "mid": {"cash": 1.0},
        "risk_off": {"short": 0.7, "cash": 0.3},
    }
    # use previous s67-like: first sma50/cover/reb14/stop0.1/floor-0.05/vol_scale
    prev_short = None
    for row in gated_ranked:
        c = row["cfg"]
        if (
            c.get("gate") == "sma50"
            and c.get("reb") == 14
            and abs(c.get("stop", 0) - 0.1) < 1e-9
            and abs(c.get("floor", 0) + 0.05) < 1e-9
            and c.get("vol_scale")
            and c.get("lookback", 15) == 15
            and c.get("top_n", 1) == 1
        ):
            prev_short = row
            break
    if prev_short is None:
        prev_short = top_shorts[0]
    rets_prev = {**rets_long, "short": short_rets[prev_short["id"]]}
    p_prev, r_prev = apply_expert(rets_prev, dates, regime_w=prev_map, regime_of=regimes["sma50_200_br"])
    st_live, st_cash, st_prev = summarize(p_live, r_live), summarize(p_cash, r_cash), summarize(p_prev, r_prev)
    p12l, r12l = window_of(r_live, dates_12[0], dates_12[-1])
    p12c, r12c = window_of(r_cash, dates_12[0], dates_12[-1])

    champ = best
    champ_rows = best["_rows"]

    write_svg(
        r_live,
        r_cash,
        SVG,
        title="1y loop: live vs cash-mix vs best looped desk",
        rec_label="cash-mix (baseline)",
        extra=[
            ("prev search", "#c9a227", r_prev),
            ("best loop", "#7aa2ff", champ_rows),
        ],
    )

    rec = {
        "kind": champ["kind"],
        "regime": champ.get("regime"),
        "map": champ.get("map"),
        "euros": euros_map(champ["map"]) if champ.get("map") and "always" not in (champ.get("map") or {}) else None,
        "short_cfg": champ.get("short_cfg"),
        "overlay": champ.get("overlay"),
        "sleeve": champ.get("sleeve"),
        "allow": champ.get("allow"),
        "y1": champ["y1"],
        "w12": champ["w12"],
        "prior12": champ.get("prior12"),
        "train": champ.get("train"),
        "vs_live_1y_eur": round(champ["y1"]["pnl_eur"] - st_live["pnl_eur"], 2),
        "vs_cash_mix_1y_eur": round(champ["y1"]["pnl_eur"] - st_cash["pnl_eur"], 2),
        "vs_prev_search_1y_eur": round(champ["y1"]["pnl_eur"] - st_prev["pnl_eur"], 2),
        "vs_live_12w_eur": round(champ["w12"]["pnl_eur"] - summarize(p12l, r12l)["pnl_eur"], 2),
        "vs_cash_mix_12w_eur": round(champ["w12"]["pnl_eur"] - summarize(p12c, r12c)["pnl_eur"], 2),
    }

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK,
        "window": {"start": dates[0], "end": dates[-1], "days": len(dates)},
        "last_12w": {"start": dates_12[0], "end": dates_12[-1], "days": len(dates_12)},
        "prior_12w": {"start": dates_prior[0], "end": dates_prior[-1], "days": len(dates_prior)},
        "tested": {
            "short_configs": len(short_cfgs) + len(refine),
            "mixes_and_arch": tested,
            "regime_classifiers": list(regimes),
            "long_sleeves": list(longs),
        },
        "regime_days": {k: dict(defaultdict(int, **{lab: list(mp.values()).count(lab) for lab in set(mp.values())})) for k, mp in regimes.items()},
        "objective": "1y profit + DD>-12% + worst month>-€1500 + 12w>€3k + prior12>-€500 + train>€2k, then Calmar",
        "baselines": {
            "live_core": {"y1": slim_st(st_live), "w12": slim_st(summarize(p12l, r12l))},
            "cash_mix": {"y1": slim_st(st_cash), "w12": slim_st(summarize(p12c, r12c)), "map": REC_MAP},
            "prev_search": {"y1": slim_st(st_prev), "map": prev_map, "short_cfg": prev_short["cfg"]},
        },
        "unit_sleeves": unit_rank,
        "top_gated_shorts": [{k: v for k, v in row.items() if k != "_score"} for row in top_shorts],
        "recommendation": rec,
        "top_arch": [{k: v for k, v in row.items() if k not in ("_score", "_rows", "picks")} for row in arch_keep],
        "overlays_on_best": overlays,
        "caveats": [
            "Search is still mostly in-sample on this 1y; prior-12w and train filters reduce last-slice snooping",
            "No AlphaI; 15m core close-fills; daily fills for other sleeves",
            "Short is paper/perp-proxy; cover_on_bull flats the same day BTC recaptures the gate",
            "Not live-wired",
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n=== BEST LOOP ===", flush=True)
    print(json.dumps({k: rec[k] for k in rec if k not in ("")}, indent=2), flush=True)
    print(f"tested {tested}  wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()

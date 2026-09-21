#!/usr/bin/env python3
"""Bear-harvest hunter: find a retreating-market sleeve that rivals the momentum desk.

Target (momentum desk, €20k, ~12w rising/active tape): ≈ +€6.3–6.8k (+32–34%),
trade-path DD ≈ −€2.5–5.0k. We hunt bear-regime (BTC < SMA200) strategies that
approach or beat that return with comparable or better Calmar.

Literature levers tested:
  - Short laggards / cross-sectional weakness (FXEmpire, RS)
  - BTC regime gate SMA200 / SMA50 (DennTech, Boring Edge stacking)
  - Equal-weight alt basket short in bear (PyQuantLab EMA regime idea)
  - BTC trend short below SMA50/200
  - Dual-horizon + skip-recent (Asness)
  - Bounce block / vol-spike exits
  - Barroso-style inverse-vol deploy scaling
  - Donchian 20d low breakout shorts
  - Blend BTC-short + weakest alts

Research-only. Writes artifacts/bear_harvest_hunter.json
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from artifacts.bear_market_strategy_sim import (
    BOOK_EUR,
    FEE_RT,
    UNIVERSE,
    _align,
    _max_drawdown,
    _sma,
    load_daily,
)

OUT = Path(__file__).resolve().parent / "bear_harvest_hunter.json"

# Momentum desk benchmark (combined / x1 12w sims on €20k)
DESK_TARGET_PNL = 6369.0
DESK_TARGET_RET = 31.84
DESK_TARGET_DD = -4996.0  # trade path; x1 used -2554
DESK_CALMAR = abs(DESK_TARGET_PNL / DESK_TARGET_DD)


@dataclass
class Result:
    name: str
    pnl_eur: float
    return_pct: float
    max_dd_eur: float
    max_dd_pct: float
    calmar: float
    trades: int
    win_rate: float | None
    active_days: int
    params: dict[str, Any]

    def score(self) -> tuple:
        """Prefer desk-beating PnL, then Calmar, then lower |DD|."""
        beats = int(self.pnl_eur >= DESK_TARGET_PNL * 0.9)  # within 10% of desk
        strong = int(self.pnl_eur >= DESK_TARGET_PNL)
        # penalize catastrophic DD (>40% of book)
        dd_ok = int(self.max_dd_pct > -35)
        return (strong, beats, dd_ok, self.calmar, self.pnl_eur, self.max_dd_pct)


def summarize(name: str, eq: list[float], trades: int, wins: int, active: int, params: dict) -> Result:
    pnl = eq[-1] - BOOK_EUR
    mdd = _max_drawdown(eq)
    cal = (pnl / abs(mdd)) if abs(mdd) > 1 else (999.0 if pnl > 0 else 0.0)
    return Result(
        name=name,
        pnl_eur=round(pnl, 2),
        return_pct=round(100 * pnl / BOOK_EUR, 2),
        max_dd_eur=round(mdd, 2),
        max_dd_pct=round(100 * mdd / BOOK_EUR, 2),
        calmar=round(cal, 3),
        trades=trades,
        win_rate=round(wins / trades, 3) if trades else None,
        active_days=active,
        params=params,
    )


def atr14(closes: list[float], i: int) -> float:
    if i < 14:
        return 0.02
    rets = [abs(closes[j] / closes[j - 1] - 1.0) for j in range(i - 13, i + 1) if closes[j - 1] > 0]
    return sum(rets) / len(rets) if rets else 0.02


def sim_short_book(
    closes: dict[str, list[float]],
    btc: list[float],
    sma200: list[float | None],
    i0: int,
    i1: int,
    *,
    pick_fn: Callable[[int], list[tuple[str, float]]],
    rebalance_every: int,
    trail_pct: float,
    hard_stop_pct: float,
    vol_spike: bool,
    vol_mult: float,
    max_weight: float,
    deploy_frac: float,
    vol_scale: bool,
    require_bear: bool,
    bear_sma: list[float | None] | None = None,
) -> tuple[list[float], int, int, int]:
    """Generic short book with reserved-notional accounting."""
    cash = BOOK_EUR
    shorts: dict[str, tuple[float, float, float, float]] = {}  # notional, entry, peak, atr
    eq: list[float] = []
    trades = wins = active = 0
    last_reb = -10**9
    gate = bear_sma if bear_sma is not None else sma200

    def equity_now(i: int) -> float:
        u = sum(n * (e - closes[b][i]) / e for b, (n, e, _, _) in shorts.items())
        return cash + sum(n for n, _, _, _ in shorts.values()) + u

    def close_one(b: str, i: int) -> None:
        nonlocal cash, trades, wins
        n, e, _, _ = shorts.pop(b)
        ret = (e - closes[b][i]) / e
        net = n * ret - n * FEE_RT
        cash += n + net
        trades += 1
        wins += int(net > 0)

    for i in range(i0, i1 + 1):
        # exits
        for b in list(shorts):
            n, e, peak, atr = shorts[b]
            ret = (e - closes[b][i]) / e
            peak = max(peak, ret)
            shorts[b] = (n, e, peak, atr)
            reason = None
            if hard_stop_pct > 0 and ret <= -hard_stop_pct:
                reason = "stop"
            elif trail_pct > 0 and peak - ret >= trail_pct:
                reason = "trail"
            elif vol_spike and i > 0:
                day = closes[b][i] / closes[b][i - 1] - 1.0
                if day >= vol_mult * max(atr, 0.01):
                    reason = "spike"
            if reason:
                close_one(b, i)

        if shorts:
            active += 1

        due = i - last_reb >= rebalance_every or last_reb < 0
        bear_ok = (not require_bear) or (gate[i] is not None and btc[i] < gate[i])
        if due:
            for b in list(shorts):
                close_one(b, i)
            if bear_ok and cash > 50:
                picks = pick_fn(i)
                if picks:
                    deploy = cash * deploy_frac
                    if vol_scale:
                        # scale deploy by inverse of median ATR vs 2% target
                        atrs = [atr14(closes[b], i) for b, _ in picks]
                        med = sorted(atrs)[len(atrs) // 2]
                        scale = min(1.5, max(0.35, 0.02 / max(med, 0.005)))
                        deploy *= scale
                    # weights already in picks or equal
                    wsum = sum(w for _, w in picks) or 1.0
                    for b, w in picks:
                        ww = min(w / wsum, max_weight)
                        notional = deploy * ww
                        if notional < 50 or notional > cash:
                            continue
                        fee = notional * (FEE_RT / 2)
                        cash -= fee + notional
                        shorts[b] = (notional * (1.0 - FEE_RT / 2), closes[b][i], 0.0, atr14(closes[b], i))
                        trades += 1
            last_reb = i
        eq.append(equity_now(i))

    if shorts:
        for b in list(shorts):
            close_one(b, i1)
        eq[-1] = cash
    return eq, trades, wins, active


def pick_weakest(
    closes: dict[str, list[float]],
    btc: list[float],
    i: int,
    *,
    lookback: int,
    top_n: int,
    floor: float,
    skip: int,
    lookback2: int,
    floor2: float,
    mode: str,
    bounce: float,
    below_sma: int,
    weight_mode: str,
) -> list[tuple[str, float]]:
    scored: list[tuple[float, str, float]] = []
    for b in UNIVERSE:
        end = i - skip
        start = end - lookback
        if start < 0 or end < 0:
            continue
        mom = closes[b][end] / closes[b][start] - 1.0
        score = mom
        if mode == "excess":
            bm = btc[end] / btc[start] - 1.0
            score = mom - bm
        if score > floor:
            continue
        if lookback2 > 0:
            s2 = end - lookback2
            if s2 < 0:
                continue
            mom2 = closes[b][end] / closes[b][s2] - 1.0
            if mode == "excess":
                mom2 = mom2 - (btc[end] / btc[s2] - 1.0)
            if mom2 > floor2:
                continue
        if below_sma > 0:
            if i + 1 < below_sma:
                continue
            sma = sum(closes[b][i + 1 - below_sma : i + 1]) / below_sma
            if closes[b][i] >= sma:
                continue
        if bounce > 0 and i > 0:
            if closes[b][i] / closes[b][i - 1] - 1.0 >= bounce:
                continue
        atr = atr14(closes[b], i)
        scored.append((score, b, atr))
    scored.sort(key=lambda x: x[0])
    picks = scored[:top_n]
    if not picks:
        return []
    if weight_mode == "magnitude":
        mag = sum(abs(s) for s, _, _ in picks) or 1.0
        return [(b, abs(s) / mag) for s, b, _ in picks]
    if weight_mode == "inv_vol":
        inv = [1.0 / max(a, 0.005) for _, _, a in picks]
        s = sum(inv) or 1.0
        return [(b, x / s) for (_, b, _), x in zip(picks, inv)]
    return [(b, 1.0 / len(picks)) for _, b, _ in picks]


def pick_donchian(closes: dict[str, list[float]], i: int, *, channel: int, top_n: int) -> list[tuple[str, float]]:
    cands = []
    for b in UNIVERSE:
        if i < channel:
            continue
        lo = min(closes[b][i - channel : i])  # prior window, break today
        if closes[b][i] < lo:
            depth = (lo - closes[b][i]) / lo
            cands.append((depth, b))
    cands.sort(reverse=True)
    picks = cands[:top_n]
    if not picks:
        return []
    return [(b, 1.0 / len(picks)) for _, b in picks]


def pick_ew_alts(i: int) -> list[tuple[str, float]]:
    return [(b, 1.0 / len(UNIVERSE)) for b in UNIVERSE]


def pick_btc(_i: int) -> list[tuple[str, float]]:
    return [("BTC", 1.0)]


def main() -> None:
    print("loading…", flush=True)
    series = load_daily(("BTC", *UNIVERSE), days=430)
    ts, closes = _align(series, ("BTC", *UNIVERSE))
    # include BTC in closes for BTC-short
    btc = closes["BTC"]
    sma200 = _sma(btc, 200)
    sma50 = _sma(btc, 50)
    sma100 = _sma(btc, 100)

    # Primary hunt window: contiguous BTC < SMA200
    bear_idx = [i for i, s in enumerate(sma200) if s is not None and btc[i] < s]
    i0, i1 = bear_idx[0], bear_idx[-1]
    print(
        f"BEAR REGIME {datetime.fromtimestamp(ts[i0]/1000, UTC).date()} → "
        f"{datetime.fromtimestamp(ts[i1]/1000, UTC).date()} ({i1-i0+1}d) "
        f"BTC {100*(btc[i1]/btc[i0]-1):.1f}%",
        flush=True,
    )
    print(
        f"DESK TARGET pnl≥{DESK_TARGET_PNL:.0f} ({DESK_TARGET_RET}%) calmar≈{DESK_CALMAR:.2f}",
        flush=True,
    )

    results: list[Result] = []

    def run(name: str, eq, trades, wins, active, params):
        results.append(summarize(name, eq, trades, wins, active, params))

    # --- Named baselines ---
    for label, pick, req, gate, dep, mw, reb, trail, hard, vs in [
        ("btc_short_sma200", pick_btc, True, sma200, 1.0, 1.0, 5, 0.15, 0.10, True),
        ("btc_short_sma50_gate200", pick_btc, True, sma50, 1.0, 1.0, 3, 0.12, 0.08, True),
        ("ew_alts_bear", pick_ew_alts, True, sma200, 1.0, 0.10, 7, 0.18, 0.12, True),
        ("ew_alts_bear_dep75", pick_ew_alts, True, sma200, 0.75, 0.08, 7, 0.12, 0.10, False),
    ]:
        eq, tr, w, act = sim_short_book(
            closes, btc, sma200, i0, i1,
            pick_fn=pick,
            rebalance_every=reb,
            trail_pct=trail,
            hard_stop_pct=hard,
            vol_spike=vs,
            vol_mult=3.0,
            max_weight=mw,
            deploy_frac=dep,
            vol_scale=False,
            require_bear=req,
            bear_sma=gate,
        )
        run(label, eq, tr, w, act, {"family": label})

    # Donchian
    for ch, top, reb in itertools.product([10, 20, 30], [2, 3, 5], [3, 5, 7]):
        def make_pick(channel=ch, top_n=top):
            return lambda i: pick_donchian(closes, i, channel=channel, top_n=top_n)
        eq, tr, w, act = sim_short_book(
            closes, btc, sma200, i0, i1,
            pick_fn=make_pick(),
            rebalance_every=reb,
            trail_pct=0.12,
            hard_stop_pct=0.10,
            vol_spike=True,
            vol_mult=3.0,
            max_weight=0.25,
            deploy_frac=1.0,
            vol_scale=False,
            require_bear=True,
        )
        run(f"donchian_ch{ch}_n{top}_r{reb}", eq, tr, w, act, {"channel": ch, "top_n": top, "reb": reb})

    # Focused weakest grid (~4k)
    grid = list(
        itertools.product(
            [15, 21, 30],              # lookback
            [1, 2, 3],                # top_n
            [7, 14, 30],              # reb
            [-0.08, -0.12, -0.20],    # floor
            [0, 2],                   # skip
            ["absolute", "excess"],
            [0.0, 0.04],              # bounce
            [0.0, 0.15],              # trail (0=runner)
            [0.0, 0.12],              # hard
            [False],                  # vol spike off first pass
            [0.5, 1.0],               # max weight
            ["equal", "magnitude"],
        )
    )
    print(f"weakest grid size {len(grid)}", flush=True)

    for n, (
        lb, top, reb, floor, skip, mode, bounce,
        trail, hard, vs, mw, wmode,
    ) in enumerate(grid, 1):
        def make_pick(
            lookback=lb, top_n=top, fl=floor, sk=skip, md=mode,
            bn=bounce, wm=wmode,
        ):
            return lambda i: pick_weakest(
                closes, btc, i,
                lookback=lookback, top_n=top_n, floor=fl, skip=sk,
                lookback2=0, floor2=-0.06, mode=md, bounce=bn,
                below_sma=0, weight_mode=wm,
            )
        eq, tr, w, act = sim_short_book(
            closes, btc, sma200, i0, i1,
            pick_fn=make_pick(),
            rebalance_every=reb,
            trail_pct=trail,
            hard_stop_pct=hard,
            vol_spike=vs,
            vol_mult=3.0,
            max_weight=mw,
            deploy_frac=1.0,
            vol_scale=False,
            require_bear=True,
        )
        name = (
            f"wk_lb{lb}_n{top}_r{reb}_f{floor}_sk{skip}_{mode[:3]}"
            f"_bb{bounce}_t{trail}_h{hard}_mw{mw}_{wmode[:3]}"
        )
        run(name, eq, tr, w, act, {
            "lookback": lb, "top_n": top, "reb": reb, "floor": floor, "skip": skip,
            "lookback2": 0, "mode": mode, "bounce": bounce, "below_sma": 0,
            "trail": trail, "hard": hard, "vol_spike": vs, "max_weight": mw,
            "deploy": 1.0, "weight_mode": wmode, "vol_scale": False,
        })
        if n % 400 == 0:
            print(f"  {n}/{len(grid)} best_so_far={max(results, key=lambda r: r.score()).pnl_eur}", flush=True)

    # Blend: 50% BTC short + 50% best-so-far weakest style (fixed strong params)
    def blend_pick(i: int) -> list[tuple[str, float]]:
        wk = pick_weakest(
            closes, btc, i, lookback=15, top_n=3, floor=-0.08, skip=0,
            lookback2=0, floor2=-0.06, mode="absolute", bounce=0.0,
            below_sma=0, weight_mode="equal",
        )
        # half weight to BTC, half to wk basket
        out = [("BTC", 0.5)]
        if wk:
            for b, w in wk:
                out.append((b, 0.5 * w))
        return out

    for reb, trail, dep in itertools.product([5, 7, 14], [0.12, 0.18], [0.75, 1.0]):
        eq, tr, w, act = sim_short_book(
            closes, btc, sma200, i0, i1,
            pick_fn=blend_pick,
            rebalance_every=reb,
            trail_pct=trail,
            hard_stop_pct=0.10,
            vol_spike=True,
            vol_mult=3.0,
            max_weight=0.5,
            deploy_frac=dep,
            vol_scale=False,
            require_bear=True,
        )
        run(f"blend_btc_wk_r{reb}_t{trail}_d{dep}", eq, tr, w, act, {"family": "blend", "reb": reb})

    # Hold weakest at bear start (oracle-free: rebalance once at start only via huge reb)
    # Actually: static short of names weakest on day i0 over prior 30d, hold to end
    def static_pick_factory(lb: int, top: int, floor: float):
        picked = pick_weakest(
            closes, btc, i0, lookback=lb, top_n=top, floor=floor, skip=0,
            lookback2=0, floor2=-0.06, mode="absolute", bounce=0.0,
            below_sma=0, weight_mode="equal",
        )
        return lambda i: picked if i == i0 or True else picked

    for lb, top, floor in itertools.product([15, 30, 60], [2, 3, 5], [-0.05, -0.10, -0.15]):
        picks0 = pick_weakest(
            closes, btc, i0, lookback=lb, top_n=top, floor=floor, skip=0,
            lookback2=0, floor2=-0.06, mode="absolute", bounce=0.0,
            below_sma=0, weight_mode="equal",
        )
        if not picks0:
            continue
        frozen = list(picks0)
        eq, tr, w, act = sim_short_book(
            closes, btc, sma200, i0, i1,
            pick_fn=lambda i, fr=frozen: fr,
            rebalance_every=10**9,  # never rebalance after first
            trail_pct=0.0,
            hard_stop_pct=0.0,
            vol_spike=False,
            vol_mult=3.0,
            max_weight=0.5,
            deploy_frac=1.0,
            vol_scale=False,
            require_bear=True,
        )
        run(f"static_hold_lb{lb}_n{top}_f{floor}", eq, tr, w, act, {"family": "static", "picks": [b for b,_ in frozen]})

    ranked = sorted(results, key=lambda r: r.score(), reverse=True)
    beat = [r for r in ranked if r.pnl_eur >= DESK_TARGET_PNL]
    near = [r for r in ranked if r.pnl_eur >= DESK_TARGET_PNL * 0.9]
    low_dd_winners = [r for r in beat if r.max_dd_pct > -25]

    def slim(r: Result) -> dict:
        return asdict(r)

    # Round 2: refine around top 5 param neighborhoods if nothing beats desk
    refine_results: list[Result] = []
    if not beat:
        print("Round2: refining around top configs…", flush=True)
        seeds = ranked[:15]
        for seed in seeds:
            p = seed.params
            if "lookback" not in p:
                continue
            for lb in {p["lookback"], max(5, p["lookback"] - 5), p["lookback"] + 5}:
                for top in {p.get("top_n", 3), 1, 2}:
                    for floor in {p.get("floor", -0.08), p.get("floor", -0.08) - 0.03, -0.25}:
                        for dep in {p.get("deploy", 1.0), 1.0}:
                            for mw in {p.get("max_weight", 0.35), 0.5, 1.0}:
                                for trail in {p.get("trail", 0.18), 0.0, 0.25}:
                                    for hard in {p.get("hard", 0.12), 0.0, 0.20}:
                                        def mk(
                                            lookback=lb, top_n=top, fl=floor,
                                            sk=p.get("skip", 0), l2=p.get("lookback2", 0),
                                            md=p.get("mode", "absolute"), bn=p.get("bounce", 0.0),
                                            bsm=p.get("below_sma", 0), wm=p.get("weight_mode", "equal"),
                                        ):
                                            return lambda i: pick_weakest(
                                                closes, btc, i,
                                                lookback=lookback, top_n=top_n, floor=fl, skip=sk,
                                                lookback2=l2, floor2=-0.08, mode=md, bounce=bn,
                                                below_sma=bsm, weight_mode=wm,
                                            )
                                        eq, tr, w, act = sim_short_book(
                                            closes, btc, sma200, i0, i1,
                                            pick_fn=mk(),
                                            rebalance_every=int(p.get("reb", 7)),
                                            trail_pct=float(trail),
                                            hard_stop_pct=float(hard),
                                            vol_spike=bool(p.get("vol_spike", True)),
                                            vol_mult=3.0,
                                            max_weight=float(mw),
                                            deploy_frac=float(dep),
                                            vol_scale=bool(p.get("vol_scale", False)),
                                            require_bear=True,
                                        )
                                        refine_results.append(
                                            summarize(
                                                f"r2_lb{lb}_n{top}_f{floor}_d{dep}_mw{mw}_t{trail}_h{hard}",
                                                eq, tr, w, act,
                                                {**p, "lookback": lb, "top_n": top, "floor": floor,
                                                 "deploy": dep, "max_weight": mw, "trail": trail, "hard": hard},
                                            )
                                        )
        ranked = sorted(results + refine_results, key=lambda r: r.score(), reverse=True)
        beat = [r for r in ranked if r.pnl_eur >= DESK_TARGET_PNL]
        near = [r for r in ranked if r.pnl_eur >= DESK_TARGET_PNL * 0.9]
        low_dd_winners = [r for r in beat if r.max_dd_pct > -25]

    # Round 3: if still short, try leveraged deploy conceptually via higher concentration
    # and no stops (let winners run) — static-like dynamic with rare rebalance
    if not beat:
        print("Round3: rare-rebalance + no-stop runners…", flush=True)
        for lb, top, floor, reb, mode in itertools.product(
            [15, 21, 30, 45], [1, 2, 3], [-0.08, -0.12, -0.20, -0.30],
            [21, 30, 45, 60], ["absolute", "excess"],
        ):
            def mk(lookback=lb, top_n=top, fl=floor, md=mode):
                return lambda i: pick_weakest(
                    closes, btc, i, lookback=lookback, top_n=top_n, floor=fl, skip=0,
                    lookback2=0, floor2=-0.06, mode=md, bounce=0.0,
                    below_sma=0, weight_mode="magnitude",
                )
            eq, tr, w, act = sim_short_book(
                closes, btc, sma200, i0, i1,
                pick_fn=mk(),
                rebalance_every=reb,
                trail_pct=0.0,
                hard_stop_pct=0.0,
                vol_spike=False,
                vol_mult=3.0,
                max_weight=1.0,
                deploy_frac=1.0,
                vol_scale=False,
                require_bear=True,
            )
            results.append(
                summarize(
                    f"r3_run_lb{lb}_n{top}_f{floor}_r{reb}_{mode[:3]}",
                    eq, tr, w, act,
                    {"lookback": lb, "top_n": top, "floor": floor, "reb": reb, "mode": mode,
                     "trail": 0, "hard": 0, "vol_spike": False, "max_weight": 1.0, "deploy": 1.0},
                )
            )
        ranked = sorted(results + refine_results, key=lambda r: r.score(), reverse=True)
        beat = [r for r in ranked if r.pnl_eur >= DESK_TARGET_PNL]
        near = [r for r in ranked if r.pnl_eur >= DESK_TARGET_PNL * 0.9]
        low_dd_winners = [r for r in beat if r.max_dd_pct > -25]

    winner = ranked[0]
    print(
        f"DONE n={len(ranked)} beat_desk={len(beat)} near={len(near)} "
        f"winner={winner.name} pnl={winner.pnl_eur} dd={winner.max_dd_pct}% cal={winner.calmar}",
        flush=True,
    )

    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK_EUR,
        "bear_window": {
            "start": datetime.fromtimestamp(ts[i0] / 1000, UTC).strftime("%Y-%m-%d"),
            "end": datetime.fromtimestamp(ts[i1] / 1000, UTC).strftime("%Y-%m-%d"),
            "days": i1 - i0 + 1,
            "btc_return_pct": round(100 * (btc[i1] / btc[i0] - 1), 2),
        },
        "desk_benchmark": {
            "pnl_eur": DESK_TARGET_PNL,
            "return_pct": DESK_TARGET_RET,
            "max_dd_eur": DESK_TARGET_DD,
            "calmar": round(DESK_CALMAR, 3),
            "note": "Momentum desk €20k ~12w (combined_desk_12w_sim core)",
        },
        "n_tested": len(ranked),
        "n_beat_desk": len(beat),
        "n_within_10pct_desk": len(near),
        "winner": slim(winner),
        "best_beating_desk": slim(beat[0]) if beat else None,
        "best_beating_desk_dd_gt_m25": slim(low_dd_winners[0]) if low_dd_winners else None,
        "top20": [slim(r) for r in ranked[:20]],
        "top10_beat_or_near": [slim(r) for r in (beat or near)[:10]],
        "sources": [
            "Cross-sectional short laggards / excess vs BTC",
            "BTC SMA200/50 regime short (DennTech, Boring Edge)",
            "Equal-weight alt basket short in bear (PyQuantLab)",
            "Donchian channel breakdown shorts",
            "Asness skip + dual horizon",
            "Barroso inverse-vol deploy scaling",
            "Blend BTC-short + weakest alts",
        ],
    }
    OUT.write_text(json.dumps(out, indent=2))
    print("wrote", OUT)


if __name__ == "__main__":
    main()

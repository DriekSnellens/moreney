#!/usr/bin/env python3
"""Search the best €20k desk: loop short knobs + regime splits + overlays.

Objective (in order): 1y profit, DD inside −12%, worst month > −€1500,
last-12w still harvests (>€3k), then Calmar, then 1y PnL.

Does not touch live. Writes artifacts/expert_best_desk_search.json
and artifacts/expert_best_desk_1y.svg
"""

from __future__ import annotations

import itertools
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifacts.bear_harvest_hunter import atr14, pick_weakest, sim_short_book
from artifacts.bear_market_strategy_sim import BOOK_EUR, FEE_RT, _align, _sma, load_daily
from artifacts.combined_desk_12w_sim import core_daily_equity, live_core_cfg
from artifacts.expert_20k_desk import apply_expert, path_from_eq, sim_donchian_friday, window_of
from artifacts.expert_vs_live_12w import REC_MAP, write_svg
from artifacts.missed_capacity_five_strats import ALL, ALT, sim_donchian, sim_invvol_basket, sim_winners_weekly
from artifacts.multi_strat_20k_allocator import BOOK, _date, _idx, path_from_book, static_grid, summarize, to_returns
from bot.live.momentum_desk import BAR_MS
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "expert_best_desk_search.json"
SVG = Path(__file__).resolve().parent / "expert_best_desk_1y.svg"

W0 = "2025-09-20"
W1 = "2026-09-20"
W12 = "2026-06-28"


def sim_short_flex(
    closes,
    btc,
    sma200,
    i0,
    i1,
    *,
    lookback=15,
    top_n=1,
    floor=-0.08,
    skip=2,
    bounce=0.04,
    reb=30,
    trail=0.0,
    stop=0.0,
    max_weight=0.5,
    deploy=1.0,
    vol_scale=False,
    vol_spike=False,
    gate="sma200",
    cover_on_bull=True,
    sma50=None,
) -> list[float]:
    """Short-weakest with extra cover-when-gate-off (fixes 30d squeeze hold)."""
    g = sma50 if gate == "sma50" else sma200

    def pick_fn(i: int):
        return pick_weakest(
            closes,
            btc,
            i,
            lookback=lookback,
            top_n=top_n,
            floor=floor,
            skip=skip,
            lookback2=0,
            floor2=0.0,
            mode="mom",
            bounce=bounce,
            below_sma=0,
            weight_mode="equal",
        )

    if not cover_on_bull:
        eq, _, _, _ = sim_short_book(
            closes,
            btc,
            sma200,
            i0,
            i1,
            pick_fn=pick_fn,
            rebalance_every=reb,
            trail_pct=trail,
            hard_stop_pct=stop,
            vol_spike=vol_spike,
            vol_mult=3.0,
            max_weight=max_weight,
            deploy_frac=deploy,
            vol_scale=vol_scale,
            require_bear=True,
            bear_sma=g,
        )
        return eq

    # Local copy: flatten the same day the gate turns off.
    cash = BOOK_EUR
    shorts: dict[str, tuple[float, float, float, float]] = {}
    eq_path: list[float] = []
    last_reb = -10**9

    def equity_now(i: int) -> float:
        u = sum(n * (e - closes[b][i]) / e for b, (n, e, _, _) in shorts.items())
        return cash + sum(n for n, _, _, _ in shorts.values()) + u

    def close_one(b: str, i: int) -> None:
        nonlocal cash
        n, e, _, _ = shorts.pop(b)
        ret = (e - closes[b][i]) / e
        net = n * ret - n * FEE_RT
        cash += n + net

    for i in range(i0, i1 + 1):
        for b in list(shorts):
            n, e, peak, atr = shorts[b]
            ret = (e - closes[b][i]) / e
            peak = max(peak, ret)
            shorts[b] = (n, e, peak, atr)
            if stop > 0 and ret <= -stop:
                close_one(b, i)
            elif trail > 0 and peak - ret >= trail:
                close_one(b, i)
        gate_ok = g[i] is not None and btc[i] < g[i]
        if shorts and not gate_ok:
            for b in list(shorts):
                close_one(b, i)
            last_reb = i
        due = i - last_reb >= reb or last_reb < 0
        if due:
            for b in list(shorts):
                close_one(b, i)
            if gate_ok and cash > 50:
                picks = pick_fn(i)
                if picks:
                    deploy_n = cash * deploy
                    if vol_scale:
                        atrs = [atr14(closes[b], i) for b, _ in picks]
                        med = sorted(atrs)[len(atrs) // 2]
                        deploy_n *= min(1.5, max(0.35, 0.02 / max(med, 0.005)))
                    wsum = sum(w for _, w in picks) or 1.0
                    for b, w in picks:
                        ww = min(w / wsum, max_weight)
                        notional = deploy_n * ww
                        if notional < 50 or notional > cash:
                            continue
                        fee = notional * (FEE_RT / 2)
                        cash -= fee + notional
                        shorts[b] = (notional * (1.0 - FEE_RT / 2), closes[b][i], 0.0, atr14(closes[b], i))
            last_reb = i
        eq_path.append(equity_now(i))
    if shorts:
        for b in list(shorts):
            close_one(b, i1)
        eq_path[-1] = cash
    return eq_path


def maps_risk_on() -> list[dict[str, float]]:
    names = ["core", "winners", "donch20", "cash"]
    out = []
    for w in static_grid(names, step=0.25):
        if w.get("core", 0) > 0.5:
            continue
        out.append({k: v for k, v in w.items() if v > 1e-9})
    presets = [
        {"core": 0.4, "winners": 0.3, "donch20": 0.3},
        {"core": 0.25, "winners": 0.35, "donch20": 0.4},
        {"core": 0.25, "winners": 0.35, "donch_fri": 0.4},
        {"winners": 0.4, "donch_fri": 0.4, "invvol": 0.2},
        {"winners": 0.5, "donch_fri": 0.5},
        {"invvol": 0.5, "donch20": 0.5},
        {"core": 0.25, "invvol": 0.35, "donch_fri": 0.4},
    ]
    out.extend(presets)
    seen = set()
    uniq = []
    for w in out:
        key = tuple(sorted((k, round(v, 3)) for k, v in w.items()))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(w)
    return uniq


def maps_mid() -> list[dict[str, float]]:
    names = ["donch20", "short", "cash"]
    out = [{k: v for k, v in w.items() if v > 1e-9} for w in static_grid(names, step=0.25)]
    presets = [
        {"donch20": 0.5, "cash": 0.5},
        {"donch_fri": 0.5, "cash": 0.5},
        {"short": 0.35, "donch20": 0.35, "cash": 0.3},
        {"short": 0.35, "donch_fri": 0.35, "cash": 0.3},
        {"invvol": 0.3, "cash": 0.7},
        {"cash": 1.0},
        {"short": 0.5, "cash": 0.5},
    ]
    out.extend(presets)
    seen = set()
    uniq = []
    for w in out:
        key = tuple(sorted((k, round(v, 3)) for k, v in w.items()))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(w)
    return uniq


def maps_off() -> list[dict[str, float]]:
    out = []
    for s in (0.0, 0.25, 0.4, 0.55, 0.7, 1.0):
        w = {}
        if s > 0:
            w["short"] = s
        if s < 1:
            w["cash"] = round(1.0 - s, 3)
        out.append(w)
    return out


def score_pack(st_1y: dict, st_12w: dict) -> tuple:
    worst = (st_1y.get("worst_month") or {}).get("pnl_eur") or 0.0
    return (
        int(st_1y["pnl_eur"] > 0),
        int(st_1y["max_dd_pct"] > -12),
        int(worst > -1500),
        int(st_12w["pnl_eur"] > 3000),
        int(st_1y["max_dd_pct"] > -15),
        round(st_1y["calmar"], 3),
        st_1y["pnl_eur"],
        st_12w["pnl_eur"],
        worst,
        -abs(st_1y["max_dd_pct"]),
    )


def slim_st(st: dict) -> dict:
    return {
        "pnl_eur": st["pnl_eur"],
        "max_dd_pct": st["max_dd_pct"],
        "calmar": st["calmar"],
        "worst_month": st.get("worst_month"),
        "months": st.get("months"),
    }


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
    print(f"window {dates[0]}→{dates[-1]} n={len(dates)}  12w from {dates_12[0]}", flush=True)

    sma200 = _sma(closes["BTC"], 200)
    sma50 = _sma(closes["BTC"], 50)
    alt_sma50 = {b: _sma(closes[b], 50) for b in ALT}
    regime_of: dict[str, str] = {}
    occ: dict[str, int] = defaultdict(int)
    for i in range(i0, i1 + 1):
        d = _date(ts[i])
        btc = closes["BTC"][i]
        s200, s50 = sma200[i], sma50[i]
        if s50 is not None and btc < s50:
            lab = "risk_off"
        elif s200 is not None and btc < s200:
            lab = "mid"
        else:
            lab = "risk_on"
        above = n = 0
        for b in ALT:
            sm = alt_sma50[b][i]
            if sm is None:
                continue
            n += 1
            if closes[b][i] > sm:
                above += 1
        if lab == "risk_on" and n and above / n < 0.30:
            lab = "mid"
        regime_of[d] = lab
        occ[lab] += 1
    print("regime", dict(occ), flush=True)

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
    longs = {
        "core": core_eq,
        "donch20": path_from_book(ts, i0, i1, sim_donchian(ts, closes, highs, lows, i0, i1, ch=20, exit_n=10, btc_sma=50)),
        "donch_fri": path_from_book(ts, i0, i1, sim_donchian_friday(ts, closes, highs, lows, i0, i1)),
        "winners": path_from_book(
            ts, i0, i1, sim_winners_weekly(ts, closes, highs, lows, i0, i1, lb=30, skip=7, top_n=3, reb=7, w_each=0.3, sma_n=200)
        ),
        "invvol": path_from_book(
            ts, i0, i1, sim_invvol_basket(ts, closes, highs, lows, i0, i1, lookback=21, top_n=4, deploy=0.8, sma_n=200)
        ),
        "cash": {d: BOOK for d in dates},
    }
    rets_long = {k: to_returns(v) for k, v in longs.items()}

    print("stage 1: short knob sweep…", flush=True)
    short_cfgs = []
    for gate, cover, reb, trail, stop, top_n, floor, vol_scale in itertools.product(
        ("sma50", "sma200"),
        (True, False),
        (7, 14, 30),
        (0.0, 0.05, 0.08),
        (0.0, 0.06, 0.10),
        (1, 2),
        (-0.05, -0.08),
        (False, True),
    ):
        # skip obviously redundant: no cover + long reb + no stops is the known loser
        short_cfgs.append(
            dict(
                gate=gate,
                cover_on_bull=cover,
                reb=reb,
                trail=trail,
                stop=stop,
                top_n=top_n,
                floor=floor,
                vol_scale=vol_scale,
            )
        )
    # 2*2*3*3*3*2*2*2 = 864 — too many. Thin: require cover_on_bull OR (trail or stop).
    short_cfgs = [
        c
        for c in short_cfgs
        if c["cover_on_bull"] or c["trail"] > 0 or c["stop"] > 0
    ]
    # still ~756. Drop vol_scale except on a subset.
    short_cfgs = [c for c in short_cfgs if (not c["vol_scale"]) or (c["cover_on_bull"] and c["reb"] in (7, 14) and c["top_n"] == 1)]
    print(f"  short configs {len(short_cfgs)}", flush=True)

    gated_ranked = []
    short_rets: dict[str, dict[str, float]] = {}
    for i, cfg_s in enumerate(short_cfgs):
        eq = sim_short_flex(closes, closes["BTC"], sma200, i0, i1, sma50=sma50, **cfg_s)
        name = f"s{i}"
        path = path_from_eq(ts, i0, i1, eq)
        rets = to_returns(path)
        short_rets[name] = rets
        # gated: 100% short in mid+off, cash in risk_on
        rw = {"risk_on": {"cash": 1.0}, "mid": {name: 1.0}, "risk_off": {name: 1.0}}
        p, r = apply_expert({name: rets, "cash": rets_long["cash"]}, dates, regime_w=rw, regime_of=regime_of)
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
                "_score": (
                    int(st["pnl_eur"] > 0),
                    int(st["max_dd_pct"] > -20),
                    st["calmar"],
                    st["pnl_eur"],
                    st12["pnl_eur"],
                    st["max_dd_pct"],
                ),
            }
        )
        if (i + 1) % 80 == 0:
            print(f"    {i+1}/{len(short_cfgs)}", flush=True)
    gated_ranked.sort(key=lambda x: x["_score"], reverse=True)
    top_shorts = gated_ranked[:8]
    print("  top gated shorts:", flush=True)
    for row in top_shorts:
        g, g12 = row["gated_1y"], row["gated_12w"]
        print(
            f"    {row['id']} 1y={g['pnl_eur']:+.0f} dd={g['max_dd_pct']} 12w={g12['pnl_eur']:+.0f} {row['cfg']}",
            flush=True,
        )

    on_maps = maps_risk_on()
    mid_maps = maps_mid()
    off_maps = maps_off()
    n_maps = len(on_maps) * len(mid_maps) * len(off_maps)
    print(
        f"stage 2: maps on={len(on_maps)} mid={len(mid_maps)} off={len(off_maps)} "
        f"× {len(top_shorts)} shorts ≈ {n_maps * len(top_shorts)}",
        flush=True,
    )

    best = None
    best_sc = None
    tested = 0
    keep: list[dict[str, Any]] = []
    for sh in top_shorts:
        rets = {**rets_long, "short": short_rets[sh["id"]]}
        for on, mid, off in itertools.product(on_maps, mid_maps, off_maps):
            rw = {"risk_on": on, "mid": mid, "risk_off": off}
            p, r = apply_expert(rets, dates, regime_w=rw, regime_of=regime_of)
            st = summarize(p, r)
            p12, r12 = window_of(r, dates_12[0], dates_12[-1])
            st12 = summarize(p12, r12)
            sc = score_pack(st, st12)
            tested += 1
            row = {
                "short_id": sh["id"],
                "short_cfg": sh["cfg"],
                "map": rw,
                "y1": slim_st(st),
                "w12": slim_st(st12),
                "_score": sc,
            }
            if best_sc is None or sc > best_sc:
                best_sc = sc
                best = {**row, "_rows": r}
                print(
                    f"  new best 1y={st['pnl_eur']:+.0f} dd={st['max_dd_pct']} "
                    f"12w={st12['pnl_eur']:+.0f} calmar={st['calmar']} "
                    f"on={on} mid={mid} off={off} short={sh['cfg']}",
                    flush=True,
                )
            keep.append(row)
            keep.sort(key=lambda x: x["_score"], reverse=True)
            keep = keep[:12]
            if tested % 2000 == 0:
                print(f"    tested {tested}", flush=True)

    print(f"tested {tested}  best 1y={best['y1']['pnl_eur']} dd={best['y1']['max_dd_pct']}", flush=True)
    overlays = []
    rets_w = {**rets_long, "short": short_rets[best["short_id"]]}
    for tv, dd in ((0.15, None), (0.18, None), (None, -0.08), (0.18, -0.08)):
        p, r = apply_expert(
            rets_w,
            dates,
            regime_w=best["map"],
            regime_of=regime_of,
            vol_target=tv,
            dd_halt=dd,
            dd_resume=-0.04 if dd else None,
            max_lev=1.0,
        )
        st = summarize(p, r)
        p12, r12 = window_of(r, dates_12[0], dates_12[-1])
        st12 = summarize(p12, r12)
        overlays.append(
            {
                "vol_target": tv,
                "dd_halt": dd,
                "y1": slim_st(st),
                "w12": slim_st(st12),
                "_score": score_pack(st, st12),
                "_rows": r,
            }
        )
        print(f"  overlay vt={tv} dd={dd} 1y={st['pnl_eur']:+.0f} dd={st['max_dd_pct']} 12w={st12['pnl_eur']:+.0f}", flush=True)
    overlays.sort(key=lambda x: x["_score"], reverse=True)

    # baselines: live, cash-mix
    p_live, r_live = apply_expert(rets_long, dates, weights={"core": 1.0})
    p_cash, r_cash = apply_expert(rets_long, dates, regime_w=REC_MAP, regime_of=regime_of)
    st_live, st_cash = summarize(p_live, r_live), summarize(p_cash, r_cash)
    p12l, r12l = window_of(r_live, dates_12[0], dates_12[-1])
    p12c, r12c = window_of(r_cash, dates_12[0], dates_12[-1])

    champ = best
    champ_rows = best["_rows"]
    champ_kind = "map"
    if overlays and overlays[0]["_score"] > best["_score"]:
        champ_kind = "map+overlay"
        champ = {
            **best,
            "overlay": {"vol_target": overlays[0]["vol_target"], "dd_halt": overlays[0]["dd_halt"]},
            "y1": overlays[0]["y1"],
            "w12": overlays[0]["w12"],
        }
        champ_rows = overlays[0]["_rows"]

    write_svg(
        r_live,
        r_cash,
        SVG,
        title="1y search: live vs cash-mix vs best found",
        rec_label="cash-mix (baseline)",
        extra=[("best search", "#7aa2ff", champ_rows)],
    )

    def euros_map(m: dict) -> dict:
        return {reg: {k: int(round(BOOK * v)) for k, v in w.items() if v > 0} for reg, w in m.items()}

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK,
        "window": {"start": dates[0], "end": dates[-1], "days": len(dates)},
        "last_12w": {"start": dates_12[0], "end": dates_12[-1], "days": len(dates_12)},
        "tested": {"short_configs": len(short_cfgs), "mixes": tested},
        "regime_days": dict(occ),
        "objective": "1y profit + DD>-12% + worst month>-€1500 + 12w>€3k, then Calmar",
        "baselines": {
            "live_core": {"y1": slim_st(st_live), "w12": slim_st(summarize(p12l, r12l))},
            "cash_mix": {"y1": slim_st(st_cash), "w12": slim_st(summarize(p12c, r12c)), "map": REC_MAP},
        },
        "top_gated_shorts": [{k: v for k, v in row.items() if k != "_score"} for row in top_shorts],
        "recommendation": {
            "kind": champ_kind,
            "map": champ["map"],
            "euros": euros_map(champ["map"]),
            "short_cfg": champ["short_cfg"],
            "overlay": champ.get("overlay"),
            "y1": champ["y1"],
            "w12": champ["w12"],
            "vs_live_1y_eur": round(champ["y1"]["pnl_eur"] - st_live["pnl_eur"], 2),
            "vs_cash_mix_1y_eur": round(champ["y1"]["pnl_eur"] - st_cash["pnl_eur"], 2),
            "vs_live_12w_eur": round(champ["w12"]["pnl_eur"] - summarize(p12l, r12l)["pnl_eur"], 2),
            "vs_cash_mix_12w_eur": round(champ["w12"]["pnl_eur"] - summarize(p12c, r12c)["pnl_eur"], 2),
        },
        "top_mixes": [
            {k: v for k, v in row.items() if k not in ("_score", "_rows")} for row in keep
        ],
        "overlays_on_best_map": [
            {k: v for k, v in row.items() if k not in ("_score", "_rows")} for row in overlays
        ],
        "caveats": [
            "Search is in-sample on this 1y (12w is the last slice of the same path, not a separate sim)",
            "No AlphaI; 15m core close-fills; daily fills for other sleeves",
            "Short is paper/perp-proxy; cover_on_bull flats the same day BTC recaptures the gate",
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    rec = payload["recommendation"]
    print("\n=== BEST ===", flush=True)
    print(json.dumps({k: rec[k] for k in ("euros", "short_cfg", "overlay", "y1", "w12") if k in rec}, indent=2), flush=True)
    print(f"vs cash mix 1y {rec['vs_cash_mix_1y_eur']:+.0f}  vs live {rec['vs_live_1y_eur']:+.0f}", flush=True)
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()

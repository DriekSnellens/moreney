#!/usr/bin/env python3
"""Past-year A/B: live 15m desk vs recommended expert mix, one €20k book.

Fresh start 2025-09-20 → 2026-09-20. Same accounting as expert_vs_live_12w.py.

Writes artifacts/expert_vs_live_1y.json and artifacts/expert_vs_live_1y.svg
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from artifacts.bear_market_strategy_sim import _align, _sma, load_daily
from artifacts.combined_desk_12w_sim import core_daily_equity, live_core_cfg
from artifacts.expert_20k_desk import (
    apply_expert,
    path_from_eq,
    sim_donchian_friday,
    sim_short_harvest,
)
from artifacts.expert_vs_live_12w import REC_MAP, weekly, write_svg
from artifacts.missed_capacity_five_strats import ALL, ALT, sim_donchian, sim_winners_weekly
from artifacts.multi_strat_20k_allocator import (
    BOOK,
    _date,
    _idx,
    path_from_book,
    summarize,
    to_returns,
)
from bot.live.momentum_desk import BAR_MS
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "expert_vs_live_1y.json"
SVG = Path(__file__).resolve().parent / "expert_vs_live_1y.svg"

W0 = "2025-09-20"
W1 = "2026-09-20"


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
    print(f"window {dates[0]}→{dates[-1]} n={len(dates)} first_ts={ts[0]}", flush=True)
    print(f"  daily history starts {_date(ts[0])} ({(i0)} bars before window)", flush=True)

    sma200 = _sma(closes["BTC"], 200)
    sma50 = _sma(closes["BTC"], 50)
    alt_sma50 = {b: _sma(closes[b], 50) for b in ALT}
    sma200_ready = sum(1 for i in range(i0, i1 + 1) if sma200[i] is not None)
    print(f"  SMA200 ready {sma200_ready}/{len(dates)} days", flush=True)

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
        if lab == "risk_on" and n and (above / n) < 0.30:
            lab = "mid"
        regime_of[d] = lab
        occ[lab] += 1
    print("regime days", dict(occ), flush=True)

    print("simulating live 15m core (1y)…", flush=True)
    cfg = live_core_cfg()
    end_ms = int(datetime.fromisoformat(W1).replace(tzinfo=UTC).timestamp() * 1000)
    start_ms = int(datetime.fromisoformat(W0).replace(tzinfo=UTC).timestamp() * 1000)
    end_ms = end_ms // BAR_MS * BAR_MS
    days_span = int((end_ms - start_ms) / 86_400_000) + 40
    candles = load_candles(("BTC", *cfg.universe), days=days_span, end_ms=end_ms, refresh=False)
    core_res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    core_sum = core_res.summary()
    print(f"  core realized={core_sum.get('realized_eur')} trades={core_sum.get('trades')}", flush=True)
    core_eq = {
        r["date"]: float(r["equity_eur"])
        for r in core_daily_equity(
            core_res, daily_ts=ts, daily_closes=closes, start_ms=start_ms, end_ms=end_ms, book=BOOK
        )
    }

    print("simulating mix sleeves…", flush=True)
    donch = sim_donchian(ts, closes, highs, lows, i0, i1, ch=20, exit_n=10, btc_sma=50)
    donch_fri = sim_donchian_friday(ts, closes, highs, lows, i0, i1)
    winners = sim_winners_weekly(
        ts, closes, highs, lows, i0, i1, lb=30, skip=7, top_n=3, reb=7, w_each=0.3, sma_n=200
    )
    short_eq = path_from_eq(ts, i0, i1, sim_short_harvest(closes, closes["BTC"], sma200, i0, i1))
    eq = {
        "core": core_eq,
        "donch20": path_from_book(ts, i0, i1, donch),
        "donch_fri": path_from_book(ts, i0, i1, donch_fri),
        "winners": path_from_book(ts, i0, i1, winners),
        "short": short_eq,
        "cash": {d: BOOK for d in dates},
    }
    rets = {k: to_returns(v) for k, v in eq.items()}

    # Down = short laggards; up = momentum; mid = long strength + short weakness.
    ls_map = {
        "risk_on": {"core": 0.25, "winners": 0.35, "donch20": 0.40},
        "mid": {"donch20": 0.35, "short": 0.35, "cash": 0.30},
        "risk_off": {"short": 0.45, "cash": 0.55},
    }
    ls_fri_map = {
        "risk_on": {"core": 0.25, "winners": 0.35, "donch_fri": 0.40},
        "mid": {"donch_fri": 0.35, "short": 0.30, "cash": 0.35},
        "risk_off": {"short": 0.40, "cash": 0.60},
    }

    p_live, r_live = apply_expert(rets, dates, weights={"core": 1.0})
    p_rec, r_rec = apply_expert(rets, dates, regime_w=REC_MAP, regime_of=regime_of)
    p_ls, r_ls = apply_expert(rets, dates, regime_w=ls_map, regime_of=regime_of)
    p_lsf, r_lsf = apply_expert(rets, dates, regime_w=ls_fri_map, regime_of=regime_of)
    p_short, r_short = apply_expert(rets, dates, weights={"short": 1.0})
    st_live, st_rec = summarize(p_live, r_live), summarize(p_rec, r_rec)
    st_ls, st_lsf = summarize(p_ls, r_ls), summarize(p_lsf, r_lsf)
    st_short = summarize(p_short, r_short)

    write_svg(
        r_live,
        r_rec,
        SVG,
        title="Afgelopen jaar · €20k · live vs cash-mix vs long/short-mix",
        rec_label="mix: cash in bear",
        extra=[("mix: short in bear", "#7aa2ff", r_ls)],
    )

    def month_col(st, m):
        return next((b["pnl_eur"] for b in st["months"] if b["month"] == m), 0.0)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK,
        "window": {"start": dates[0], "end": dates[-1], "days": len(dates)},
        "sma200_ready_days": sma200_ready,
        "why_cash_mix_sits": (
            "Cash-mix is idle in risk_off on purpose: short-weakest as a 100% sleeve "
            "did not pay enough vs its DD to beat sitting in EUR. The long/short mix "
            "is the desk you describe (momentum up, harvest down)."
        ),
        "caveats": [
            "Fresh 1y start at €20k; daily PnL / €20k (not compounding on shrunken equity)",
            "Live = 100% 15m momentum desk; no AlphaI timeline",
            "Short = paper bear-harvest pack (top-1 weakest, SMA200 gate, 30d reb, no stops)",
            "If SMA200 is not yet ready, BTC<SMA50 still forces risk_off / cash-or-short",
        ],
        "regime_days": dict(occ),
        "current_live_core": {
            **st_live,
            "trades": core_sum.get("trades"),
            "realized_eur": core_sum.get("realized_eur"),
        },
        "cash_mix": {**st_rec, "map": REC_MAP},
        "long_short_mix": {**st_ls, "map": ls_map},
        "long_short_donch_fri": {**st_lsf, "map": ls_fri_map},
        "short_unit_20k": st_short,
        "delta": {
            "cash_mix_minus_live_eur": round(st_rec["pnl_eur"] - st_live["pnl_eur"], 2),
            "ls_mix_minus_live_eur": round(st_ls["pnl_eur"] - st_live["pnl_eur"], 2),
            "ls_mix_minus_cash_mix_eur": round(st_ls["pnl_eur"] - st_rec["pnl_eur"], 2),
            "ls_fri_minus_live_eur": round(st_lsf["pnl_eur"] - st_live["pnl_eur"], 2),
            "dd_live_pct": st_live["max_dd_pct"],
            "dd_cash_mix_pct": st_rec["max_dd_pct"],
            "dd_ls_mix_pct": st_ls["max_dd_pct"],
        },
        "months": [
            {
                "month": a["month"],
                "live_pnl_eur": a["pnl_eur"],
                "cash_mix_pnl_eur": month_col(st_rec, a["month"]),
                "ls_mix_pnl_eur": month_col(st_ls, a["month"]),
            }
            for a in st_live["months"]
        ],
        "weeks_n": len(weekly(r_live)),
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n=== 1y €20k ===", flush=True)
    print(f"live     {st_live['pnl_eur']:+.0f}  dd={st_live['max_dd_pct']}%", flush=True)
    print(f"cash mix {st_rec['pnl_eur']:+.0f}  dd={st_rec['max_dd_pct']}%", flush=True)
    print(f"LS mix   {st_ls['pnl_eur']:+.0f}  dd={st_ls['max_dd_pct']}%", flush=True)
    print(f"LS fri   {st_lsf['pnl_eur']:+.0f}  dd={st_lsf['max_dd_pct']}%", flush=True)
    print(f"short 20k {st_short['pnl_eur']:+.0f}  dd={st_short['max_dd_pct']}%", flush=True)
    print(f"wrote {OUT}", flush=True)
    print(f"wrote {SVG}", flush=True)


if __name__ == "__main__":
    main()

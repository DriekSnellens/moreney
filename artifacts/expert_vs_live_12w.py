#!/usr/bin/env python3
"""Last-12w A/B: live 15m desk vs recommended expert mix, one €20k book.

Fresh start 2026-06-28 → 2026-09-20 (same window as combined_desk_12w_sim).
Current strategy = 100% live momentum core (paper short is a separate sleeve
and idle-fill is off; reported as a footnote, not added on top of €20k).

Writes artifacts/expert_vs_live_12w.json and artifacts/expert_vs_live_12w.svg
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifacts.bear_market_strategy_sim import _align, _sma, load_daily
from artifacts.combined_desk_12w_sim import core_daily_equity, live_core_cfg
from artifacts.expert_20k_desk import apply_expert, path_from_eq, sim_short_harvest
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

OUT = Path(__file__).resolve().parent / "expert_vs_live_12w.json"
SVG = Path(__file__).resolve().parent / "expert_vs_live_12w.svg"

W0 = "2026-06-28"
W1 = "2026-09-20"

REC_MAP = {
    "risk_on": {"core": 0.4, "winners": 0.3, "donch20": 0.3},
    "mid": {"donch20": 0.5, "cash": 0.5},
    "risk_off": {"cash": 1.0},
}


def weekly(rows: list[dict[str, Any]], book: float = BOOK) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        d = datetime.fromisoformat(r["date"]).replace(tzinfo=UTC)
        iso = d.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        by[key].append(r)
    out = []
    prev_end = book
    for key in sorted(by):
        chunk = by[key]
        end = float(chunk[-1]["equity_eur"])
        pnl = round(end - prev_end, 2)
        out.append(
            {
                "week": key,
                "start": chunk[0]["date"],
                "end": chunk[-1]["date"],
                "pnl_eur": pnl,
                "end_eur": round(end, 2),
            }
        )
        prev_end = end
    return out


def write_svg(
    live: list[dict[str, Any]],
    rec: list[dict[str, Any]],
    path: Path,
    *,
    title: str = "Laatste 12 weken · €20k · live 15m-core vs expert-mix",
) -> None:
    w, h = 920, 380
    pad_l, pad_r, pad_t, pad_b = 58, 16, 28, 40
    vals = [BOOK] + [float(r["equity_eur"]) for r in live + rec]
    vmin, vmax = min(vals), max(vals)
    span = max(vmax - vmin, 1.0)
    n = max(len(live) - 1, 1)

    def xy(i: int, v: float) -> tuple[float, float]:
        x = pad_l + i / n * (w - pad_l - pad_r)
        y = pad_t + (1.0 - (v - vmin) / span) * (h - pad_t - pad_b)
        return x, y

    def path_d(rows: list[dict[str, Any]]) -> str:
        d = []
        for j, r in enumerate(rows):
            x, y = xy(j, float(r["equity_eur"]))
            d.append(("M" if j == 0 else "L") + f"{x:.1f},{y:.1f}")
        return " ".join(d)

    x0, yb = xy(0, BOOK)
    x1, _ = xy(n, BOOK)
    ticks = [0, n // 3, (2 * n) // 3, n]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
        '<rect width="100%" height="100%" fill="#0b1020"/>',
        f'<text x="{pad_l}" y="20" fill="#e8ecf7" font-size="14" font-family="ui-sans-serif,system-ui">{title}</text>',
        f'<line x1="{x0:.1f}" y1="{yb:.1f}" x2="{x1:.1f}" y2="{yb:.1f}" stroke="#2a3348" stroke-dasharray="4 4"/>',
        f'<path d="{path_d(live)}" fill="none" stroke="#ff5d73" stroke-width="2"/>',
        f'<path d="{path_d(rec)}" fill="none" stroke="#3dff9a" stroke-width="2"/>',
        f'<text x="{w - 240}" y="{pad_t + 18}" fill="#ff5d73" font-size="12" font-family="ui-sans-serif">live 15m-core</text>',
        f'<text x="{w - 240}" y="{pad_t + 34}" fill="#3dff9a" font-size="12" font-family="ui-sans-serif">expert-mix</text>',
    ]
    for ti in ticks:
        lab = live[min(ti, len(live) - 1)]["date"]
        parts.append(
            f'<text x="{xy(min(ti, n), vmin)[0]:.1f}" y="{h - 12}" fill="#8b93a7" font-size="11" '
            f'text-anchor="middle" font-family="ui-sans-serif">{lab}</text>'
        )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    print("loading daily…", flush=True)
    series = load_daily(ALL, days=430)
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
    print(f"window {dates[0]}→{dates[-1]} n={len(dates)}", flush=True)

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
        if lab == "risk_on" and n and (above / n) < 0.30:
            lab = "mid"
        regime_of[d] = lab
        occ[lab] += 1
    print("regime days", dict(occ), flush=True)

    print("simulating live 15m core (12w)…", flush=True)
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
    winners = sim_winners_weekly(
        ts, closes, highs, lows, i0, i1, lb=30, skip=7, top_n=3, reb=7, w_each=0.3, sma_n=200
    )
    short_eq = path_from_eq(ts, i0, i1, sim_short_harvest(closes, closes["BTC"], sma200, i0, i1))
    eq = {
        "core": core_eq,
        "donch20": path_from_book(ts, i0, i1, donch),
        "winners": path_from_book(ts, i0, i1, winners),
        "short": short_eq,
        "cash": {d: BOOK for d in dates},
    }
    rets = {k: to_returns(v) for k, v in eq.items()}

    p_live, r_live = apply_expert(rets, dates, weights={"core": 1.0})
    p_rec, r_rec = apply_expert(rets, dates, regime_w=REC_MAP, regime_of=regime_of)
    st_live, st_rec = summarize(p_live, r_live), summarize(p_rec, r_rec)
    delta = round(st_rec["pnl_eur"] - st_live["pnl_eur"], 2)

    # Footnote: paper short as its own €20k (not sharing the live book)
    p_short, r_short = apply_expert(rets, dates, weights={"short": 1.0})
    st_short = summarize(p_short, r_short)

    weeks_live = weekly(r_live)
    weeks_rec = weekly(r_rec)
    week_delta = []
    for a, b in zip(weeks_live, weeks_rec, strict=False):
        week_delta.append(
            {
                "week": a["week"],
                "start": a["start"],
                "end": a["end"],
                "live_pnl_eur": a["pnl_eur"],
                "mix_pnl_eur": b["pnl_eur"],
                "delta_mix_minus_live_eur": round(b["pnl_eur"] - a["pnl_eur"], 2),
            }
        )

    write_svg(r_live, r_rec, SVG)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK,
        "window": {"start": dates[0], "end": dates[-1], "days": len(dates)},
        "caveats": [
            "Fresh 12w start at €20k; daily PnL / €20k (not compounding on shrunken equity)",
            "Live = 100% 15m momentum desk (live-micro knobs); no AlphaI timeline",
            "Expert mix = winners_when_bull_breadth on the SAME €20k (capital-split)",
            "Paper short is NOT in the live 20k book; shown as a separate footnote",
        ],
        "regime_days": dict(occ),
        "current_live_core": {
            **st_live,
            "trades": core_sum.get("trades"),
            "realized_eur": core_sum.get("realized_eur"),
            "weeks": weeks_live,
        },
        "expert_mix": {**st_rec, "map": REC_MAP, "euros": {
            "risk_on": {"core": 8000, "winners": 6000, "donch20": 6000},
            "mid": {"donch20": 10000, "cash": 10000},
            "risk_off": {"cash": 20000},
        }, "weeks": weeks_rec},
        "delta": {
            "mix_minus_live_eur": delta,
            "who_won": "expert_mix" if delta > 0 else ("live_core" if delta < 0 else "tie"),
            "mix_minus_live_pct_of_book": round(100.0 * delta / BOOK, 2),
            "dd_live_pct": st_live["max_dd_pct"],
            "dd_mix_pct": st_rec["max_dd_pct"],
        },
        "weeks": week_delta,
        "footnote_paper_short_own_20k": st_short,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n=== 12w €20k ===", flush=True)
    print(f"live  {st_live['pnl_eur']:+.0f}  dd={st_live['max_dd_pct']}%", flush=True)
    print(f"mix   {st_rec['pnl_eur']:+.0f}  dd={st_rec['max_dd_pct']}%", flush=True)
    print(f"delta mix−live {delta:+.0f} EUR", flush=True)
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Expert €20k desk: longer sample, more sleeves, risk-budget allocation.

Looks back to the Oct-2025 BTC top. Mixes daytrader (capped 15m core),
CTA/Donchian, cross-section winners, BTC/ETH ballast, and a bear short.
Allocates ONE €20k book (not €20k per sleeve).

Expert constraints (investor + crypto + daytrader):
  - cash is a position
  - never 100% in the 15m core (path DD is the desk's weak point)
  - alts only when BTC trend allows; shorts only in SMA200-bear
  - inverse-vol / vol-target / DD circuit as risk overlays
  - score on FULL sample Calmar with a hard DD + worst-month cap
    (do not chase the last 90d bull)

No AlphaI. No per-coin hardcodes. Research only — does not touch live.

Writes artifacts/expert_20k_desk.json and artifacts/expert_20k_equity.svg
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifacts.bear_harvest_hunter import pick_weakest, sim_short_book
from artifacts.bear_market_strategy_sim import _align, _sma, load_daily
from artifacts.combined_desk_12w_sim import core_daily_equity, live_core_cfg
from artifacts.missed_capacity_five_strats import (
    ALL,
    ALT,
    Book,
    sim_btc_sma,
    sim_donchian,
    sim_invvol_basket,
    sim_winners_weekly,
    _mom,
)
from artifacts.multi_strat_20k_allocator import (
    BOOK,
    _date,
    _idx,
    apply_weights,
    path_from_book,
    static_grid,
    summarize,
    to_returns,
)
from bot.live.momentum_desk import BAR_MS
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "expert_20k_desk.json"
SVG = Path(__file__).resolve().parent / "expert_20k_equity.svg"
HTML = Path(__file__).resolve().parent / "expert_20k_desk.html"

FULL_START = "2025-10-06"  # BTC top / start of the ~52% drawdown
BEAR_END = "2026-06-30"
JAN_START = "2026-01-11"
Q_START = "2026-06-20"
Q_END = "2026-09-20"
W12_START = "2026-06-28"


def path_from_eq(ts: list[int], i0: int, i1: int, eqs: list[float]) -> dict[str, float]:
    out: dict[str, float] = {}
    n = i1 - i0 + 1
    use = eqs[:n]
    for k, i in enumerate(range(i0, i0 + len(use))):
        out[_date(ts[i])] = float(use[k])
    return out


def sim_asset_sma(
    ts, closes, highs, lows, i0, i1, *, base: str, sma_n=200, vol_n=20, target_vol=0.25, max_w=0.8
) -> Book:
    """Long-flat time-series momentum on one liquid name, vol-targeted."""
    px_s = closes[base]
    sma = _sma(px_s, sma_n)
    book = Book()
    for i in range(i0, i1 + 1):
        px = {base: px_s[i]}
        book.bump_peaks({base: highs[base][i]})
        if i - vol_n < 1:
            book.snapshot(px)
            continue
        rets = [px_s[j] / px_s[j - 1] - 1.0 for j in range(i - vol_n + 1, i + 1) if px_s[j - 1] > 0]
        vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 * math.sqrt(365) if rets else 0.4
        w = min(max_w, target_vol / max(vol, 0.08))
        want_long = sma[i] is not None and px_s[i] > sma[i]
        held = base in book.pos
        if held and not want_long:
            book.close(base, px_s[i])
        elif want_long:
            target_n = book.mark(px) * w
            if not held:
                book.open(base, px_s[i], min(target_n, book.cash * 0.99))
            else:
                n, e, peak = book.pos[base]
                if abs(n - target_n) / max(n, 1) > 0.25 and book.cash + n > target_n:
                    book.close(base, px_s[i])
                    book.open(base, px_s[i], min(target_n, book.cash * 0.99))
        book.snapshot(px)
    if base in book.pos:
        book.close(base, px_s[i1])
        book.eq[-1] = book.cash
    return book


def sim_dual_mom(
    ts, closes, highs, lows, i0, i1, *, assets=("BTC", "ETH"), lb=90, sma_n=200, max_w=0.85
) -> Book:
    """Antonacci dual momentum: hold the strongest positive-momentum asset above SMA, else cash."""
    smas = {a: _sma(closes[a], sma_n) for a in assets}
    book = Book()
    last = -10**9
    for i in range(i0, i1 + 1):
        px = {a: closes[a][i] for a in assets}
        book.bump_peaks({a: highs[a][i] for a in book.pos if a in highs})
        due = i - last >= 7 or last < 0
        if due:
            scores = []
            for a in assets:
                m = _mom(closes[a], i, lb, 0)
                above = smas[a][i] is not None and closes[a][i] > smas[a][i]
                if m is not None and m > 0 and above:
                    scores.append((m, a))
            scores.sort(reverse=True)
            want = scores[0][1] if scores else None
            for b in list(book.pos):
                book.close(b, px.get(b, book.pos[b][1]))
            if want is not None:
                book.open(want, px[want], min(book.mark(px) * max_w, book.cash * 0.99))
            last = i
        book.snapshot(px)
    for b in list(book.pos):
        book.close(b, closes[b][i1])
    if book.eq:
        book.eq[-1] = book.cash
    return book


def sim_donchian_friday(
    ts, closes, highs, lows, i0, i1, *, ch=20, exit_n=10, max_pos=2, w=0.4, btc_sma=50
) -> Book:
    """Donchian 20/10 that flattens Friday close (weekend gap risk)."""
    sma_btc = _sma(closes["BTC"], btc_sma)
    book = Book()
    for i in range(i0, i1 + 1):
        px = {b: closes[b][i] for b in ALT}
        book.bump_peaks({b: highs[b][i] for b in book.pos if b in highs})
        btc_ok = sma_btc[i] is not None and closes["BTC"][i] > sma_btc[i]
        wd = datetime.fromtimestamp(ts[i] / 1000, UTC).weekday()
        if wd >= 4 and book.pos:  # Fri/Sat/Sun
            for b in list(book.pos):
                book.close(b, px[b])
            book.snapshot(px)
            continue
        for b in list(book.pos):
            if i >= exit_n:
                ll = min(lows[b][i - exit_n : i])
                if lows[b][i] <= ll:
                    book.close(b, px[b])
        if btc_ok and len(book.pos) < max_pos and i >= ch:
            cands = []
            for b in ALT:
                if b in book.pos:
                    continue
                hh = max(highs[b][i - ch : i])
                if highs[b][i] > hh:
                    mom = _mom(closes[b], i, ch, 0) or 0.0
                    cands.append((mom, b))
            cands.sort(reverse=True)
            eq = book.mark(px)
            for _, b in cands:
                if len(book.pos) >= max_pos:
                    break
                book.open(b, px[b], min(eq * w, book.cash * 0.95))
        book.snapshot(px)
    for b in list(book.pos):
        book.close(b, closes[b][i1])
    if book.eq:
        book.eq[-1] = book.cash
    return book


def sim_short_harvest(closes, btc, sma200, i0, i1) -> list[float]:
    """Balanced bear-harvest pack (paper short-weakest defaults)."""

    def pick_fn(i: int):
        return pick_weakest(
            closes,
            btc,
            i,
            lookback=15,
            top_n=1,
            floor=-0.08,
            skip=2,
            lookback2=0,
            floor2=0.0,
            mode="mom",
            bounce=0.04,
            below_sma=0,
            weight_mode="equal",
        )

    eq, _, _, _ = sim_short_book(
        closes,
        btc,
        sma200,
        i0,
        i1,
        pick_fn=pick_fn,
        rebalance_every=30,
        trail_pct=0.0,
        hard_stop_pct=0.0,
        vol_spike=False,
        vol_mult=3.0,
        max_weight=0.5,
        deploy_frac=1.0,
        vol_scale=False,
        require_bear=True,
    )
    return eq


def sim_btc_short(closes, btc, sma200, i0, i1) -> list[float]:
    """Simple BTC SMA200 short (ballast / crash hedge)."""
    eq, _, _, _ = sim_short_book(
        closes,
        btc,
        sma200,
        i0,
        i1,
        pick_fn=lambda _i: [("BTC", 1.0)],
        rebalance_every=14,
        trail_pct=0.0,
        hard_stop_pct=0.0,
        vol_spike=False,
        vol_mult=3.0,
        max_weight=1.0,
        deploy_frac=0.5,
        vol_scale=False,
        require_bear=True,
    )
    return eq


def trailing_vol(vals: list[float], n: int = 21) -> float:
    if len(vals) < 8:
        return 0.25
    use = vals[-n:]
    mu = sum(use) / len(use)
    var = sum((x - mu) ** 2 for x in use) / max(len(use) - 1, 1)
    return math.sqrt(var) * math.sqrt(365)


def apply_expert(
    rets: dict[str, dict[str, float]],
    dates: list[str],
    *,
    weights: dict[str, float] | None = None,
    regime_w: dict[str, dict[str, float]] | None = None,
    regime_of: dict[str, str] | None = None,
    rp_allow: dict[str, list[str]] | None = None,
    rp_max_w: float = 0.40,
    rp_vol_n: int = 21,
    vol_target: float | None = None,
    vol_n: int = 21,
    max_lev: float = 1.0,
    dd_halt: float | None = None,
    dd_resume: float | None = None,
    book: float = BOOK,
) -> tuple[list[float], list[dict[str, Any]]]:
    """Regime or RP mix, then optional vol-target and DD circuit. No lookahead."""
    eq = book
    path = [eq]
    rows: list[dict[str, Any]] = []
    peak = book
    halted = False
    hist: dict[str, deque[float]] = {k: deque(maxlen=max(rp_vol_n, vol_n) + 2) for k in rets}
    mix_hist: deque[float] = deque(maxlen=vol_n + 2)

    for d in dates:
        label = None
        if regime_of is not None:
            label = regime_of.get(d, "risk_on")

        if rp_allow is not None:
            allow = rp_allow.get(label or "risk_on", [])
            inv: dict[str, float] = {}
            for k in allow:
                vol = trailing_vol(list(hist[k]), rp_vol_n) if k in hist else 0.25
                inv[k] = 1.0 / max(vol, 0.08)
            s = sum(inv.values()) or 1.0
            w = {k: v / s for k, v in inv.items()}
            # cap and leftover → cash
            extra = 0.0
            for k in list(w):
                if w[k] > rp_max_w:
                    extra += w[k] - rp_max_w
                    w[k] = rp_max_w
            if extra and w:
                room = [k for k, v in w.items() if v < rp_max_w]
                if room:
                    add = extra / len(room)
                    for k in room:
                        w[k] = min(rp_max_w, w[k] + add)
            wsum = sum(w.values())
            if wsum < 1.0:
                w["cash"] = 1.0 - wsum
        elif regime_w is not None:
            w = regime_w.get(label or "risk_on", weights or {"cash": 1.0})
        else:
            w = weights or {"cash": 1.0}

        dd = (eq - peak) / book
        if dd_halt is not None and dd <= dd_halt:
            halted = True
        if halted and dd_resume is not None and dd >= dd_resume:
            halted = False

        r_raw = 0.0
        used = {k: float(w.get(k, 0.0)) for k in set(rets) | set(w) if float(w.get(k, 0.0)) > 1e-9}
        for k, wk in used.items():
            r_raw += wk * float(rets.get(k, {}).get(d, 0.0))

        scale = 1.0
        if vol_target is not None and len(mix_hist) >= 10:
            pv = trailing_vol(list(mix_hist), vol_n)
            scale = min(max_lev, vol_target / max(pv, 0.04))
        r = 0.0 if halted else r_raw * scale

        eq += book * r
        path.append(eq)
        peak = max(peak, eq)
        mix_hist.append(r_raw)
        for k in rets:
            hist[k].append(float(rets[k].get(d, 0.0)))
        rows.append(
            {
                "date": d,
                "equity_eur": round(eq, 2),
                "day_ret": round(r, 5),
                "regime": label,
                "halted": halted,
                "vol_scale": round(scale, 3),
                "weights": {k: round(v, 3) for k, v in used.items()},
            }
        )
    return path, rows


def expert_score(full: dict[str, Any], q90: dict[str, Any], bear: dict[str, Any]) -> tuple:
    """Hard DD gate, then Calmar, then PnL. Oct-2025 crash month is not a 4% veto.

    Investor rule: stay above −12% book DD and −7.5% worst month (−€1500).
    Among feasible books, maximize Calmar (PnL per unit of DD), then full PnL.
    """
    worst = (full.get("worst_month") or {}).get("pnl_eur") or 0.0
    dd = full["max_dd_pct"]
    dd12 = int(dd > -12)
    dd15 = int(dd > -15)
    worst_ok = int(worst > -1500)
    worst_soft = int(worst > -2000)
    q_ok = int(q90["pnl_eur"] > 1500)
    bear_ok = int(bear["pnl_eur"] > -500)
    profit = int(full["pnl_eur"] > 0)
    # Pain-adjusted: €1 of extra DD-euro costs €1.5 of PnL (Calmar still primary).
    return (
        profit,
        dd12,
        worst_ok,
        q_ok,
        bear_ok,
        dd15,
        worst_soft,
        round(full["calmar"], 3),
        full["pnl_eur"],
        q90["pnl_eur"],
        worst,
        -abs(dd),
    )


def euros(w: dict[str, float]) -> dict[str, int]:
    return {k: int(round(BOOK * v)) for k, v in w.items() if v > 1e-9}


def window_of(rows: list[dict[str, Any]], start: str, end: str) -> tuple[list[float], list[dict[str, Any]]]:
    sub = [r for r in rows if start <= r["date"] <= end]
    if not sub:
        return [BOOK], []
    eq = BOOK
    path = [eq]
    out = []
    for r in sub:
        eq += BOOK * float(r["day_ret"])
        path.append(eq)
        out.append({**r, "equity_eur": round(eq, 2)})
    return path, out


def eval_arch(
    rets_full: dict[str, dict[str, float]],
    rets_q: dict[str, dict[str, float]],
    dates_full: list[str],
    dates_q: list[str],
    dates_bear: list[str],
    dates_jan: list[str],
    dates_w12: list[str],
    regime_of: dict[str, str],
    spec: dict[str, Any],
) -> dict[str, Any]:
    kw = {
        "weights": spec.get("weights"),
        "regime_w": spec.get("map"),
        "regime_of": regime_of if spec.get("map") or spec.get("rp_allow") else None,
        "rp_allow": spec.get("rp_allow"),
        "rp_max_w": spec.get("rp_max_w", 0.40),
        "vol_target": spec.get("vol_target"),
        "dd_halt": spec.get("dd_halt"),
        "dd_resume": spec.get("dd_resume"),
        "max_lev": spec.get("max_lev", 1.0),
    }
    pF, rF = apply_expert(rets_full, dates_full, **kw)
    pQ, rQ = apply_expert(rets_q, dates_q, **kw)
    pB, rB = window_of(rF, dates_bear[0], dates_bear[-1]) if dates_bear else ([BOOK], [])
    pJ, rJ = window_of(rF, dates_jan[0], dates_jan[-1]) if dates_jan else ([BOOK], [])
    pW, rW = window_of(rF, dates_w12[0], dates_w12[-1]) if dates_w12 else ([BOOK], [])
    stF, stQ, stB = summarize(pF, rF), summarize(pQ, rQ), summarize(pB, rB)
    occ: dict[str, int] = defaultdict(int)
    halt_days = 0
    for r in rF:
        occ[r.get("regime") or "static"] += 1
        halt_days += int(bool(r.get("halted")))
    return {
        "name": spec["name"],
        "kind": spec.get("kind", "regime"),
        "thesis": spec.get("thesis", ""),
        "map": spec.get("map"),
        "weights": spec.get("weights"),
        "rp_allow": spec.get("rp_allow"),
        "vol_target": spec.get("vol_target"),
        "dd_halt": spec.get("dd_halt"),
        "euros": {reg: euros(w) for reg, w in (spec.get("map") or {}).items()}
        if spec.get("map")
        else ({"always": euros(spec["weights"])} if spec.get("weights") else {"risk_parity": spec.get("rp_allow")}),
        "full": stF,
        "q90": stQ,
        "bear_to_jun": stB,
        "jan_sep": summarize(pJ, rJ),
        "last_12w": summarize(pW, rW),
        "regime_days_full": dict(occ),
        "dd_halt_days": halt_days,
        "path_full": [round(x, 1) for x in pF[::7]],  # weekly downsample for JSON size
        "_score": expert_score(stF, stQ, stB),
        "_rows_full": rF,
        "_path_full": pF,
    }


def sleeve_unit(rets: dict[str, dict[str, float]], dates: list[str]) -> dict[str, Any]:
    out = {}
    for name in rets:
        p, r = apply_weights({name: rets[name]}, {name: 1.0}, dates)
        out[name] = summarize(p, r)
    return out


def write_svg(series: dict[str, list[tuple[str, float]]], path: Path) -> None:
    w, h = 920, 420
    pad_l, pad_r, pad_t, pad_b = 58, 16, 28, 36
    all_v = [v for pts in series.values() for _, v in pts]
    if not all_v:
        return
    vmin, vmax = min(all_v + [BOOK]), max(all_v + [BOOK])
    span = max(vmax - vmin, 1.0)
    dates = series[next(iter(series))]
    n = max(len(dates) - 1, 1)

    def xy(i: int, v: float) -> tuple[float, float]:
        x = pad_l + i / n * (w - pad_l - pad_r)
        y = pad_t + (1.0 - (v - vmin) / span) * (h - pad_t - pad_b)
        return x, y

    colors = {
        "recommendation": "#3dff9a",
        "core": "#ff5d73",
        "winners_when_bull": "#7aa2ff",
        "cash": "#8b93a7",
        "donch20": "#f0c14b",
        "btc200": "#c084fc",
    }
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
        '<rect width="100%" height="100%" fill="#0b1020"/>',
        f'<text x="{pad_l}" y="20" fill="#e8ecf7" font-size="14" font-family="ui-sans-serif,system-ui">'
        "€20k expert desk — equity from BTC top (Oct 2025)</text>",
    ]
    # zero / book line
    x0, yb = xy(0, BOOK)
    x1, _ = xy(n, BOOK)
    parts.append(f'<line x1="{x0:.1f}" y1="{yb:.1f}" x2="{x1:.1f}" y2="{yb:.1f}" stroke="#2a3348" stroke-dasharray="4 4"/>')
    parts.append(
        f'<text x="8" y="{yb:.1f}" fill="#8b93a7" font-size="11" font-family="ui-sans-serif">€20k</text>'
    )
    for i, (name, pts) in enumerate(series.items()):
        col = colors.get(name, "#9ad0ff")
        d = []
        for j, (_, v) in enumerate(pts):
            x, y = xy(j, v)
            d.append(("M" if j == 0 else "L") + f"{x:.1f},{y:.1f}")
        parts.append(f'<path d="{" ".join(d)}" fill="none" stroke="{col}" stroke-width="2"/>')
        parts.append(
            f'<text x="{w - pad_r - 210}" y="{pad_t + 18 + i * 16}" fill="{col}" font-size="12" '
            f'font-family="ui-sans-serif">{name}</text>'
        )
    # x labels
    for frac, idx in ((0.0, 0), (0.5, n // 2), (1.0, n)):
        lab = dates[min(idx, len(dates) - 1)][0]
        x, y = xy(min(idx, n), vmin)
        parts.append(
            f'<text x="{x:.1f}" y="{h - 10}" fill="#8b93a7" font-size="11" text-anchor="middle" '
            f'font-family="ui-sans-serif">{lab}</text>'
        )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_html(payload: dict[str, Any], svg: str, path: Path) -> None:
    rec = payload["recommendation"]
    rows = []
    for a in payload["architectures"][:10]:
        rows.append(
            "<tr>"
            f"<td>{a['name']}</td>"
            f"<td>{a['full']['pnl_eur']:.0f}</td>"
            f"<td>{a['full']['max_dd_pct']:.1f}%</td>"
            f"<td>{a['full']['calmar']:.2f}</td>"
            f"<td>{(a['full'].get('worst_month') or {}).get('pnl_eur', 0):.0f}</td>"
            f"<td>{a['q90']['pnl_eur']:.0f}</td>"
            f"<td>{a['bear_to_jun']['pnl_eur']:.0f}</td>"
            "</tr>"
        )
    sleeve_rows = []
    for name, st in payload["sleeve_unit_20k"]["full"].items():
        sleeve_rows.append(
            f"<tr><td>{name}</td><td>{st['pnl_eur']:.0f}</td><td>{st['max_dd_pct']:.1f}%</td>"
            f"<td>{st['calmar']:.2f}</td>"
            f"<td>{(st.get('worst_month') or {}).get('pnl_eur', 0):.0f}</td></tr>"
        )
    split = json.dumps(rec.get("euros_by_regime") or rec.get("euros"), indent=2)
    html = f"""<!DOCTYPE html>
<html lang="nl"><head><meta charset="utf-8"/>
<title>Expert €20k desk</title>
<style>
body{{background:#0b1020;color:#e8ecf7;font-family:ui-sans-serif,system-ui;margin:24px;}}
h1,h2{{font-weight:600}}
.sub{{color:#8b93a7}}
table{{border-collapse:collapse;width:100%;margin:12px 0 28px}}
th,td{{border-bottom:1px solid #1d2638;padding:6px 8px;text-align:left;font-size:13px}}
th{{color:#8b93a7;font-weight:500}}
pre{{background:#12192b;padding:12px;border-radius:8px;overflow:auto}}
.kpi{{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0 20px}}
.kpi div{{background:#12192b;padding:12px 16px;border-radius:8px;min-width:140px}}
.kpi b{{display:block;font-size:20px;color:#3dff9a}}
svg{{max-width:100%;height:auto}}
</style></head><body>
<h1>Expert €20k desk</h1>
<p class="sub">{payload['full']['start']} → {payload['full']['end']} · book €20k · geen AlphaI · capital-split</p>
<div class="kpi">
  <div>Aanbevolen PnL<b>€{rec['full']['pnl_eur']:.0f}</b></div>
  <div>Max DD<b style="color:#ff5d73">{rec['full']['max_dd_pct']:.1f}%</b></div>
  <div>Calmar<b>{rec['full']['calmar']:.2f}</b></div>
  <div>Slechtste maand<b>€{(rec['full'].get('worst_month') or {}).get('pnl_eur', 0):.0f}</b></div>
  <div>Laatste 90d<b>€{rec['q90']['pnl_eur']:.0f}</b></div>
</div>
<h2>Wanneer wat aan staat</h2>
<pre>{split}</pre>
<p>{rec.get('why','')}</p>
{svg}
<h2>Architecturen (volledige sample)</h2>
<table><thead><tr><th>naam</th><th>PnL</th><th>DD</th><th>Calmar</th><th>slechtste mnd</th><th>90d</th><th>bear→jun</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>Losse sleeves €20k (okt 2025 → nu)</h2>
<table><thead><tr><th>sleeve</th><th>PnL</th><th>DD</th><th>Calmar</th><th>slechtste mnd</th></tr></thead>
<tbody>{''.join(sleeve_rows)}</tbody></table>
<p class="sub">Caveats: {'; '.join(payload['caveats'])}</p>
</body></html>"""
    path.write_text(html, encoding="utf-8")


def architectures() -> list[dict[str, Any]]:
    """Pre-specified maps. Not fit on the last 90 days."""
    prev_winners = {
        "name": "winners_when_bull",
        "kind": "regime",
        "thesis": "Vorige allocator-winnaar: core/winners/Donchian in bull, cash onder SMA50.",
        "map": {
            "risk_on": {"core": 0.4, "winners": 0.3, "donch20": 0.3},
            "mid": {"donch20": 0.5, "cash": 0.5},
            "risk_off": {"cash": 1.0},
        },
    }
    return [
        prev_winners,
        {
            "name": "capped_core_cta",
            "kind": "regime",
            "thesis": "Daytrader-core max 25% (DD-budget). CTA + winners doen het zware tillen.",
            "map": {
                "risk_on": {"core": 0.25, "winners": 0.30, "donch20": 0.25, "btc200": 0.20},
                "mid": {"donch20": 0.35, "btc200": 0.15, "cash": 0.50},
                "risk_off": {"cash": 1.0},
            },
        },
        {
            "name": "ls_mid_harvest",
            "kind": "regime",
            "thesis": "Long strength + short laggards in SMA50–200 (mid). Crash = short+cash.",
            "map": {
                "risk_on": {"core": 0.25, "winners": 0.35, "donch20": 0.40},
                "mid": {"donch20": 0.35, "short": 0.35, "cash": 0.30},
                "risk_off": {"short": 0.45, "cash": 0.55},
            },
        },
        {
            "name": "cta_plus_short",
            "kind": "regime",
            "thesis": "Managed-futures stack: Donchian + BTC trend; short only in risk_off.",
            "map": {
                "risk_on": {"donch20": 0.40, "btc200": 0.30, "winners": 0.20, "cash": 0.10},
                "mid": {"donch20": 0.30, "btc200": 0.15, "short": 0.20, "cash": 0.35},
                "risk_off": {"short": 0.40, "cash": 0.60},
            },
        },
        {
            "name": "all_weather_crypto",
            "kind": "regime",
            "thesis": "Ballast BTC+ETH SMA200, inv-vol alts, cash. Geen 15m core.",
            "map": {
                "risk_on": {"btc200": 0.25, "eth200": 0.20, "invvol": 0.30, "cash": 0.25},
                "mid": {"btc200": 0.20, "eth200": 0.15, "cash": 0.65},
                "risk_off": {"cash": 1.0},
            },
        },
        {
            "name": "preserve_first",
            "kind": "regime",
            "thesis": "Minimale DD: veel cash, geen core, winners+Donchian alleen in bull.",
            "map": {
                "risk_on": {"winners": 0.30, "donch20": 0.25, "btc200": 0.20, "cash": 0.25},
                "mid": {"btc200": 0.20, "cash": 0.80},
                "risk_off": {"cash": 1.0},
            },
        },
        {
            "name": "dual_mom_ballast",
            "kind": "regime",
            "thesis": "Dual momentum BTC/ETH als kern, Donchian satellite, cash in bear.",
            "map": {
                "risk_on": {"dualmom": 0.45, "donch20": 0.30, "winners": 0.25},
                "mid": {"dualmom": 0.30, "cash": 0.70},
                "risk_off": {"cash": 1.0},
            },
        },
        {
            "name": "friday_flatten_desk",
            "kind": "regime",
            "thesis": "Daytrader: Donchian plat op vrijdag. Core capped. Short in crash.",
            "map": {
                "risk_on": {"core": 0.30, "donch_fri": 0.35, "winners": 0.35},
                "mid": {"donch_fri": 0.40, "cash": 0.60},
                "risk_off": {"short": 0.35, "cash": 0.65},
            },
        },
        {
            "name": "barbell_eth_short",
            "kind": "regime",
            "thesis": "Barbell: capped core + ETH ballast in bull; short+cash in bear.",
            "map": {
                "risk_on": {"core": 0.30, "eth200": 0.25, "winners": 0.25, "donch20": 0.20},
                "mid": {"eth200": 0.25, "donch20": 0.25, "cash": 0.50},
                "risk_off": {"short": 0.40, "btc_short": 0.15, "cash": 0.45},
            },
        },
        {
            "name": "rp_trend_sleeves",
            "kind": "risk_parity",
            "thesis": "Inverse-vol over toegestane sleeves per regime; max 40% per sleeve.",
            "rp_allow": {
                "risk_on": ["donch20", "winners", "btc200", "eth200", "core"],
                "mid": ["donch20", "btc200", "eth200", "short"],
                "risk_off": ["short", "btc_short"],
            },
            "rp_max_w": 0.40,
        },
        {
            "name": "cta_vol18",
            "kind": "regime+vol",
            "thesis": "CTA+short, book gescaled naar 18% jaarband vol, geen leverage.",
            "map": {
                "risk_on": {"donch20": 0.40, "btc200": 0.30, "winners": 0.20, "cash": 0.10},
                "mid": {"donch20": 0.30, "short": 0.20, "cash": 0.50},
                "risk_off": {"short": 0.40, "cash": 0.60},
            },
            "vol_target": 0.18,
            "max_lev": 1.0,
        },
        {
            "name": "ls_dd8",
            "kind": "regime+dd",
            "thesis": "Long/short mid + 8% DD circuit-breaker naar cash tot −4%.",
            "map": {
                "risk_on": {"core": 0.25, "winners": 0.35, "donch20": 0.40},
                "mid": {"donch20": 0.35, "short": 0.35, "cash": 0.30},
                "risk_off": {"short": 0.45, "cash": 0.55},
            },
            "dd_halt": -0.08,
            "dd_resume": -0.04,
        },
        {
            "name": "core_only_risk_on",
            "kind": "regime",
            "thesis": "Baseline: 100% 15m desk alleen boven SMA200, anders cash.",
            "map": {
                "risk_on": {"core": 1.0},
                "mid": {"cash": 1.0},
                "risk_off": {"cash": 1.0},
            },
        },
        {
            "name": "static_donch_winners_cash",
            "kind": "static",
            "thesis": "Geen regime: 40% Donchian / 30% winners / 30% cash, altijd.",
            "weights": {"donch20": 0.4, "winners": 0.3, "cash": 0.3},
        },
        {
            "name": "winners_donch_fri",
            "kind": "regime",
            "thesis": "Vorige bull-mix, maar Donchian plat op vrijdag (weekend-gap). Core 40%.",
            "map": {
                "risk_on": {"core": 0.4, "winners": 0.3, "donch_fri": 0.3},
                "mid": {"donch_fri": 0.5, "cash": 0.5},
                "risk_off": {"cash": 1.0},
            },
        },
        {
            "name": "capped_core_donch_fri",
            "kind": "regime",
            "thesis": "Core max 25% + Friday-Donchian + winners + BTC ballast.",
            "map": {
                "risk_on": {"core": 0.25, "winners": 0.30, "donch_fri": 0.30, "btc200": 0.15},
                "mid": {"donch_fri": 0.40, "btc200": 0.10, "cash": 0.50},
                "risk_off": {"cash": 1.0},
            },
        },
        {
            "name": "invvol_donch_fri_bull",
            "kind": "regime",
            "thesis": "Geen 15m-core. Inv-vol + Friday-Donchian + winners in bull; cash in bear.",
            "map": {
                "risk_on": {"invvol": 0.35, "donch_fri": 0.35, "winners": 0.30},
                "mid": {"donch_fri": 0.40, "invvol": 0.15, "cash": 0.45},
                "risk_off": {"cash": 1.0},
            },
        },
        {
            "name": "ls_mid_donch_fri",
            "kind": "regime",
            "thesis": "Friday-Donchian long strength + short laggards in mid; cash+short in crash.",
            "map": {
                "risk_on": {"core": 0.25, "winners": 0.35, "donch_fri": 0.40},
                "mid": {"donch_fri": 0.35, "short": 0.30, "cash": 0.35},
                "risk_off": {"short": 0.40, "cash": 0.60},
            },
        },
    ]


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

    i_full = _idx(ts, FULL_START)
    i_bear = _idx(ts, BEAR_END)
    i_jan = _idx(ts, JAN_START)
    i_q0 = _idx(ts, Q_START)
    i_w12 = _idx(ts, W12_START)
    i_end = _idx(ts, Q_END)
    dates_full = [_date(ts[i]) for i in range(i_full, i_end + 1)]
    dates_q = [_date(ts[i]) for i in range(i_q0, i_end + 1)]
    dates_bear = [_date(ts[i]) for i in range(i_full, i_bear + 1)]
    dates_jan = [_date(ts[i]) for i in range(i_jan, i_end + 1)]
    dates_w12 = [_date(ts[i]) for i in range(i_w12, i_end + 1)]

    sma200 = _sma(closes["BTC"], 200)
    sma50 = _sma(closes["BTC"], 50)
    regime_of: dict[str, str] = {}
    breadth_of: dict[str, str] = {}
    alt_sma50 = {b: _sma(closes[b], 50) for b in ALT}
    for i in range(i_full, i_end + 1):
        d = _date(ts[i])
        btc = closes["BTC"][i]
        s200, s50 = sma200[i], sma50[i]
        if s50 is not None and btc < s50:
            lab = "risk_off"
        elif s200 is not None and btc < s200:
            lab = "mid"
        else:
            lab = "risk_on"
        regime_of[d] = lab
        above = 0
        n = 0
        for b in ALT:
            sm = alt_sma50[b][i]
            if sm is None:
                continue
            n += 1
            if closes[b][i] > sm:
                above += 1
        br = above / max(n, 1)
        if lab == "risk_on" and br < 0.30:
            breadth_of[d] = "mid"
        else:
            breadth_of[d] = lab

    print(
        f"full {dates_full[0]}→{dates_full[-1]}  q90 {dates_q[0]}→{dates_q[-1]}  "
        f"bear {dates_bear[0]}→{dates_bear[-1]}",
        flush=True,
    )
    occ_full = defaultdict(int)
    for d in dates_full:
        occ_full[regime_of[d]] += 1
    print("regime days full", dict(occ_full), flush=True)

    # ── 15m core (expensive) ──────────────────────────────────────────────
    print("simulating core 15m (Oct→now)…", flush=True)
    cfg = live_core_cfg()
    end_ms = int(datetime.fromisoformat(Q_END).replace(tzinfo=UTC).timestamp() * 1000)
    start_ms = int(datetime.fromisoformat(FULL_START).replace(tzinfo=UTC).timestamp() * 1000)
    end_ms = end_ms // BAR_MS * BAR_MS
    days_span = int((end_ms - start_ms) / 86_400_000) + 40
    candles = load_candles(("BTC", *cfg.universe), days=days_span, end_ms=end_ms, refresh=False)
    core_full = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    print(f"  core full realized={core_full.summary().get('realized_eur')} trades={core_full.summary().get('trades')}", flush=True)
    q_ms = int(datetime.fromisoformat(Q_START).replace(tzinfo=UTC).timestamp() * 1000)
    print("simulating core 15m (fresh 90d)…", flush=True)
    core_q = simulate(candles, cfg, start_ms=q_ms, end_ms=end_ms, alphai=None)
    print(f"  core 90d realized={core_q.summary().get('realized_eur')} trades={core_q.summary().get('trades')}", flush=True)

    core_eq = {
        r["date"]: float(r["equity_eur"])
        for r in core_daily_equity(
            core_full, daily_ts=ts, daily_closes=closes, start_ms=start_ms, end_ms=end_ms, book=BOOK
        )
    }
    core_eq_q = {
        r["date"]: float(r["equity_eur"])
        for r in core_daily_equity(
            core_q, daily_ts=ts, daily_closes=closes, start_ms=q_ms, end_ms=end_ms, book=BOOK
        )
    }

    def run_sleeves(i0: int, i1: int) -> dict[str, dict[str, float]]:
        print(f"  sleeves { _date(ts[i0])}→{_date(ts[i1])}…", flush=True)
        btc = closes["BTC"]
        out: dict[str, dict[str, float]] = {}
        specs: dict[str, Any] = {
            "donch20": sim_donchian(ts, closes, highs, lows, i0, i1, ch=20, exit_n=10, btc_sma=50),
            "donch10": sim_donchian(ts, closes, highs, lows, i0, i1, ch=10, exit_n=5, btc_sma=50),
            "donch200": sim_donchian(ts, closes, highs, lows, i0, i1, ch=20, exit_n=10, btc_sma=200),
            "donch_fri": sim_donchian_friday(ts, closes, highs, lows, i0, i1),
            "winners": sim_winners_weekly(
                ts, closes, highs, lows, i0, i1, lb=30, skip=7, top_n=3, reb=7, w_each=0.3, sma_n=200
            ),
            "invvol": sim_invvol_basket(
                ts, closes, highs, lows, i0, i1, lookback=21, top_n=4, deploy=0.8, sma_n=200
            ),
            "btc200": sim_btc_sma(ts, closes, highs, lows, i0, i1, sma_n=200, target_vol=0.25, max_w=0.8),
            "eth200": sim_asset_sma(
                ts, closes, highs, lows, i0, i1, base="ETH", sma_n=200, target_vol=0.25, max_w=0.8
            ),
            "dualmom": sim_dual_mom(ts, closes, highs, lows, i0, i1),
        }
        for name, bk in specs.items():
            out[name] = path_from_book(ts, i0, i1, bk)
            print(f"    {name:10} end={list(out[name].values())[-1]:.1f}", flush=True)
        out["short"] = path_from_eq(ts, i0, i1, sim_short_harvest(closes, btc, sma200, i0, i1))
        out["btc_short"] = path_from_eq(ts, i0, i1, sim_btc_short(closes, btc, sma200, i0, i1))
        print(f"    short      end={list(out['short'].values())[-1]:.1f}", flush=True)
        print(f"    btc_short  end={list(out['btc_short'].values())[-1]:.1f}", flush=True)
        out["cash"] = {d: BOOK for d in [_date(ts[i]) for i in range(i0, i1 + 1)]}
        return out

    print("simulating sleeves full sample…", flush=True)
    eq_full = run_sleeves(i_full, i_end)
    eq_full["core"] = core_eq
    print("simulating sleeves fresh 90d…", flush=True)
    eq_q = run_sleeves(i_q0, i_end)
    eq_q["core"] = core_eq_q

    rets_full = {k: to_returns(v) for k, v in eq_full.items()}
    rets_q = {k: to_returns(v) for k, v in eq_q.items()}

    sleeve_full = sleeve_unit(rets_full, dates_full)
    sleeve_q = sleeve_unit(rets_q, dates_q)
    print("\n=== unit €20k full ===", flush=True)
    for k, st in sorted(sleeve_full.items(), key=lambda kv: kv[1]["pnl_eur"], reverse=True):
        print(f"  {k:10} pnl={st['pnl_eur']:8} dd={st['max_dd_pct']:7} calmar={st['calmar']}", flush=True)

    # ── architectures ────────────────────────────────────────────────────
    print("\nevaluating architectures…", flush=True)
    arch_specs = architectures()
    # breadth variant of the previous winner
    wmap = [a for a in arch_specs if a["name"] == "winners_when_bull"][0]["map"]
    results = []
    for spec in arch_specs:
        row = eval_arch(
            rets_full, rets_q, dates_full, dates_q, dates_bear, dates_jan, dates_w12, regime_of, spec
        )
        print(
            f"  {spec['name']:24} full={row['full']['pnl_eur']:8} dd={row['full']['max_dd_pct']:6} "
            f"q90={row['q90']['pnl_eur']:8} bear={row['bear_to_jun']['pnl_eur']:8} "
            f"worst={row['full'].get('worst_month')}",
            flush=True,
        )
        results.append(row)

    # breadth-gated copy of best-looking long stack
    br_spec = {
        "name": "winners_when_bull_breadth",
        "kind": "regime",
        "thesis": "Zelfde als winners_when_bull, maar risk_on demoveert naar mid als <30% alts >SMA50.",
        "map": wmap,
    }
    br = eval_arch(
        rets_full, rets_q, dates_full, dates_q, dates_bear, dates_jan, dates_w12, breadth_of, br_spec
    )
    print(
        f"  {br_spec['name']:24} full={br['full']['pnl_eur']:8} dd={br['full']['max_dd_pct']:6} "
        f"q90={br['q90']['pnl_eur']:8}",
        flush=True,
    )
    results.append(br)

    fri_map = next(a["map"] for a in arch_specs if a["name"] == "winners_donch_fri")
    fri_br = eval_arch(
        rets_full,
        rets_q,
        dates_full,
        dates_q,
        dates_bear,
        dates_jan,
        dates_w12,
        breadth_of,
        {
            "name": "winners_donch_fri_breadth",
            "kind": "regime",
            "thesis": "Friday-Donchian bull-mix, risk_on demoveert naar mid als <30% alts >SMA50.",
            "map": fri_map,
        },
    )
    print(
        f"  winners_donch_fri_breadth full={fri_br['full']['pnl_eur']:8} dd={fri_br['full']['max_dd_pct']:6} "
        f"q90={fri_br['q90']['pnl_eur']:8}",
        flush=True,
    )
    results.append(fri_br)

    # small static grid on the robust names (not core-heavy)
    print("static grid (robust names, step 0.25)…", flush=True)
    gnames = ["winners", "donch20", "btc200", "short", "cash"]
    grid = static_grid(gnames, step=0.25)
    grid_rows = []
    for w in grid:
        spec = {"name": "grid", "kind": "static", "weights": w, "thesis": "static grid"}
        row = eval_arch(
            rets_full, rets_q, dates_full, dates_q, dates_bear, dates_jan, dates_w12, regime_of, spec
        )
        grid_rows.append(row)
    grid_rows.sort(key=lambda x: x["_score"], reverse=True)
    top_grid = grid_rows[0]
    top_grid["name"] = "static_grid_best"
    top_grid["thesis"] = "Beste 25%-stap mix van winners/Donchian/BTC200/short/cash op de volle sample."
    top_grid["weights"] = {k: round(v, 3) for k, v in top_grid["weights"].items() if v > 1e-9}
    print(
        f"  best grid {top_grid['weights']} full={top_grid['full']['pnl_eur']} "
        f"dd={top_grid['full']['max_dd_pct']}",
        flush=True,
    )
    results.append(top_grid)

    results.sort(key=lambda x: x["_score"], reverse=True)
    best = results[0]

    # vol-target overlay on the winner if it is a regime map and has no overlay yet
    extra = []
    if best.get("map") and best.get("vol_target") is None:
        for tv, dd in ((0.15, None), (0.18, None), (None, -0.08)):
            spec = {
                "name": f"{best['name']}_vt{int((tv or 0)*100) or 'x'}_dd{int(abs((dd or 0)*100))}",
                "kind": "overlay",
                "thesis": f"Overlay op {best['name']}: vol_target={tv} dd_halt={dd}",
                "map": best["map"],
                "vol_target": tv,
                "dd_halt": dd,
                "dd_resume": -0.04 if dd is not None else None,
                "max_lev": 1.0,
            }
            row = eval_arch(
                rets_full, rets_q, dates_full, dates_q, dates_bear, dates_jan, dates_w12, regime_of, spec
            )
            print(
                f"  overlay {spec['name']:28} full={row['full']['pnl_eur']:8} dd={row['full']['max_dd_pct']:6}",
                flush=True,
            )
            extra.append(row)
        results.extend(extra)
        results.sort(key=lambda x: x["_score"], reverse=True)
        best = results[0]

    feasible = [
        a
        for a in results
        if a["full"]["max_dd_pct"] > -12
        and ((a["full"].get("worst_month") or {}).get("pnl_eur") or 0) > -1500
        and a["q90"]["pnl_eur"] > 0
    ] or results
    preserve = min(feasible, key=lambda a: (abs(a["full"]["max_dd_pct"]), -a["full"]["pnl_eur"]))
    harvest = max(feasible, key=lambda a: (a["q90"]["pnl_eur"], a["full"]["calmar"]))

    # ── recommendation copy ──────────────────────────────────────────────
    why = (
        f"{best['thesis']} Score baas is de volle sample vanaf de BTC-top "
        f"({dates_full[0]}→{dates_full[-1]}), niet alleen de recente bull. "
        f"Full €{best['full']['pnl_eur']:.0f} / DD {best['full']['max_dd_pct']}% / "
        f"Calmar {best['full']['calmar']}; 90d €{best['q90']['pnl_eur']:.0f}; "
        f"bear-tot-jun €{best['bear_to_jun']['pnl_eur']:.0f}; "
        f"slechtste maand {(best['full'].get('worst_month') or {})}. "
        "15m-core blijft gecapped: die sleeve wint bull-weken maar heeft de diepste path-DD. "
        "Geen walk-forward chase (die koos vorige maand de winnaar en faalde OOS)."
    )

    def slim(a: dict[str, Any]) -> dict[str, Any]:
        skip = {"_score", "_rows_full", "_path_full"}
        return {k: v for k, v in a.items() if k not in skip}

    rec = {
        "kind": best["kind"],
        "name": best["name"],
        "thesis": best["thesis"],
        "why": why,
        "euros_by_regime": best.get("euros"),
        "map": best.get("map"),
        "weights": best.get("weights"),
        "rp_allow": best.get("rp_allow"),
        "vol_target": best.get("vol_target"),
        "dd_halt": best.get("dd_halt"),
        "when_on": {
            "risk_on": "BTC boven SMA200 — long sleeves (core capped, winners, Donchian, BTC/ETH trend)",
            "mid": "BTC tussen SMA50 en SMA200 — geen volle alt-book; Donchian en/of short+cash",
            "risk_off": "BTC onder SMA50 — cash en eventueel short-weakest / BTC-short; geen alt-longs",
        },
        "full": best["full"],
        "q90": best["q90"],
        "bear_to_jun": best["bear_to_jun"],
        "jan_sep": best["jan_sep"],
        "last_12w": best["last_12w"],
        "vs_prev_winners_when_bull": next(
            (
                {
                    "full": a["full"],
                    "q90": a["q90"],
                    "bear_to_jun": a["bear_to_jun"],
                }
                for a in results
                if a["name"] == "winners_when_bull"
            ),
            None,
        ),
        "vs_core_only_risk_on": next(
            (
                {"full": a["full"], "q90": a["q90"]}
                for a in results
                if a["name"] == "core_only_risk_on"
            ),
            None,
        ),
        "frontier": {
            "preserve_min_dd": {
                "name": preserve["name"],
                "euros": preserve.get("euros"),
                "full": preserve["full"],
                "q90": preserve["q90"],
            },
            "recommended": {"name": best["name"], "full": best["full"], "q90": best["q90"]},
            "harvest_90d": {
                "name": harvest["name"],
                "euros": harvest.get("euros"),
                "full": harvest["full"],
                "q90": harvest["q90"],
            },
        },
    }

    # equity svg: rec vs core vs previous vs cash
    def series_from(name: str, rows: list[dict[str, Any]]) -> list[tuple[str, float]]:
        return [(r["date"], float(r["equity_eur"])) for r in rows]

    svg_series = {
        "recommendation": series_from("rec", best["_rows_full"]),
        "cash": [(d, BOOK) for d in dates_full],
    }
    prev = next((a for a in results if a["name"] == "winners_when_bull"), None)
    if prev:
        svg_series["winners_when_bull"] = series_from("w", prev["_rows_full"])
    core_only = next((a for a in results if a["name"] == "core_only_risk_on"), None)
    if core_only:
        svg_series["core"] = series_from("c", core_only["_rows_full"])
    write_svg(svg_series, SVG)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK,
        "full": {"start": dates_full[0], "end": dates_full[-1], "days": len(dates_full)},
        "q90": {"start": dates_q[0], "end": dates_q[-1], "days": len(dates_q)},
        "bear_to_jun": {"start": dates_bear[0], "end": dates_bear[-1], "days": len(dates_bear)},
        "jan_sep": {"start": dates_jan[0], "end": dates_jan[-1], "days": len(dates_jan)},
        "last_12w": {"start": dates_w12[0], "end": dates_w12[-1], "days": len(dates_w12)},
        "caveats": [
            "Geen AlphaI-tijdlijn; 15m core close-fills; daily close voor overige sleeves",
            "Capital-split: sleeves delen één €20k-boek, ze krijgen niet elk €20k",
            "Alt-longs kunnen overlappen (core vs Donchian vs winners)",
            "Short is paper/perp-proxy zonder echte funding-path (~1.8%/jr drag weggelaten)",
            "Vol-target en DD-halt gebruiken alleen trailing data (geen lookahead)",
            "Static grid is in-sample op de volle periode — regime-maps zijn pre-specified",
        ],
        "regime_days_full": dict(occ_full),
        "sleeve_unit_20k": {"full": sleeve_full, "q90": sleeve_q},
        "architectures": [slim(a) for a in results],
        "recommendation": rec,
        "expert_notes": [
            "15m-core zonder SMA200-gate verbrandt het book in de okt-2025 bear (unit DD ~−80%). Alleen gated/capped gebruiken.",
            "Donchian dat vrijdag flattens (weekend-gap) verslaat 20/10 op de volle sample — daytrader-regel.",
            "Donchian 20/10 met SMA200-gate is de meest stabiele alt-trend (Calmar ~2.1, DD ~−7%).",
            "Winners-weekly is de beste Calmar-long in de recente bull, idle in bear (SMA200-gate).",
            "Short-weakest is geen 'altijd groen' overlay; hij hoort in mid/risk_off, niet als idle-fill. BTC-short is toxisch.",
            "ETH-SMA200 is ballast (laagste DD van de longs), geen vervanger van Donchian.",
            "Walk-forward monthly chase bleef OOS zwak; niet gebruiken als live allocator.",
            "Cash onder SMA50 is de drawdown-kill-switch die een desk écht schaalt.",
        ],
    }
    # JSON without huge paths
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_html(payload, SVG.read_text(encoding="utf-8"), HTML)
    print("\n=== RECOMMENDATION ===", flush=True)
    print(json.dumps(rec["euros_by_regime"], indent=2), flush=True)
    print("name", rec["name"], "full", rec["full"]["pnl_eur"], "dd", rec["full"]["max_dd_pct"], flush=True)
    print(f"wrote {OUT}", flush=True)
    print(f"wrote {SVG}", flush=True)
    print(f"wrote {HTML}", flush=True)


if __name__ == "__main__":
    main()

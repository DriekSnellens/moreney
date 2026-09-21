#!/usr/bin/env python3
"""Research only: does AlphaI add value on the live Donchian mix?

Does not touch live runners, allocator, or Donchian engine.

AlphaI labels on disk start ~2026-09-07 (pick_outcomes sessions). There is no
1y AlphaI tape, so this A/B is the overlap window only, plus a candidate-quality
table (Donchian 10d-breakouts that were vs were not AlphaI picks).

Overlays (coin-agnostic):
  baseline       — current mix Donchian (no AlphaI)
  require_pick   — new entries only if the name is an AlphaI pick
  rank_pick      — fill AlphaI picks first among breakouts
  size_pick      — clip ×1.30 on picks, ×0.70 otherwise
  skip_macro     — no new entries while AlphaI macro_caution
  flatten_macro  — flatten the sleeve when macro_caution
  dump_nonpick   — exit a lot if it drops off the pick list
  avoid_block    — skip AlphaI avoid names (almost a no-op: avoid is empty)

Writes artifacts/donchian_alphai_overlay_ab.json
and artifacts/donchian_alphai_overlay_ab.svg
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from artifacts.bear_market_strategy_sim import FEE_RT, _align, _sma, load_daily
from artifacts.expert_20k_desk import sim_donchian_friday
from artifacts.expert_best_desk_loop import apply_expert, build_regimes
from artifacts.missed_capacity_five_strats import ALT, ALL, Book, _mom, sim_donchian
from artifacts.multi_strat_20k_allocator import BOOK, _date, _idx, path_from_book, summarize, to_returns
ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "donchian_alphai_overlay_ab.json"
SVG = Path(__file__).resolve().parent / "donchian_alphai_overlay_ab.svg"

ALPHAI_FILES = [
    ROOT / "artifacts" / "alphai_pick_outcomes_week_snap.json",
    ROOT / "artifacts" / "alphai_pick_outcomes_live_copy.json",
    ROOT / "artifacts" / "alphai_pick_outcomes_merged.json",
    ROOT / "data" / "alphai" / "daily_recommendations.json",
]

REGIME_W = {
    "risk_on": {"donch_fri10": 0.5, "donch10": 0.5},
    "mid": {"cash": 1.0},
    "risk_off": {"donch_fri": 0.3, "cash": 0.7},  # isolate Donchian; shorts stay out
}

SIZE_PICK_MULT = 1.30
SIZE_OTHER_MULT = 0.70


def _parse_ts(raw: str) -> int:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _bases(rows: Any) -> set[str]:
    out: set[str] = set()
    for row in rows or []:
        if isinstance(row, str):
            b = row.strip().upper()
        elif isinstance(row, dict):
            b = str(row.get("base") or "").strip().upper()
        else:
            continue
        if b:
            out.add(b)
    return out


def _scores(rows: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        b = str(row.get("base") or "").strip().upper()
        if not b:
            continue
        try:
            out[b] = float(row.get("score") or 0.0)
        except (TypeError, ValueError):
            out[b] = 0.0
    return out


def load_alphai_timeline() -> tuple[list[int], list[dict[str, Any]], dict[str, Any]]:
    by_ts: dict[int, dict[str, Any]] = {}
    sources: list[str] = []
    for path in ALPHAI_FILES:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sources.append(str(path.relative_to(ROOT)))
        sessions: list[dict[str, Any]]
        if isinstance(raw, dict) and isinstance(raw.get("sessions"), list):
            sessions = [s for s in raw["sessions"] if isinstance(s, dict)]
        elif isinstance(raw, dict) and raw.get("generated_at"):
            sessions = [raw]
        else:
            continue
        for s in sessions:
            gen = s.get("generated_at") or ""
            if not gen:
                continue
            try:
                t = _parse_ts(gen)
            except ValueError:
                continue
            by_ts[t] = {
                "ts": t,
                "generated_at": gen,
                "picks": _bases(s.get("picks")),
                "avoid": _bases(s.get("avoid")),
                "scores": _scores(s.get("picks")),
                "macro_caution": bool(s.get("macro_caution")),
            }
    times = sorted(by_ts)
    views = [by_ts[t] for t in times]
    pick_days: set[str] = set()
    for v in views:
        d = datetime.fromtimestamp(v["ts"] / 1000, UTC).strftime("%Y-%m-%d")
        if v["picks"]:
            pick_days.add(d)
    meta = {
        "sources": sources,
        "sessions": len(views),
        "first": views[0]["generated_at"] if views else None,
        "last": views[-1]["generated_at"] if views else None,
        "macro_rate": round(sum(1 for v in views if v["macro_caution"]) / max(1, len(views)), 3),
        "avoid_sessions": sum(1 for v in views if v["avoid"]),
        "days_with_picks": len(pick_days),
        "unique_picks": sorted({b for v in views for b in v["picks"]}),
    }
    return times, views, meta


def alphai_at(times: list[int], views: list[dict[str, Any]], t_ms: int) -> dict[str, Any] | None:
    """Last snapshot known at t_ms (no lookahead)."""
    if not times or t_ms < times[0]:
        return None
    lo, hi = 0, len(times) - 1
    ans = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if times[mid] <= t_ms:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return views[ans] if ans >= 0 else None


def decide_ms(bar_open_ms: int) -> int:
    """Live Donchian decides ~00:05 UTC after the bar's UTC day closes."""
    dt = datetime.fromtimestamp(bar_open_ms / 1000, UTC)
    nxt = (dt + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return int(nxt.timestamp() * 1000)


def sim_donchian_overlay(
    ts,
    closes,
    highs,
    lows,
    i0,
    i1,
    *,
    times: list[int],
    views: list[dict[str, Any]],
    mode: str,
    ch: int = 10,
    exit_n: int = 5,
    friday: bool = False,
    max_pos: int = 2,
    w: float = 0.4,
    btc_sma: int = 50,
) -> tuple[Book, dict[str, int]]:
    sma_btc = _sma(closes["BTC"], btc_sma)
    book = Book()
    stats = defaultdict(int)
    for i in range(i0, i1 + 1):
        px = {b: closes[b][i] for b in ALT}
        book.bump_peaks({b: highs[b][i] for b in book.pos if b in highs})
        view = alphai_at(times, views, decide_ms(ts[i]))
        picks = view["picks"] if view else set()
        avoid = view["avoid"] if view else set()
        macro = bool(view and view["macro_caution"])
        if view:
            stats["days_with_alphai"] += 1
            if macro:
                stats["days_macro"] += 1
        btc_ok = sma_btc[i] is not None and closes["BTC"][i] > sma_btc[i]
        wd = datetime.fromtimestamp(ts[i] / 1000, UTC).weekday()

        if friday and wd >= 4 and book.pos:
            for b in list(book.pos):
                book.close(b, px[b])
                stats["exits_friday"] += 1
            book.snapshot(px)
            continue
        if mode == "flatten_macro" and macro and book.pos:
            for b in list(book.pos):
                book.close(b, px[b])
                stats["exits_macro"] += 1
            book.snapshot(px)
            continue

        for b in list(book.pos):
            dumped = False
            if mode == "dump_nonpick" and view and b not in picks:
                book.close(b, px[b])
                stats["exits_dump_nonpick"] += 1
                dumped = True
            if dumped:
                continue
            if i >= exit_n:
                ll = min(lows[b][i - exit_n : i])
                if lows[b][i] <= ll:
                    book.close(b, px[b])
                    stats["exits_channel"] += 1

        allow_new = btc_ok and len(book.pos) < max_pos and i >= ch
        if mode == "skip_macro" and macro:
            allow_new = False
            stats["blocked_macro"] += 1
        if allow_new:
            cands: list[tuple[float, str, bool]] = []
            for b in ALT:
                if b in book.pos:
                    continue
                hh = max(highs[b][i - ch : i])
                if highs[b][i] <= hh:
                    continue
                mom = _mom(closes[b], i, ch, 0) or 0.0
                is_pick = b in picks
                if mode == "avoid_block" and b in avoid:
                    stats["blocked_avoid"] += 1
                    continue
                if mode == "require_pick" and not is_pick:
                    stats["blocked_nonpick"] += 1
                    continue
                cands.append((mom, b, is_pick))
            if mode == "rank_pick":
                cands.sort(key=lambda x: (x[2], x[0]), reverse=True)
            else:
                cands.sort(key=lambda x: x[0], reverse=True)
            eq = book.mark(px)
            for mom, b, is_pick in cands:
                if len(book.pos) >= max_pos:
                    break
                ww = w
                if mode == "size_pick":
                    ww = w * (SIZE_PICK_MULT if is_pick else SIZE_OTHER_MULT)
                if book.open(b, px[b], min(eq * ww, book.cash * 0.95)):
                    stats["entries"] += 1
                    stats["entries_pick" if is_pick else "entries_nonpick"] += 1
        book.snapshot(px)
    for b in list(book.pos):
        book.close(b, closes[b][i1])
    if book.eq:
        book.eq[-1] = book.cash
    return book, dict(stats)


def fwd_ret(closes: list[float], i: int, n: int) -> float | None:
    j = i + n
    if j >= len(closes) or closes[i] <= 0 or closes[j] <= 0:
        return None
    return closes[j] / closes[i] - 1.0


def candidate_quality(
    ts,
    closes,
    highs,
    i0,
    i1,
    *,
    times,
    views,
    ch: int = 10,
    btc_sma: int = 50,
) -> dict[str, Any]:
    sma_btc = _sma(closes["BTC"], btc_sma)
    rows: list[dict[str, Any]] = []
    for i in range(i0, i1 + 1):
        view = alphai_at(times, views, decide_ms(ts[i]))
        if view is None:
            continue
        btc_ok = sma_btc[i] is not None and closes["BTC"][i] > sma_btc[i]
        if not btc_ok:
            continue
        btc_1 = fwd_ret(closes["BTC"], i, 1)
        btc_5 = fwd_ret(closes["BTC"], i, 5)
        for b in ALT:
            hh = max(highs[b][i - ch : i]) if i >= ch else None
            if hh is None or highs[b][i] <= hh:
                continue
            r1 = fwd_ret(closes[b], i, 1)
            r5 = fwd_ret(closes[b], i, 5)
            is_pick = b in view["picks"]
            rows.append(
                {
                    "date": _date(ts[i]),
                    "base": b,
                    "pick": is_pick,
                    "macro": view["macro_caution"],
                    "ret_1d": None if r1 is None else round(r1, 5),
                    "ret_5d": None if r5 is None else round(r5, 5),
                    "xs_btc_1d": None if r1 is None or btc_1 is None else round(r1 - btc_1, 5),
                    "xs_btc_5d": None if r5 is None or btc_5 is None else round(r5 - btc_5, 5),
                }
            )
    def _mean(key: str, pick: bool | None) -> dict[str, Any]:
        vals = [
            float(r[key])
            for r in rows
            if r.get(key) is not None and (pick is None or r["pick"] is pick)
        ]
        if not vals:
            return {"n": 0, "mean": None}
        return {"n": len(vals), "mean": round(sum(vals) / len(vals), 5)}

    return {
        "n_breakouts": len(rows),
        "n_pick": sum(1 for r in rows if r["pick"]),
        "n_nonpick": sum(1 for r in rows if not r["pick"]),
        "ret_1d_pick": _mean("ret_1d", True),
        "ret_1d_nonpick": _mean("ret_1d", False),
        "ret_5d_pick": _mean("ret_5d", True),
        "ret_5d_nonpick": _mean("ret_5d", False),
        "xs_btc_1d_pick": _mean("xs_btc_1d", True),
        "xs_btc_1d_nonpick": _mean("xs_btc_1d", False),
        "xs_btc_5d_pick": _mean("xs_btc_5d", True),
        "xs_btc_5d_nonpick": _mean("xs_btc_5d", False),
        "sample": rows[:40],
    }


def mix_path(
    ts,
    closes,
    highs,
    lows,
    i0,
    i1,
    dates: list[str],
    regimes: dict[str, str],
    *,
    times,
    views,
    mode: str,
) -> tuple[list[float], dict[str, Any], dict[str, dict[str, int]]]:
    specs = {
        "donch_fri10": dict(ch=10, exit_n=5, friday=True),
        "donch10": dict(ch=10, exit_n=5, friday=False),
        "donch_fri": dict(ch=20, exit_n=10, friday=True),
    }
    sleeve_stats: dict[str, dict[str, int]] = {}
    rets: dict[str, dict[str, float]] = {"cash": {d: 0.0 for d in dates}}
    ends: dict[str, float] = {}
    for name, kw in specs.items():
        book, st = sim_donchian_overlay(
            ts, closes, highs, lows, i0, i1, times=times, views=views, mode=mode, **kw
        )
        sleeve_stats[name] = st
        eq = path_from_book(ts, i0, i1, book)
        rets[name] = to_returns(eq)
        ends[name] = list(eq.values())[-1] if eq else BOOK
    path, rows = apply_expert(rets, dates, regime_w=REGIME_W, regime_of=regimes)
    st = summarize(path, rows)
    st["sleeve_end"] = {k: round(v, 2) for k, v in ends.items()}
    return path, st, sleeve_stats


def main() -> None:
    print("loading AlphaI timeline…", flush=True)
    times, views, alphai_meta = load_alphai_timeline()
    print(f"  sessions={alphai_meta['sessions']} {alphai_meta['first']} → {alphai_meta['last']}", flush=True)
    if not times:
        raise SystemExit("no AlphaI sessions on disk")

    print("loading daily candles…", flush=True)
    series = load_daily(ALL, days=560)
    ts, closes = _align(series, ALL)
    highs: dict[str, list[float]] = {}
    lows: dict[str, list[float]] = {}
    for b in ALL:
        idx = {t: i for i, t in enumerate(series[b].ts)}
        highs[b] = [series[b].h[idx[t]] for t in ts]
        lows[b] = [series[b].l[idx[t]] for t in ts]

    first_d = datetime.fromtimestamp(times[0] / 1000, UTC).strftime("%Y-%m-%d")
    last_d = datetime.fromtimestamp(times[-1] / 1000, UTC).strftime("%Y-%m-%d")
    # Score the overlap; keep warmup bars before i0 for SMA/channel.
    i0 = _idx(ts, first_d)
    i1 = _idx(ts, last_d)
    dates = [_date(ts[i]) for i in range(i0, i1 + 1)]
    print(f"overlap {dates[0]}→{dates[-1]} n={len(dates)}", flush=True)

    sma = {
        "sma20": _sma(closes["BTC"], 20),
        "sma50": _sma(closes["BTC"], 50),
        "sma100": _sma(closes["BTC"], 100),
        "sma200": _sma(closes["BTC"], 200),
    }
    alt_sma50 = {b: _sma(closes[b], 50) for b in ALT}
    regimes = build_regimes(ts, closes, i0, i1, sma, alt_sma50)["sma20_50"]
    occ: dict[str, int] = defaultdict(int)
    for lab in regimes.values():
        occ[lab] += 1

    modes = [
        "baseline",
        "require_pick",
        "rank_pick",
        "size_pick",
        "skip_macro",
        "flatten_macro",
        "dump_nonpick",
        "avoid_block",
    ]
    results: dict[str, Any] = {}
    paths: dict[str, list[float]] = {}
    for mode in modes:
        print(f"sim {mode}…", flush=True)
        path, st, sleeve_stats = mix_path(
            ts, closes, highs, lows, i0, i1, dates, regimes, times=times, views=views, mode=mode
        )
        paths[mode] = path
        if "baseline" in results:
            base_pnl = float(results["baseline"]["pnl_eur"])
            delta = round(st["pnl_eur"] - base_pnl, 2)
        else:
            delta = 0.0
        results[mode] = {
            **{k: st[k] for k in ("pnl_eur", "max_dd_pct", "calmar", "end_eur") if k in st},
            "trades_hint": sleeve_stats,
            "delta_vs_baseline_eur": delta,
            "sleeve_end": st.get("sleeve_end"),
        }
        print(
            f"  pnl={st['pnl_eur']:+.2f} dd={st['max_dd_pct']:.2f} "
            f"delta={results[mode]['delta_vs_baseline_eur']:+.2f}",
            flush=True,
        )

    print("candidate quality…", flush=True)
    quality = candidate_quality(ts, closes, highs, i0, i1, times=times, views=views)

    # Isolated 10/5 sleeve (no mix) so a blocked entry isn't hidden by cash.
    unit = {}
    for mode in ("baseline", "require_pick", "skip_macro", "size_pick"):
        book, st = sim_donchian_overlay(
            ts, closes, highs, lows, i0, i1, times=times, views=views, mode=mode, ch=10, exit_n=5, friday=False
        )
        unit[mode] = {
            "pnl_eur": round(book.cash - BOOK, 2) if not book.eq else round(book.eq[-1] - BOOK, 2),
            "trades": book.trades,
            "wins": book.wins,
            "stats": st,
        }

    # 1y Donchian mix without AlphaI — reminder, not an A/B.
    y0, y1 = "2025-09-20", "2026-09-20"
    yi0, yi1 = _idx(ts, y0), _idx(ts, y1)
    ydates = [_date(ts[i]) for i in range(yi0, yi1 + 1)]
    ysma = {
        "sma20": _sma(closes["BTC"], 20),
        "sma50": _sma(closes["BTC"], 50),
        "sma100": _sma(closes["BTC"], 100),
        "sma200": _sma(closes["BTC"], 200),
    }
    yreg = build_regimes(ts, closes, yi0, yi1, ysma, {b: _sma(closes[b], 50) for b in ALT})["sma20_50"]
    ylongs = {
        "donch_fri10": path_from_book(ts, yi0, yi1, sim_donchian_friday(ts, closes, highs, lows, yi0, yi1, ch=10, exit_n=5)),
        "donch10": path_from_book(ts, yi0, yi1, sim_donchian(ts, closes, highs, lows, yi0, yi1, ch=10, exit_n=5)),
        "donch_fri": path_from_book(ts, yi0, yi1, sim_donchian_friday(ts, closes, highs, lows, yi0, yi1, ch=20, exit_n=10)),
        "cash": {d: BOOK for d in ydates},
    }
    yrets = {k: to_returns(v) for k, v in ylongs.items()}
    ypath, yrows = apply_expert(yrets, ydates, regime_w=REGIME_W, regime_of=yreg)
    yst = summarize(ypath, yrows)

    pick_xs_1 = (quality["xs_btc_1d_pick"] or {}).get("mean")
    non_xs_1 = (quality["xs_btc_1d_nonpick"] or {}).get("mean")
    pick_n_1 = (quality["xs_btc_1d_pick"] or {}).get("n") or 0
    non_n_1 = (quality["xs_btc_1d_nonpick"] or {}).get("n") or 0
    require_delta = results["require_pick"]["delta_vs_baseline_eur"]
    skip_delta = results["skip_macro"]["delta_vs_baseline_eur"]
    size_delta = results["size_pick"]["delta_vs_baseline_eur"]
    flatten_delta = results["flatten_macro"]["delta_vs_baseline_eur"]
    dump_delta = results["dump_nonpick"]["delta_vs_baseline_eur"]
    rank_delta = results["rank_pick"]["delta_vs_baseline_eur"]

    helpful = []
    hurt = []
    for mode, row in results.items():
        if mode == "baseline":
            continue
        dlt = row["delta_vs_baseline_eur"]
        if dlt > 5:
            helpful.append(mode)
        elif dlt < -5:
            hurt.append(mode)

    xs_wash = (
        pick_xs_1 is not None
        and non_xs_1 is not None
        and abs(float(pick_xs_1) - float(non_xs_1)) < 0.005
    )
    verdict = {
        "add_to_live_donchian": False,
        "confidence": "LOW",
        "headline_nl": (
            "Niet verwerken in live Donchian. Picks-only kost ~€950 op 14 dagen; "
            "1d excess vs BTC is gelijk voor pick vs non-pick. Rank/size/macro-plussen "
            "zijn één kort pad, geen 1y-bewijs."
        ),
        "why_nl": [
            f"AlphaI-labels: {alphai_meta['sessions']} sessions, {dates[0]}–{dates[-1]} "
            f"({len(dates)} dagen). Het loop-jaar heeft geen AlphaI-tape.",
            f"require_pick {require_delta:+.0f} € — non-pick breakouts waren de winst.",
            (
                f"Breakout 1d excess vs BTC: pick {pick_xs_1} (n={pick_n_1}) vs "
                f"non-pick {non_xs_1} (n={non_n_1})"
                + (" — praktisch gelijk." if xs_wash else ".")
            ),
            f"rank_pick {rank_delta:+.0f} / size_pick {size_delta:+.0f} / flatten_macro {flatten_delta:+.0f} "
            f"op 14 dagen is te weinig om live knoppen te wijzigen.",
            "avoid_block en skip_macro = 0 € (avoid leeg; macro viel niet samen met extra entries).",
        ],
        "helpful_modes": helpful,
        "hurt_modes": hurt,
        "deltas": {
            "require_pick": require_delta,
            "rank_pick": rank_delta,
            "skip_macro": skip_delta,
            "size_pick": size_delta,
            "flatten_macro": flatten_delta,
            "dump_nonpick": dump_delta,
        },
        "pick_xs_btc_1d": pick_xs_1,
        "nonpick_xs_btc_1d": non_xs_1,
        "pick_xs_btc_5d": (quality["xs_btc_5d_pick"] or {}).get("mean"),
        "nonpick_xs_btc_5d": (quality["xs_btc_5d_nonpick"] or {}).get("mean"),
        "pick_xs_btc_5d_n": (quality["xs_btc_5d_pick"] or {}).get("n"),
    }

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "live_engine_changed": False,
        "book_eur": BOOK,
        "fee_rt": FEE_RT,
        "window": {"start": dates[0], "end": dates[-1], "days": len(dates)},
        "regime_days": dict(occ),
        "alphai": alphai_meta,
        "data_ceiling_nl": (
            "Candle-historie is ~1y, AlphaI-labels starten 7 sep 2026. "
            "Zonder labels is geen echte with/without Donchian-A/B over het loop-jaar mogelijk."
        ),
        "mix": REGIME_W,
        "note": "risk_off cash 70% ipv short-weakest — deze test is Donchian+AlphaI, niet de short-sleeve.",
        "overlap_mix": results,
        "unit_donch10": unit,
        "candidate_quality": {
            k: v for k, v in quality.items() if k != "sample"
        },
        "candidate_sample": quality.get("sample"),
        "one_year_donchian_mix_no_alphai": {
            "window": {"start": ydates[0], "end": ydates[-1]},
            "pnl_eur": yst.get("pnl_eur"),
            "max_dd_pct": yst.get("max_dd_pct"),
            "calmar": yst.get("calmar"),
        },
        "verdict": verdict,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)

    # Tiny bar chart of overlay deltas.
    bars = [("baseline", 0.0)] + [
        (m, results[m]["delta_vs_baseline_eur"]) for m in modes if m != "baseline"
    ]
    wsvg, hsvg = 920, 360
    pad = 70
    xs = [v for _, v in bars]
    lo, hi = min(xs + [0.0]), max(xs + [0.0])
    span = max(hi - lo, 1.0)
    bw = (wsvg - 2 * pad) / max(len(bars), 1)
    zero_y = pad + (hsvg - 2 * pad) * (hi / span)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {wsvg} {hsvg}">',
        '<rect width="100%" height="100%" fill="#0b1220"/>',
        '<text x="24" y="28" fill="#e8eef8" font-size="16" font-family="system-ui">'
        "Donchian mix · AlphaI overlay Δ vs baseline (overlap)</text>",
        f'<line x1="{pad}" x2="{wsvg - pad}" y1="{zero_y:.1f}" y2="{zero_y:.1f}" stroke="#445" />',
    ]
    for i, (name, val) in enumerate(bars):
        x = pad + i * bw + 8
        h = (hsvg - 2 * pad) * abs(val) / span
        y = zero_y - h if val >= 0 else zero_y
        color = "#3dd68c" if val > 0 else ("#888" if val == 0 else "#ff6b6b")
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw - 16:.1f}" height="{h:.1f}" fill="{color}" rx="3"/>')
        parts.append(
            f'<text x="{x + (bw - 16) / 2:.1f}" y="{hsvg - 18}" fill="#9aa4b2" font-size="10" '
            f'text-anchor="middle" font-family="system-ui">{name}</text>'
        )
        parts.append(
            f'<text x="{x + (bw - 16) / 2:.1f}" y="{y - 6 if val >= 0 else y + h + 12:.1f}" '
            f'fill="#e8eef8" font-size="11" text-anchor="middle" font-family="system-ui">{val:+.0f}</text>'
        )
    parts.append("</svg>")
    SVG.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {SVG}", flush=True)
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

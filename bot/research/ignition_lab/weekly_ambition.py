"""Weekly PnL ambition scan for ignition (target ~€2–3k/week).

Uses wet next-open Bitvavo 1d cache. Classic|coil hybrid scoring from live
``momentum_ignition``. Reports week buckets and how often weeks clear €2k/€3k.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.live.momentum_btc_rs_clip import sma
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.live.momentum_ignition import (
    IgnitionConfig,
    IgnitionPosition,
    effective_trail_pct,
    score_ignition_day,
)

FEE = 0.003
SLIP = 0.001
CACHE = Path("data/ignition_expand_candles")
END = "2026-10-02"
START = "2024-03-16"
TARGET_LO = 2_000.0
TARGET_HI = 3_000.0


def load_ohlc() -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for path in sorted(CACHE.glob("*.json")):
        out[path.stem] = json.loads(path.read_text())
    return out


def by_date(rows: list[list[float]]) -> dict[str, list[float]]:
    return {
        datetime.fromtimestamp(int(r[0]) / 1000, UTC).strftime("%Y-%m-%d"): r for r in rows
    }


def _week_key(d: str) -> str:
    dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=UTC)
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


def run_book(
    ohlc: dict[str, list[list[float]]],
    maps: dict[str, dict[str, list[float]]],
    dates: list[str],
    cfg: IgnitionConfig,
    *,
    start: str,
    end: str,
    book: float,
    max_positions: int,
    compound: bool,
) -> dict[str, Any]:
    univ = tuple(b for b in cfg.universe if b in ohlc and b != "BTC")
    cfg = replace(cfg, universe=univ, book_eur=book, max_positions=max_positions)
    cash = float(book)
    positions: list[IgnitionPosition] = []
    pending: list[tuple[str, dict[str, Any]]] = []
    trades: list[dict[str, Any]] = []
    day_pnl: dict[str, float] = {}
    equity_end: dict[str, float] = {}
    hist: dict[str, list[list[float]]] = {b: [] for b in (*univ, "BTC")}
    peak_eq = float(book)
    max_dd = 0.0

    def mark_eq() -> float:
        eq = cash
        for p in positions:
            rows = hist.get(p.base) or []
            if not rows or p.entry_price <= 0:
                continue
            eq += p.notional_eur * (float(rows[-1][4]) / p.entry_price)
        return eq

    def close_px(base: str) -> float:
        rows = hist.get(base) or []
        return float(rows[-1][4]) if rows else 0.0

    def open_px(base: str) -> float:
        rows = hist.get(base) or []
        return float(rows[-1][1]) if rows else 0.0

    for d in dates:
        # Mark equity on prior closes *before* appending today's bar so overnight
        # moves land in this day's PnL (otherwise MTM weeks stay near zero).
        in_window = start <= d <= end
        eq0 = mark_eq() if in_window else 0.0

        if d in maps["BTC"]:
            hist["BTC"].append(maps["BTC"][d])
        for b in univ:
            if d in maps.get(b, {}):
                hist[b].append(maps[b][d])
        if not in_window:
            continue

        # Fill pending at next open.
        if pending and len(positions) < max_positions:
            still: list[tuple[str, dict[str, Any]]] = []
            for base, sc in pending:
                if len(positions) >= max_positions:
                    still.append((base, sc))
                    continue
                if any(p.base == base for p in positions):
                    continue
                px = open_px(base) * (1 + SLIP)
                firepower = mark_eq() if compound else float(book)
                free_slots = max_positions - len(positions)
                notion = min(cash, firepower / max(1, free_slots)) * float(cfg.deploy_frac)
                # Cap single clip to book (or equity) / slots.
                notion = min(notion, firepower / max_positions)
                if px <= 0 or notion < cfg.min_notional_eur:
                    continue
                cash -= notion
                path = str(sc.get("entry_path") or "classic")
                trail = float(sc.get("trail_pct") or cfg.trail_pct)
                positions.append(
                    IgnitionPosition(
                        base=base,
                        entry_price=px,
                        notional_eur=notion,
                        qty=notion / px,
                        opened_ms=1,
                        peak_px=px,
                        points=int(sc.get("points") or 0),
                        trail_pct=trail,
                        entry_path=path,
                    )
                )
            pending = still

        # Manage trails.
        kept: list[IgnitionPosition] = []
        for pos in positions:
            m = close_px(pos.base)
            if m <= 0:
                kept.append(pos)
                continue
            pos.peak_px = max(float(pos.peak_px or pos.entry_price), m)
            trail = effective_trail_pct(pos, cfg)
            if trail > 0 and m <= pos.peak_px * (1 - trail):
                exit_px = m * (1 - SLIP)
                net = pos.notional_eur * ((exit_px / pos.entry_price - 1) - FEE)
                cash += pos.notional_eur + net
                trades.append(
                    {
                        "date": d,
                        "base": pos.base,
                        "net": round(net, 2),
                        "path": pos.entry_path,
                        "ret": round(exit_px / pos.entry_price - 1, 4),
                    }
                )
            else:
                kept.append(pos)
        positions = kept

        # New signals when slots free.
        if len(positions) < max_positions and not pending and len(hist["BTC"]) >= 60:
            btc = hist["BTC"]
            btc_c = [float(r[4]) for r in btc if float(r[4]) > 0]
            s50 = sma(btc_c, cfg.btc_sma_n)
            last = btc_c[-1] if btc_c else 0.0
            risk_on = (not cfg.require_btc_sma) or (s50 is not None and last > s50)
            if risk_on:
                held = {p.base for p in positions}
                ranked: list[tuple[str, dict[str, Any]]] = []
                for base in univ:
                    if base in held:
                        continue
                    sc = score_ignition_day(hist.get(base) or [], btc, cfg)
                    if not sc:
                        continue
                    path = str(sc.get("entry_path") or "")
                    if not path:
                        continue
                    if path == "classic" and sc["points"] < cfg.min_points:
                        continue
                    ranked.append((base, sc))
                ranked.sort(
                    key=lambda x: (
                        0 if x[1].get("entry_path") == "classic" else 1,
                        -int(x[1]["points"]),
                        -float(x[1]["vol_x"]),
                        -float(x[1]["day_ret"]),
                    )
                )
                slots = max_positions - len(positions)
                pending = ranked[:slots]

        eq1 = mark_eq()
        day_pnl[d] = day_pnl.get(d, 0.0) + (eq1 - eq0)
        equity_end[d] = eq1
        peak_eq = max(peak_eq, eq1)
        if peak_eq > 0:
            max_dd = max(max_dd, (peak_eq - eq1) / peak_eq)

    # Flatten residual.
    if positions:
        d = dates[-1]
        for pos in positions:
            m = close_px(pos.base)
            if m <= 0:
                continue
            exit_px = m * (1 - SLIP)
            net = pos.notional_eur * ((exit_px / pos.entry_price - 1) - FEE)
            cash += pos.notional_eur + net
            trades.append(
                {
                    "date": d,
                    "base": pos.base,
                    "net": round(net, 2),
                    "path": pos.entry_path,
                    "ret": round(exit_px / pos.entry_price - 1, 4),
                }
            )
        positions = []
        equity_end[d] = cash

    weeks: dict[str, float] = {}
    for d, pnl in day_pnl.items():
        weeks[_week_key(d)] = weeks.get(_week_key(d), 0.0) + pnl
    week_vals = sorted(weeks.values())
    n_w = len(week_vals)

    # Realized-by-exit-week: "banked €X this week" (matches operator ambition).
    realized_weeks: dict[str, float] = {}
    for t in trades:
        realized_weeks[_week_key(t["date"])] = (
            realized_weeks.get(_week_key(t["date"]), 0.0) + float(t["net"])
        )
    # Include empty holding weeks as 0 for hit-rate over the same calendar.
    for wk in weeks:
        realized_weeks.setdefault(wk, 0.0)
    real_vals = sorted(realized_weeks.values())
    n_rw = len(real_vals)

    def pct(xs: list[float], p: float) -> float:
        if not xs:
            return 0.0
        i = min(len(xs) - 1, max(0, int(round((p / 100) * (len(xs) - 1)))))
        return xs[i]

    hit2 = sum(1 for v in week_vals if v >= TARGET_LO)
    hit3 = sum(1 for v in week_vals if v >= TARGET_HI)
    r_hit2 = sum(1 for v in real_vals if v >= TARGET_LO)
    r_hit3 = sum(1 for v in real_vals if v >= TARGET_HI)
    best_trades = sorted(trades, key=lambda t: -float(t["net"]))[:8]
    final = cash if not positions else mark_eq()
    return {
        "pnl_eur": round(final - book, 2),
        "final_eur": round(final, 2),
        "max_dd": round(max_dd, 3),
        "n_trades": len(trades),
        "win": round(sum(1 for t in trades if t["net"] > 0) / len(trades), 3) if trades else 0,
        "best_trade": round(best_trades[0]["net"], 2) if best_trades else 0,
        "best_trades": best_trades,
        "n_weeks": n_w,
        "avg_week": round(sum(week_vals) / n_w, 1) if n_w else 0,
        "med_week": round(pct(week_vals, 50), 1),
        "p75_week": round(pct(week_vals, 75), 1),
        "p90_week": round(pct(week_vals, 90), 1),
        "max_week": round(max(week_vals), 1) if week_vals else 0,
        "weeks_ge_2k": hit2,
        "weeks_ge_3k": hit3,
        "pct_weeks_ge_2k": round(100 * hit2 / n_w, 1) if n_w else 0,
        "pct_weeks_ge_3k": round(100 * hit3 / n_w, 1) if n_w else 0,
        "realized_avg_week": round(sum(real_vals) / n_rw, 1) if n_rw else 0,
        "realized_max_week": round(max(real_vals), 1) if real_vals else 0,
        "realized_weeks_ge_2k": r_hit2,
        "realized_weeks_ge_3k": r_hit3,
        "realized_pct_weeks_ge_2k": round(100 * r_hit2 / n_rw, 1) if n_rw else 0,
        "realized_pct_weeks_ge_3k": round(100 * r_hit3 / n_rw, 1) if n_rw else 0,
        "path_pnl": {
            "classic": round(sum(t["net"] for t in trades if t["path"] == "classic"), 2),
            "coil": round(sum(t["net"] for t in trades if t["path"] == "coil"), 2),
        },
    }


def main() -> None:
    ohlc = load_ohlc()
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC in {CACHE}")
    desk = tuple(b for b in DEFAULT_UNIVERSE if b in ohlc)
    expanded = tuple(b for b in ohlc if b != "BTC")
    maps = {b: by_date(rows) for b, rows in ohlc.items()}
    dates = sorted(maps["BTC"])
    print(f"desk={len(desk)} expanded={len(expanded)} bars={len(dates)}", flush=True)

    specs: list[tuple[str, IgnitionConfig, dict[str, Any]]] = []
    books = (2_000, 5_000, 10_000, 20_000, 50_000)
    for univ_name, univ in (("desk", desk), ("top80", expanded)):
        for coil in (False, True):
            for slots in (1, 2, 3):
                for book in books:
                    for compound in (False, True):
                        # Skip absurd grid cells: tiny book multi-slot compound duplicates.
                        if book < 5_000 and slots > 2 and compound:
                            continue
                        cfg = IgnitionConfig(
                            universe=univ,
                            book_eur=float(book),
                            max_positions=slots,
                            trail_pct=0.12,
                            trail_ratchet_arm_pct=0.30,
                            trail_ratchet_pct=0.10,
                            coil_entry_enabled=coil,
                            coil_trail_pct=0.25,
                            quiet_max=0.12 if univ_name == "desk" else 0.15,
                        )
                        name = (
                            f"{univ_name}|{'coil' if coil else 'classic'}|"
                            f"s{slots}|b{book}|{'eq' if compound else 'fix'}"
                        )
                        specs.append(
                            (
                                name,
                                cfg,
                                {
                                    "book": book,
                                    "slots": slots,
                                    "compound": compound,
                                    "univ": univ_name,
                                    "coil": coil,
                                },
                            )
                        )

    rows: list[dict[str, Any]] = []
    for i, (name, cfg, meta) in enumerate(specs, 1):
        res = run_book(
            ohlc,
            maps,
            dates,
            cfg,
            start=START,
            end=END,
            book=float(meta["book"]),
            max_positions=int(meta["slots"]),
            compound=bool(meta["compound"]),
        )
        # Ambition score: realized €2k-week hit rate, then max realized week, pnl.
        score = (
            1000 * float(res["realized_pct_weeks_ge_2k"])
            + 10 * float(res["realized_max_week"])
            + 0.01 * float(res["pnl_eur"])
        )
        row = {"name": name, "score": round(score, 2), **meta, **res}
        rows.append(row)
        if i % 20 == 0 or i == len(specs):
            print(
                f"  {i}/{len(specs)} {name} mtm_avg {res['avg_week']} "
                f"real_max {res['realized_max_week']} "
                f"real≥2k {res['realized_pct_weeks_ge_2k']}%",
                flush=True,
            )

    rows.sort(key=lambda r: (-float(r["score"]), -float(r["pnl_eur"])))
    # Also: smallest book that ever hits ≥1 week of €2k / €3k (realized).
    hitters_2k = [r for r in rows if r["realized_weeks_ge_2k"] > 0]
    hitters_3k = [r for r in rows if r["realized_weeks_ge_3k"] > 0]
    hitters_2k.sort(key=lambda r: (r["book"], -r["realized_weeks_ge_2k"], -r["pnl_eur"]))
    hitters_3k.sort(key=lambda r: (r["book"], -r["realized_weeks_ge_3k"], -r["pnl_eur"]))

    # Focus: live-like desk coil s1 b2k fixed as baseline.
    baseline = next(
        (r for r in rows if r["name"] == "desk|coil|s1|b2000|fix"),
        None,
    )
    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "target_week_eur": [TARGET_LO, TARGET_HI],
        "start": START,
        "end": END,
        "baseline_live_like": baseline,
        "n_specs": len(rows),
        "top_ambition": rows[:20],
        "smallest_book_hit_2k": hitters_2k[:10],
        "smallest_book_hit_3k": hitters_3k[:10],
        "desk_coil_by_book": [
            r
            for r in rows
            if r["univ"] == "desk" and r["coil"] and r["slots"] == 1 and not r["compound"]
        ],
    }
    out = Path("bot/research/ignition_lab/WEEKLY_AMBITION.json")
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}", flush=True)
    print("\nBASELINE desk|coil|s1|b2000|fix:", flush=True)
    if baseline:
        print(
            f"  pnl {baseline['pnl_eur']:+.0f} mtm_avg_week {baseline['avg_week']} "
            f"mtm_max {baseline['max_week']} "
            f"realized_max {baseline['realized_max_week']} "
            f"real≥2k {baseline['realized_weeks_ge_2k']} "
            f"({baseline['realized_pct_weeks_ge_2k']}%) "
            f"real≥3k {baseline['realized_weeks_ge_3k']} "
            f"({baseline['realized_pct_weeks_ge_3k']}%)",
            flush=True,
        )
    print("\nTOP ambition (realized €2k-week hit-rate):", flush=True)
    for r in rows[:12]:
        print(
            f"  {r['name']:<34} pnl {r['pnl_eur']:+8.0f} "
            f"mtm_avg {r['avg_week']:+7.0f} real_max {r['realized_max_week']:+7.0f} "
            f"real≥2k {r['realized_pct_weeks_ge_2k']:5.1f}% "
            f"real≥3k {r['realized_pct_weeks_ge_3k']:5.1f}% "
            f"dd {r['max_dd']*100:.0f}%",
            flush=True,
        )
    print("\nSmallest books that ever banked a €2k week (realized):", flush=True)
    for r in hitters_2k[:8]:
        print(
            f"  {r['name']:<34} real≥2k {r['realized_weeks_ge_2k']} "
            f"real_max {r['realized_max_week']:+.0f} pnl {r['pnl_eur']:+.0f}",
            flush=True,
        )


if __name__ == "__main__":
    main()

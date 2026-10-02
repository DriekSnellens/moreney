"""Grid entry×exit on desk+top80 liquid; compare to desk-16 baseline."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
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
WINDOWS = {
    "full": "2024-03-16",
    "y2026": "2026-01-01",
    "last_180d": "2026-04-05",
}


def load_ohlc() -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for path in sorted(CACHE.glob("*.json")):
        out[path.stem] = json.loads(path.read_text())
    return out


def by_date(rows: list[list[float]]) -> dict[str, list[float]]:
    return {
        datetime.fromtimestamp(int(r[0]) / 1000, UTC).strftime("%Y-%m-%d"): r for r in rows
    }


def run(
    ohlc: dict[str, list[list[float]]],
    maps: dict[str, dict[str, list[float]]],
    dates: list[str],
    cfg: IgnitionConfig,
    *,
    start: str,
    end: str,
    book: float = 2_000.0,
    time_max: int = 0,
    require_xs15: bool = False,
    require_trend: bool = False,
    require_r3: bool = False,
    require_expand: bool = False,
) -> dict[str, Any]:
    univ = tuple(b for b in cfg.universe if b in ohlc and b != "BTC")
    cfg = replace(cfg, universe=univ, book_eur=book)
    cash = book
    pos: IgnitionPosition | None = None
    peak = 0.0
    pending: tuple[str, dict[str, Any]] | None = None
    entry_i = -1
    trades: list[dict[str, Any]] = []
    equity: list[float] = []
    hist: dict[str, list[list[float]]] = {b: [] for b in (*univ, "BTC")}

    for di, d in enumerate(dates):
        if d in maps["BTC"]:
            hist["BTC"].append(maps["BTC"][d])
        for b in univ:
            if d in maps.get(b, {}):
                hist[b].append(maps[b][d])
        if d < start or d > end:
            continue

        def close(base: str) -> float:
            rows = hist.get(base) or []
            return float(rows[-1][4]) if rows else 0.0

        def open_px(base: str) -> float:
            rows = hist.get(base) or []
            return float(rows[-1][1]) if rows else 0.0

        if pending is not None and pos is None:
            base, sc = pending
            pending = None
            px = open_px(base) * (1 + SLIP)
            notion = min(cash, book) * cfg.deploy_frac
            if px > 0 and notion >= cfg.min_notional_eur:
                cash -= notion
                pos = IgnitionPosition(
                    base=base,
                    entry_price=px,
                    notional_eur=notion,
                    qty=notion / px,
                    opened_ms=1,
                    peak_px=px,
                    points=int(sc["points"]),
                )
                peak = px
                entry_i = di

        if pos is not None:
            m = close(pos.base)
            if m > 0:
                peak = max(peak, m)
                pos.peak_px = peak
                trail = effective_trail_pct(pos, cfg)
                stop = bool(trail > 0 and m <= peak * (1 - trail))
                if time_max > 0 and entry_i >= 0 and (di - entry_i) >= time_max:
                    stop = True
                if stop:
                    exit_px = m * (1 - SLIP)
                    net = pos.notional_eur * ((exit_px / pos.entry_price - 1) - FEE)
                    cash += pos.notional_eur + net
                    trades.append(
                        {
                            "base": pos.base,
                            "net": net,
                            "in_desk": pos.base in DEFAULT_UNIVERSE,
                        }
                    )
                    pos = None
                    peak = 0.0

        if pos is None and pending is None and len(hist["BTC"]) >= 60:
            btc = hist["BTC"]
            btc_c = [float(r[4]) for r in btc if float(r[4]) > 0]
            s50 = sma(btc_c, cfg.btc_sma_n)
            last = btc_c[-1] if btc_c else 0.0
            risk_on = (not cfg.require_btc_sma) or (s50 is not None and last > s50)
            if risk_on:
                ranked: list[tuple[str, dict[str, Any]]] = []
                for base in univ:
                    sc = score_ignition_day(hist.get(base) or [], btc, cfg)
                    if not sc or not sc["early_signal"] or sc["points"] < cfg.min_points:
                        continue
                    atoms = set(sc.get("atoms") or [])
                    if require_xs15 and "xs15" not in atoms and "xs25" not in atoms:
                        continue
                    if require_trend and "trend" not in atoms:
                        continue
                    if require_r3 and "r3_15" not in atoms:
                        continue
                    if require_expand and "expand" not in atoms:
                        continue
                    ranked.append((base, sc))
                ranked.sort(
                    key=lambda x: (x[1]["points"], x[1]["vol_x"], x[1]["day_ret"]),
                    reverse=True,
                )
                if ranked:
                    pending = ranked[0]

        eq = cash
        if pos is not None and pos.entry_price:
            eq += pos.notional_eur * (close(pos.base) / pos.entry_price)
        equity.append(eq)

    if pos is not None:
        m = close(pos.base)
        if m > 0:
            exit_px = m * (1 - SLIP)
            net = pos.notional_eur * ((exit_px / pos.entry_price - 1) - FEE)
            cash += pos.notional_eur + net
            trades.append(
                {
                    "base": pos.base,
                    "net": net,
                    "in_desk": pos.base in DEFAULT_UNIVERSE,
                }
            )

    peak_e = book
    max_dd = 0.0
    for e in equity:
        peak_e = max(peak_e, e)
        max_dd = max(max_dd, (peak_e - e) / peak_e if peak_e else 0)
    wins = sum(1 for t in trades if t["net"] > 0)
    outside = [t for t in trades if not t["in_desk"]]
    return {
        "pnl_eur": round(cash - book, 2),
        "max_dd": round(max_dd, 3),
        "n_trades": len(trades),
        "win": round(wins / len(trades), 3) if trades else 0,
        "avg_trade": round(sum(t["net"] for t in trades) / len(trades), 1) if trades else 0,
        "outside_n": len(outside),
        "outside_pnl": round(sum(t["net"] for t in outside), 2),
        "outside_bases": sorted({t["base"] for t in outside}),
    }


ENTRY_SPECS: list[tuple[str, dict[str, Any]]] = [
    ("live_gates", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3}),
    ("day5_vol2", {"day_ret_min": 0.05, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3}),
    ("day6_vol2.5", {"day_ret_min": 0.06, "vol_mult_min": 2.5, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3}),
    ("day6_vol3", {"day_ret_min": 0.06, "vol_mult_min": 3.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3}),
    ("day8_vol2", {"day_ret_min": 0.08, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3}),
    ("day8_vol3", {"day_ret_min": 0.08, "vol_mult_min": 3.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3}),
    ("quiet08", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.08, "min_points": 3}),
    ("quiet15", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.15, "min_points": 3}),
    ("brk10_day6_vol2", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 10, "quiet_max": 0.12, "min_points": 3}),
    ("brk10_day8_vol3", {"day_ret_min": 0.08, "vol_mult_min": 3.0, "breakout_days": 10, "quiet_max": 0.12, "min_points": 3}),
    ("minpts4", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 4}),
    ("minpts5", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 5}),
    ("live+xs15", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3, "require_xs15": True}),
    ("live+trend", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3, "require_trend": True}),
    ("live+r3", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3, "require_r3": True}),
    ("live+expand", {"day_ret_min": 0.06, "vol_mult_min": 2.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3, "require_expand": True}),
    ("vol3+xs15", {"day_ret_min": 0.06, "vol_mult_min": 3.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3, "require_xs15": True}),
    ("vol3+trend", {"day_ret_min": 0.06, "vol_mult_min": 3.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 3, "require_trend": True}),
    ("day8_vol3+xs15", {"day_ret_min": 0.08, "vol_mult_min": 3.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 4, "require_xs15": True}),
    ("day8_vol3+trend", {"day_ret_min": 0.08, "vol_mult_min": 3.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 4, "require_trend": True}),
    ("vol3+xs15+trend", {"day_ret_min": 0.06, "vol_mult_min": 3.0, "breakout_days": 20, "quiet_max": 0.12, "min_points": 4, "require_xs15": True, "require_trend": True}),
    ("quiet08_vol3", {"day_ret_min": 0.06, "vol_mult_min": 3.0, "breakout_days": 20, "quiet_max": 0.08, "min_points": 3}),
]

EXIT_SPECS: list[tuple[str, dict[str, Any]]] = [
    ("t12_r30", {"trail_pct": 0.12, "trail_ratchet_arm_pct": 0.30, "trail_ratchet_pct": 0.10}),
    ("t15", {"trail_pct": 0.15, "trail_ratchet_arm_pct": 0.0, "trail_ratchet_pct": 0.0}),
    ("t10", {"trail_pct": 0.10, "trail_ratchet_arm_pct": 0.0, "trail_ratchet_pct": 0.0}),
    ("t12", {"trail_pct": 0.12, "trail_ratchet_arm_pct": 0.0, "trail_ratchet_pct": 0.0}),
    ("t12_r25_8", {"trail_pct": 0.12, "trail_ratchet_arm_pct": 0.25, "trail_ratchet_pct": 0.08}),
    ("t12_r40_10", {"trail_pct": 0.12, "trail_ratchet_arm_pct": 0.40, "trail_ratchet_pct": 0.10}),
    ("t12_r30_time14", {"trail_pct": 0.12, "trail_ratchet_arm_pct": 0.30, "trail_ratchet_pct": 0.10, "time_max": 14}),
    ("t12_r30_time21", {"trail_pct": 0.12, "trail_ratchet_arm_pct": 0.30, "trail_ratchet_pct": 0.10, "time_max": 21}),
    ("t08", {"trail_pct": 0.08, "trail_ratchet_arm_pct": 0.0, "trail_ratchet_pct": 0.0}),
    ("t20", {"trail_pct": 0.20, "trail_ratchet_arm_pct": 0.0, "trail_ratchet_pct": 0.0}),
]


def main() -> None:
    ohlc = load_ohlc()
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC in {CACHE}")
    desk = tuple(b for b in DEFAULT_UNIVERSE if b in ohlc)
    expanded = tuple(b for b in ohlc if b != "BTC")
    maps = {b: by_date(rows) for b, rows in ohlc.items()}
    dates = sorted(maps["BTC"])
    print(f"desk={len(desk)} expanded={len(expanded)} bars={len(dates)}", flush=True)

    baselines: dict[str, dict[str, Any]] = {}
    desk_cfg = IgnitionConfig(
        universe=desk,
        trail_pct=0.12,
        trail_ratchet_arm_pct=0.30,
        trail_ratchet_pct=0.10,
    )
    for wname, start in WINDOWS.items():
        baselines[wname] = run(ohlc, maps, dates, desk_cfg, start=start, end=END)
        b = baselines[wname]
        print(
            f"baseline desk {wname}: pnl {b['pnl_eur']:+.0f} "
            f"dd {b['max_dd'] * 100:.1f}% n {b['n_trades']}",
            flush=True,
        )

    rows: list[dict[str, Any]] = []
    total = len(ENTRY_SPECS) * len(EXIT_SPECS)
    done = 0
    for ename, eraw in ENTRY_SPECS:
        ekw = dict(eraw)
        req = {
            "require_xs15": bool(ekw.pop("require_xs15", False)),
            "require_trend": bool(ekw.pop("require_trend", False)),
            "require_r3": bool(ekw.pop("require_r3", False)),
            "require_expand": bool(ekw.pop("require_expand", False)),
        }
        for xname, xraw in EXIT_SPECS:
            xkw = dict(xraw)
            time_max = int(xkw.pop("time_max", 0) or 0)
            cfg = IgnitionConfig(
                universe=expanded,
                day_ret_min=float(ekw["day_ret_min"]),
                vol_mult_min=float(ekw["vol_mult_min"]),
                breakout_days=int(ekw["breakout_days"]),
                quiet_max=float(ekw["quiet_max"]),
                min_points=int(ekw["min_points"]),
                trail_pct=float(xkw["trail_pct"]),
                trail_ratchet_arm_pct=float(xkw["trail_ratchet_arm_pct"]),
                trail_ratchet_pct=float(xkw["trail_ratchet_pct"]),
            )
            name = f"{ename}__{xname}"
            by_w: dict[str, Any] = {}
            for wname, start in WINDOWS.items():
                by_w[wname] = run(
                    ohlc,
                    maps,
                    dates,
                    cfg,
                    start=start,
                    end=END,
                    time_max=time_max,
                    **req,
                )
            done += 1
            if done % 20 == 0 or done == total:
                print(f"  {done}/{total} {name}", flush=True)
            # Score: beat desk on full + not terrible recently
            score = (
                float(by_w["full"]["pnl_eur"])
                + 0.5 * float(by_w["y2026"]["pnl_eur"])
                + 0.25 * float(by_w["last_180d"]["pnl_eur"])
            )
            rows.append(
                {
                    "name": name,
                    "entry": ename,
                    "exit": xname,
                    "score": round(score, 2),
                    "delta_full_vs_desk": round(
                        by_w["full"]["pnl_eur"] - baselines["full"]["pnl_eur"], 2
                    ),
                    "delta_y2026_vs_desk": round(
                        by_w["y2026"]["pnl_eur"] - baselines["y2026"]["pnl_eur"], 2
                    ),
                    "delta_180d_vs_desk": round(
                        by_w["last_180d"]["pnl_eur"] - baselines["last_180d"]["pnl_eur"], 2
                    ),
                    "beats_desk_full": by_w["full"]["pnl_eur"] > baselines["full"]["pnl_eur"],
                    "beats_desk_all": all(
                        by_w[w]["pnl_eur"] > baselines[w]["pnl_eur"] for w in WINDOWS
                    ),
                    "windows": by_w,
                }
            )

    rows.sort(
        key=lambda r: (
            -int(r["beats_desk_all"]),
            -int(r["beats_desk_full"]),
            -float(r["score"]),
        )
    )
    beat_full = [r for r in rows if r["beats_desk_full"]]
    beat_all = [r for r in rows if r["beats_desk_all"]]
    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "end": END,
        "desk_n": len(desk),
        "expanded_n": len(expanded),
        "baselines_desk": baselines,
        "n_combos": len(rows),
        "n_beat_desk_full": len(beat_full),
        "n_beat_desk_all_windows": len(beat_all),
        "top": rows[:25],
        "best_beat_desk_full": beat_full[:10],
        "best_beat_desk_all": beat_all[:10],
        "best_expanded_even_if_below_desk": rows[:15],
    }
    out = Path("bot/research/ignition_lab/EXPAND_GRID.json")
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}", flush=True)
    print(f"beat desk full: {len(beat_full)} / {len(rows)}", flush=True)
    print(f"beat desk all windows: {len(beat_all)} / {len(rows)}", flush=True)
    print("\nTOP 12 by score:", flush=True)
    for r in rows[:12]:
        f = r["windows"]["full"]
        print(
            f"  {r['name']:<40} full {f['pnl_eur']:+7.0f} "
            f"(Δdesk {r['delta_full_vs_desk']:+7.0f})  "
            f"y2026 {r['windows']['y2026']['pnl_eur']:+7.0f}  "
            f"180d {r['windows']['last_180d']['pnl_eur']:+7.0f}  "
            f"out_pnl {f['outside_pnl']:+.0f} n {f['n_trades']}",
            flush=True,
        )
    if beat_full:
        print("\nBest that BEAT desk on full:", flush=True)
        for r in beat_full[:8]:
            print(
                f"  {r['name']:<40} full {r['windows']['full']['pnl_eur']:+.0f} "
                f"Δ {r['delta_full_vs_desk']:+.0f} outside {r['windows']['full']['outside_bases']}",
                flush=True,
            )
    else:
        print("\nNo combo beat desk-16 on the full window.", flush=True)


if __name__ == "__main__":
    main()

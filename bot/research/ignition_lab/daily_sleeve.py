"""Daily OKX sleeve lab — entries most days + same/next-day exits.

Wet next-open Bitvavo 1d cache (proxy for OKX EUR tape). Goal: find packs that
trade frequently (ideally near-daily when risk-on) with best PnL / DD, for an
OKX sleeve that wants daily activity rather than sparse ignition spikes.
"""

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
BOOK = 2_000.0


def load_ohlc() -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for path in sorted(CACHE.glob("*.json")):
        out[path.stem] = json.loads(path.read_text())
    return out


def by_date(rows: list[list[float]]) -> dict[str, list[float]]:
    return {
        datetime.fromtimestamp(int(r[0]) / 1000, UTC).strftime("%Y-%m-%d"): r for r in rows
    }


def _qvol(row: list[float]) -> float:
    return max(0.0, float(row[5]) * float(row[4]))


def pick_signal(
    hist: dict[str, list[list[float]]],
    univ: tuple[str, ...],
    cfg: IgnitionConfig,
    *,
    mode: str,
) -> tuple[str, dict[str, Any]] | None:
    """Return (base, score_meta) or None."""
    btc = hist.get("BTC") or []
    if len(btc) < 60:
        return None
    btc_c = [float(r[4]) for r in btc if float(r[4]) > 0]
    s50 = sma(btc_c, cfg.btc_sma_n)
    last = btc_c[-1] if btc_c else 0.0
    if cfg.require_btc_sma and (s50 is None or last <= s50):
        return None

    ranked: list[tuple[str, dict[str, Any], tuple]] = []
    for base in univ:
        rows = hist.get(base) or []
        if len(rows) < 25:
            continue
        sc = score_ignition_day(rows, btc, cfg)
        if sc is None:
            continue
        day = rows[-1]
        o, c = float(day[1]), float(day[4])
        if o <= 0 or c <= 0:
            continue
        day_ret = c / o - 1.0
        vol_x = float(sc.get("vol_x") or 0.0)
        liquid = bool(sc.get("liquid"))
        if not liquid:
            continue

        if mode == "ignition":
            path = str(sc.get("entry_path") or "")
            if not path:
                continue
            if path == "classic" and int(sc["points"]) < cfg.min_points:
                continue
            key = (
                0 if path == "classic" else 1,
                -int(sc["points"]),
                -vol_x,
                -day_ret,
            )
            ranked.append((base, {**sc, "pick_mode": mode}, key))
            continue

        if mode == "day_mom":
            # Strongest up-day among liquid names; optional mild filters via cfg.
            if day_ret < float(cfg.day_ret_min):
                continue
            if vol_x < float(cfg.vol_mult_min):
                continue
            if float(sc.get("quiet_ret") or 0.0) >= float(cfg.quiet_max):
                continue
            key = (-day_ret, -vol_x, -int(sc.get("points") or 0))
            ranked.append(
                (
                    base,
                    {
                        **sc,
                        "entry_path": "day_mom",
                        "trail_pct": cfg.trail_pct,
                        "pick_mode": mode,
                    },
                    key,
                )
            )
            continue

        if mode == "coil_or_day":
            path = str(sc.get("entry_path") or "")
            if path:
                if path == "classic" and int(sc["points"]) < cfg.min_points:
                    path = ""
            if not path:
                # Soft day momentum fallback for daily coverage.
                if (
                    day_ret >= float(cfg.day_ret_min)
                    and vol_x >= float(cfg.vol_mult_min)
                    and float(sc.get("quiet_ret") or 0.0) < float(cfg.quiet_max)
                ):
                    path = "day_mom"
                else:
                    continue
            key = (
                0 if path == "classic" else 1 if path == "coil" else 2,
                -day_ret,
                -vol_x,
            )
            ranked.append(
                (
                    base,
                    {
                        **sc,
                        "entry_path": path,
                        "trail_pct": (
                            cfg.coil_trail_pct if path == "coil" else cfg.trail_pct
                        ),
                        "pick_mode": mode,
                    },
                    key,
                )
            )
            continue

        if mode == "top_day_always":
            # Always pick best day_ret when risk-on (guaranteed activity).
            key = (-day_ret, -vol_x)
            ranked.append(
                (
                    base,
                    {
                        **sc,
                        "entry_path": "force",
                        "trail_pct": cfg.trail_pct,
                        "pick_mode": mode,
                        "day_ret": day_ret,
                    },
                    key,
                )
            )

    if not ranked:
        return None
    ranked.sort(key=lambda x: x[2])
    base, sc, _ = ranked[0]
    return base, sc


def run_pack(
    ohlc: dict[str, list[list[float]]],
    maps: dict[str, dict[str, list[float]]],
    dates: list[str],
    univ: tuple[str, ...],
    cfg: IgnitionConfig,
    *,
    start: str,
    end: str,
    book: float,
    pick_mode: str,
    exit_mode: str,
    time_max: int = 0,
) -> dict[str, Any]:
    """exit_mode: eod | next_close | trail | trail_or_eod | trail_or_time."""
    cfg = replace(cfg, universe=univ, book_eur=book)
    cash = float(book)
    pos: IgnitionPosition | None = None
    pending: tuple[str, dict[str, Any]] | None = None
    entry_i = -1
    trades: list[dict[str, Any]] = []
    equity: list[float] = []
    hist: dict[str, list[list[float]]] = {b: [] for b in (*univ, "BTC")}
    days_in = 0
    days_entered = 0
    days_held = 0

    def close_px(base: str) -> float:
        rows = hist.get(base) or []
        return float(rows[-1][4]) if rows else 0.0

    def open_px(base: str) -> float:
        rows = hist.get(base) or []
        return float(rows[-1][1]) if rows else 0.0

    for di, d in enumerate(dates):
        if d in maps["BTC"]:
            hist["BTC"].append(maps["BTC"][d])
        for b in univ:
            if d in maps.get(b, {}):
                hist[b].append(maps[b][d])
        if d < start or d > end:
            continue
        days_in += 1

        # Fill pending at today's open.
        if pending is not None and pos is None:
            base, sc = pending
            pending = None
            px = open_px(base) * (1 + SLIP)
            notion = min(cash, book) * float(cfg.deploy_frac)
            if px > 0 and notion >= cfg.min_notional_eur:
                cash -= notion
                path = str(sc.get("entry_path") or "classic")
                trail = float(sc.get("trail_pct") or cfg.trail_pct)
                pos = IgnitionPosition(
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
                entry_i = di
                days_entered += 1

        # Manage exit.
        if pos is not None:
            days_held += 1
            m = close_px(pos.base)
            if m > 0:
                pos.peak_px = max(float(pos.peak_px or pos.entry_price), m)
                stop = False
                reason = ""
                if exit_mode in {"trail", "trail_or_eod", "trail_or_time"}:
                    trail = effective_trail_pct(pos, cfg)
                    if trail > 0 and m <= pos.peak_px * (1 - trail):
                        stop = True
                        reason = "trail"
                if exit_mode == "eod":
                    # Entered this morning → exit today's close.
                    if di == entry_i:
                        stop = True
                        reason = "eod"
                if exit_mode == "next_close":
                    if di > entry_i:
                        stop = True
                        reason = "next_close"
                if exit_mode == "trail_or_eod" and di == entry_i and not stop:
                    stop = True
                    reason = "eod"
                if exit_mode in {"trail_or_time", "time"} or time_max > 0:
                    tmax = time_max if time_max > 0 else 1
                    if (di - entry_i) >= tmax:
                        stop = True
                        reason = reason or f"time{tmax}"
                if exit_mode == "trail_or_time" and time_max > 0:
                    if (di - entry_i) >= time_max:
                        stop = True
                        reason = reason or f"time{time_max}"
                if stop:
                    exit_px = m * (1 - SLIP)
                    net = pos.notional_eur * ((exit_px / pos.entry_price - 1) - FEE)
                    cash += pos.notional_eur + net
                    trades.append(
                        {
                            "date": d,
                            "base": pos.base,
                            "net": net,
                            "path": pos.entry_path,
                            "reason": reason,
                            "ret": exit_px / pos.entry_price - 1,
                        }
                    )
                    pos = None
                    entry_i = -1

        # New signal when flat.
        if pos is None and pending is None:
            pick = pick_signal(hist, univ, cfg, mode=pick_mode)
            if pick is not None:
                pending = pick

        eq = cash
        if pos is not None and pos.entry_price > 0:
            eq += pos.notional_eur * (close_px(pos.base) / pos.entry_price)
        equity.append(eq)

    if pos is not None:
        m = close_px(pos.base)
        if m > 0:
            exit_px = m * (1 - SLIP)
            net = pos.notional_eur * ((exit_px / pos.entry_price - 1) - FEE)
            cash += pos.notional_eur + net
            trades.append(
                {
                    "date": dates[-1],
                    "base": pos.base,
                    "net": net,
                    "path": pos.entry_path,
                    "reason": "flatten",
                    "ret": exit_px / pos.entry_price - 1,
                }
            )

    peak_e = book
    max_dd = 0.0
    for e in equity:
        peak_e = max(peak_e, e)
        max_dd = max(max_dd, (peak_e - e) / peak_e if peak_e else 0.0)
    wins = sum(1 for t in trades if t["net"] > 0)
    return {
        "pnl_eur": round(cash - book, 2),
        "max_dd": round(max_dd, 3),
        "n_trades": len(trades),
        "win": round(wins / len(trades), 3) if trades else 0.0,
        "avg_trade": round(sum(t["net"] for t in trades) / len(trades), 1) if trades else 0.0,
        "days_in": days_in,
        "days_entered": days_entered,
        "entry_day_pct": round(100 * days_entered / days_in, 1) if days_in else 0.0,
        "best_trade": round(max((t["net"] for t in trades), default=0.0), 2),
        "worst_trade": round(min((t["net"] for t in trades), default=0.0), 2),
        "path_pnl": {
            "classic": round(sum(t["net"] for t in trades if t["path"] == "classic"), 2),
            "coil": round(sum(t["net"] for t in trades if t["path"] == "coil"), 2),
            "day_mom": round(sum(t["net"] for t in trades if t["path"] == "day_mom"), 2),
            "force": round(sum(t["net"] for t in trades if t["path"] == "force"), 2),
        },
    }


ENTRY_PACKS: list[tuple[str, str, dict[str, Any]]] = [
    # (name, pick_mode, cfg overrides)
    ("live_sniper", "ignition", {"quiet_max": 0.15, "coil_quiet_max": 0.10}),
    (
        "day_mom_q15_d2_v1",
        "day_mom",
        {"quiet_max": 0.15, "day_ret_min": 0.02, "vol_mult_min": 1.0, "coil_entry_enabled": False},
    ),
    (
        "day_mom_q20_d3_v12",
        "day_mom",
        {"quiet_max": 0.20, "day_ret_min": 0.03, "vol_mult_min": 1.2, "coil_entry_enabled": False},
    ),
    (
        "day_mom_q25_d2_v1",
        "day_mom",
        {"quiet_max": 0.25, "day_ret_min": 0.02, "vol_mult_min": 1.0, "coil_entry_enabled": False},
    ),
    (
        "day_mom_q30_d15_v1",
        "day_mom",
        {"quiet_max": 0.30, "day_ret_min": 0.015, "vol_mult_min": 1.0, "coil_entry_enabled": False},
    ),
    (
        "day_mom_noquiet_d2",
        "day_mom",
        {"quiet_max": 1.0, "day_ret_min": 0.02, "vol_mult_min": 1.0, "coil_entry_enabled": False},
    ),
    (
        "coil_or_day_q20",
        "coil_or_day",
        {
            "quiet_max": 0.20,
            "coil_quiet_max": 0.15,
            "day_ret_min": 0.02,
            "vol_mult_min": 1.1,
            "coil_day_ret_min": 0.02,
            "coil_vol_mult_min": 1.1,
        },
    ),
    (
        "coil_or_day_q25",
        "coil_or_day",
        {
            "quiet_max": 0.25,
            "coil_quiet_max": 0.20,
            "day_ret_min": 0.015,
            "vol_mult_min": 1.0,
            "coil_day_ret_min": 0.015,
            "coil_vol_mult_min": 1.0,
        },
    ),
    (
        "top_day_always",
        "top_day_always",
        {"quiet_max": 1.0, "day_ret_min": 0.0, "vol_mult_min": 0.0, "coil_entry_enabled": False},
    ),
    (
        "ignition_loose_q25",
        "ignition",
        {
            "quiet_max": 0.25,
            "coil_quiet_max": 0.20,
            "day_ret_min": 0.04,
            "vol_mult_min": 1.5,
            "coil_day_ret_min": 0.02,
            "coil_vol_mult_min": 1.1,
            "min_points": 2,
        },
    ),
]

EXIT_PACKS: list[tuple[str, str, dict[str, Any]]] = [
    ("eod", "eod", {"trail_pct": 0.12, "trail_ratchet_arm_pct": 0.0, "time_max": 0}),
    ("next_close", "next_close", {"trail_pct": 0.12, "trail_ratchet_arm_pct": 0.0, "time_max": 0}),
    ("t05", "trail", {"trail_pct": 0.05, "trail_ratchet_arm_pct": 0.0, "time_max": 0}),
    ("t08", "trail", {"trail_pct": 0.08, "trail_ratchet_arm_pct": 0.0, "time_max": 0}),
    ("t12_r30", "trail", {"trail_pct": 0.12, "trail_ratchet_arm_pct": 0.30, "trail_ratchet_pct": 0.10, "time_max": 0}),
    ("t08_time1", "trail_or_time", {"trail_pct": 0.08, "trail_ratchet_arm_pct": 0.0, "time_max": 1}),
    ("t05_time1", "trail_or_time", {"trail_pct": 0.05, "trail_ratchet_arm_pct": 0.0, "time_max": 1}),
    ("t08_time2", "trail_or_time", {"trail_pct": 0.08, "trail_ratchet_arm_pct": 0.0, "time_max": 2}),
    ("time1", "trail_or_time", {"trail_pct": 0.99, "trail_ratchet_arm_pct": 0.0, "time_max": 1}),
    ("time2", "trail_or_time", {"trail_pct": 0.99, "trail_ratchet_arm_pct": 0.0, "time_max": 2}),
]


def main() -> None:
    ohlc = load_ohlc()
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC in {CACHE}")
    ex_desk = tuple(b for b in ohlc if b != "BTC" and b not in DEFAULT_UNIVERSE)
    maps = {b: by_date(r) for b, r in ohlc.items()}
    dates = sorted(maps["BTC"])
    print(f"ex_desk={len(ex_desk)} bars={len(dates)} book={BOOK}", flush=True)

    rows: list[dict[str, Any]] = []
    total = len(ENTRY_PACKS) * len(EXIT_PACKS)
    done = 0
    for ename, pmode, ekw in ENTRY_PACKS:
        for xname, xmode, xkw in EXIT_PACKS:
            xraw = dict(xkw)
            time_max = int(xraw.pop("time_max", 0) or 0)
            eoverrides = {
                "quiet_max": 0.15,
                "coil_quiet_max": 0.10,
                "day_ret_min": 0.06,
                "vol_mult_min": 2.0,
                "coil_entry_enabled": True,
                "coil_day_ret_min": 0.025,
                "coil_vol_mult_min": 1.2,
                "min_points": 3,
                **ekw,
            }
            cfg = IgnitionConfig(
                universe=ex_desk,
                universe_mode="custom",
                book_eur=BOOK,
                max_positions=1,
                compound_sizing=False,
                deploy_frac=1.0,
                coil_trail_pct=0.25,
                require_btc_sma=True,
                quiet_max=float(eoverrides["quiet_max"]),
                coil_quiet_max=float(eoverrides["coil_quiet_max"]),
                day_ret_min=float(eoverrides["day_ret_min"]),
                vol_mult_min=float(eoverrides["vol_mult_min"]),
                coil_entry_enabled=bool(eoverrides["coil_entry_enabled"]),
                coil_day_ret_min=float(eoverrides["coil_day_ret_min"]),
                coil_vol_mult_min=float(eoverrides["coil_vol_mult_min"]),
                min_points=int(eoverrides["min_points"]),
                trail_pct=float(xraw.get("trail_pct", 0.12)),
                trail_ratchet_arm_pct=float(xraw.get("trail_ratchet_arm_pct", 0.0)),
                trail_ratchet_pct=float(xraw.get("trail_ratchet_pct", 0.10)),
            )
            by_w: dict[str, Any] = {}
            for wname, start in WINDOWS.items():
                by_w[wname] = run_pack(
                    ohlc,
                    maps,
                    dates,
                    ex_desk,
                    cfg,
                    start=start,
                    end=END,
                    book=BOOK,
                    pick_mode=pmode,
                    exit_mode=xmode,
                    time_max=time_max,
                )
            done += 1
            f = by_w["full"]
            # Ambition: PnL first, then entry frequency, penalize DD.
            score = (
                float(f["pnl_eur"])
                + 0.5 * float(by_w["y2026"]["pnl_eur"])
                + 0.25 * float(by_w["last_180d"]["pnl_eur"])
                + 15.0 * float(f["entry_day_pct"])  # reward daily-ish coverage
                - 2000.0 * float(f["max_dd"])
            )
            row = {
                "name": f"{ename}__{xname}",
                "entry": ename,
                "exit": xname,
                "pick_mode": pmode,
                "score": round(score, 2),
                "windows": by_w,
            }
            rows.append(row)
            if done % 20 == 0 or done == total:
                print(
                    f"  {done}/{total} {row['name']:<40} "
                    f"full {f['pnl_eur']:+7.0f} entry% {f['entry_day_pct']:5.1f} "
                    f"dd {f['max_dd']*100:4.0f}%",
                    flush=True,
                )

    rows.sort(key=lambda r: -float(r["score"]))
    # Best PnL among packs with ≥20% / ≥40% / ≥60% entry days on full.
    def best_freq(min_pct: float) -> list[dict[str, Any]]:
        return [
            r
            for r in rows
            if float(r["windows"]["full"]["entry_day_pct"]) >= min_pct
            and float(r["windows"]["full"]["pnl_eur"]) > 0
        ][:8]

    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": BOOK,
        "universe": "ex_desk",
        "universe_n": len(ex_desk),
        "note": (
            "Daily OKX sleeve grid. entry_day_pct = % of calendar days with a new entry. "
            "live_sniper is sparse baseline; top_day_always ≈ guaranteed risk-on activity."
        ),
        "n_combos": len(rows),
        "top_score": rows[:20],
        "best_pnl_overall": sorted(
            rows, key=lambda r: -float(r["windows"]["full"]["pnl_eur"])
        )[:12],
        "best_entry_ge_20pct_pnl_pos": best_freq(20),
        "best_entry_ge_40pct_pnl_pos": best_freq(40),
        "best_entry_ge_60pct_pnl_pos": best_freq(60),
        "live_sniper_baseline": next(
            (r for r in rows if r["name"].startswith("live_sniper__t12_r30")), None
        ),
    }
    out = Path("bot/research/ignition_lab/DAILY_SLEEVE.json")
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}", flush=True)

    def show(title: str, rs: list[dict[str, Any]], n: int = 8) -> None:
        print(f"\n{title}", flush=True)
        for r in rs[:n]:
            f = r["windows"]["full"]
            print(
                f"  {r['name']:<42} full {f['pnl_eur']:+7.0f} "
                f"entry% {f['entry_day_pct']:5.1f} n {f['n_trades']:3d} "
                f"win {f['win']:.0%} dd {f['max_dd']*100:4.0f}% | "
                f"2026 {r['windows']['y2026']['pnl_eur']:+7.0f} "
                f"180d {r['windows']['last_180d']['pnl_eur']:+7.0f}",
                flush=True,
            )

    show("TOP by score (PnL + entry% − DD)", rows)
    show("Best PnL overall", payload["best_pnl_overall"])
    show("Best with ≥40% entry days & +PnL", payload["best_entry_ge_40pct_pnl_pos"])
    show("Best with ≥60% entry days & +PnL", payload["best_entry_ge_60pct_pnl_pos"])
    if payload["live_sniper_baseline"]:
        show("Live sniper baseline (t12_r30)", [payload["live_sniper_baseline"]])


if __name__ == "__main__":
    main()

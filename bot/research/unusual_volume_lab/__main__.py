"""Replay the unusual-volume momentum system over the full Bitvavo EUR universe.

    .venv/bin/python -m bot.research.unusual_volume_lab --refresh      # pull 1d + 4h candles
    .venv/bin/python -m bot.research.unusual_volume_lab                # grid on the cache

Writes artifacts/unusual_volume_lab.json with the daily forward audit and every
variant's IS/OOS result. See RESULTS.md for the interpretation.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from bot.research.unusual_volume_lab.data import CACHE_1D, CACHE_4H, eur_bases, load_cached, refresh
from bot.research.unusual_volume_lab.scanner import ScanSpec, forward_audit, scan
from bot.research.unusual_volume_lab.system import SystemSpec, run_system

H4_MS = 4 * 3_600_000


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def daily_scans() -> dict[str, ScanSpec]:
    return {
        "m3_q50k": ScanSpec(),
        "m3_q1M": ScanSpec(min_day_qvol_eur=1_000_000),
        "m5_q50k": ScanSpec(mult=5.0),
        "m3_q50k_quiet": ScanSpec(max_abs_day_ret=0.05),
        "m3_q50k_up": ScanSpec(min_day_ret=0.05),
    }


def h4_scans() -> dict[str, ScanSpec]:
    return {
        "4h_m10_q20k": ScanSpec(mult=10, avg_days=180, min_day_qvol_eur=20_000),
        "4h_m20_q20k": ScanSpec(mult=20, avg_days=180, min_day_qvol_eur=20_000),
        "4h_m10_q100k": ScanSpec(mult=10, avg_days=180, min_day_qvol_eur=100_000),
        "4h_m10_q20k_up5": ScanSpec(
            mult=10, avg_days=180, min_day_qvol_eur=20_000, min_day_ret=0.05
        ),
    }


VOLUME_BANDS: tuple[tuple[float, float], ...] = (
    (50e3, 100e3),
    (100e3, 150e3),
    (150e3, 250e3),
    (100e3, 300e3),
    (100e3, 400e3),
    (200e3, 500e3),
    (500e3, 1e6),
    (1e6, 3e6),
    (3e6, 0),
)


def band_scans() -> dict[str, ScanSpec]:
    """Spike days bucketed by the day's own quote volume (0 upper bound = open)."""
    return {
        f"band_{int(lo / 1e3)}k_{int(hi / 1e3) if hi else 'inf'}k": ScanSpec(
            min_day_qvol_eur=lo, max_day_qvol_eur=hi
        )
        for lo, hi in VOLUME_BANDS
    }


def variants() -> list[SystemSpec]:
    s = SystemSpec
    return [
        s(name="base_rsi75_swing"),
        s(name="rsi80_swing", rsi_exit=80),
        s(name="rsi70_swing", rsi_exit=70),
        s(name="norsi_swing", rsi_exit=0),
        s(name="rsi75_pct10", trail_mode="pct"),
        s(name="norsi_pct10", rsi_exit=0, trail_mode="pct"),
        s(name="norsi_pct15_hs10", rsi_exit=0, trail_mode="pct", trail_pct=0.15, hard_stop=0.10),
        s(name="scaleout_20_40", scale_out=((0.20, 0.25), (0.40, 0.25))),
        s(name="scaleout_norsi", rsi_exit=0, scale_out=((0.20, 0.25), (0.40, 0.25))),
        s(name="ema_only", require_macd=False),
        s(name="macd_only", require_ema=False),
        s(name="macd_fresh3", macd_fresh=3),
        s(name="no_confluence", require_ema=False, require_macd=False, confirm_bars=1),
        s(name="confirm12", confirm_bars=12),
        s(name="pos3", max_positions=3),
        s(name="pos3_norsi_pct10", max_positions=3, rsi_exit=0, trail_mode="pct"),
        s(name="time12", time_bars=12),
        s(name="time60", time_bars=60),
        s(name="hs5", hard_stop=0.05),
        s(name="hs15", hard_stop=0.15),
    ]


def _short(r: dict) -> dict:
    return {
        k: (round(v, 3) if isinstance(v, float) else v)
        for k, v in r.items()
        if k not in ("trades", "name")
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--refresh", action="store_true", help="fetch/extend the 1d and 4h candle caches first"
    )
    ap.add_argument("--start", default="2024-06-01")
    ap.add_argument("--is-end", default="2025-12-31")
    ap.add_argument("--oos-start", default="2026-01-01")
    ap.add_argument("--end", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    ap.add_argument("--out", default="artifacts/unusual_volume_lab.json")
    ap.add_argument("--full-grid", action="store_true", help="every variant on every scan (slow)")
    a = ap.parse_args()

    log = lambda m: print(m, flush=True)  # noqa: E731
    if a.refresh:
        bases = eur_bases()
        log(f"{len(bases)} EUR markets")
        t0 = time.time()
        refresh(
            bases,
            interval="1d",
            cache=CACHE_1D,
            start_ms=_ms("2023-01-01"),
            end_ms=_ms(a.end) + 86_400_000,
            log=log,
        )
        refresh(
            bases,
            interval="4h",
            cache=CACHE_4H,
            start_ms=_ms(a.start) - 90 * 86_400_000,
            end_ms=_ms(a.end) + 86_400_000,
            log=log,
        )
        log(f"refresh done {time.time() - t0:.0f}s")

    bases = [p[:-5] for p in os.listdir(CACHE_4H) if p.endswith(".json")]
    daily = load_cached(CACHE_1D, bases)
    h4 = load_cached(CACHE_4H, bases)
    log(f"{len(daily)} names with 1d, {len(h4)} with 4h")
    periods = {"IS": (a.start, a.is_end), "OOS": (a.oos_start, a.end)}

    base_sig = scan(daily, ScanSpec(), start=a.start, end=a.end)
    audit = {
        pn: forward_audit(daily, [s for s in base_sig if lo <= s["day"] <= hi])
        for pn, (lo, hi) in periods.items()
    }
    n_is, n_oos = audit["IS"]["n_used"], audit["OOS"]["n_used"]
    log(f"daily signals {len(base_sig)}; audit IS n={n_is} OOS n={n_oos}")

    band_audit = {}
    for bname, bspec in band_scans().items():
        sig = scan(daily, bspec, start=a.start, end=a.end)
        band_audit[bname] = {
            pn: forward_audit(daily, [s for s in sig if lo <= s["day"] <= hi])
            for pn, (lo, hi) in periods.items()
        }
        c = {pn: band_audit[bname][pn]["horizons"][5]["close"] for pn in periods}
        parts = [f"{pn} {c[pn].get('mean', 0):+.3f} (n {c[pn].get('n', 0)})" for pn in periods]
        log(f"{bname:18s} 5d close mean " + "  ".join(parts))

    rows = []
    core = [v for v in variants() if v.name in ("base_rsi75_swing", "norsi_pct10", "pos3")]
    for scans, series, bar_ms in (
        (daily_scans(), daily, 86_400_000),
        (band_scans(), daily, 86_400_000),
        (h4_scans(), h4, H4_MS),
    ):
        for sname, sspec in scans.items():
            sig = scan(series, sspec, start=a.start, end=a.end, bar_ms=bar_ms)
            vs = variants() if (a.full_grid or sname == "m3_q50k") else core
            for v in vs:
                row = {"scan": sname, "variant": v.name, "signals": len(sig)}
                for pn, (lo, hi) in periods.items():
                    row[pn] = _short(run_system(daily, h4, sig, v, start=lo, end=hi))
                rows.append(row)
                i, o = row["IS"], row["OOS"]
                log(
                    f"{sname:16s} {v.name:20s} "
                    f"IS {i['pnl_eur']:8.0f} dd {i['max_dd_eur']:6.0f} n {i['n_trades']:4d} | "
                    f"OOS {o['pnl_eur']:8.0f} dd {o['max_dd_eur']:6.0f} n {o['n_trades']:4d}"
                )
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"periods": periods, "audit": audit, "band_audit": band_audit, "grid": rows}, indent=1
        )
    )
    log(f"wrote {out}")


if __name__ == "__main__":
    main()

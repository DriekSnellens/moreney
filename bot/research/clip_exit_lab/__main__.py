"""Run the BTC+RS clip exit scan on cached Bitvavo 1d bars.

Usage::

    python -m bot.research.clip_exit_lab
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_exit_lab.engine import DRY, WET, run_clip_exits
from bot.research.clip_exit_lab.policies import POLICIES

WINDOWS = (
    ("fair_2y", "2024-03-16"),
    ("since_2025", "2025-01-01"),
    ("last_12w", "2026-06-26"),
    ("last_90d", "2026-06-25"),
)


def load_cached(cache_dir: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        path = cache_dir / f"{base}.json"
        if not path.exists():
            continue
        out[base] = json.loads(path.read_text())
    return out


def _line(row: dict[str, Any]) -> str:
    return (
        f"  {row['policy']:<28} pnl {row['pnl_eur']:+9.0f}  "
        f"dd {row['max_dd_pct'] * 100:5.1f}%  calmar {row['calmar']:5.2f}  "
        f"ann {row['ann_pct'] * 100:6.1f}%  trades {row['n_trades']:3d}  "
        f"ovx {row.get('n_overlay_exits', 0):3d}"
    )


def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (-float(r["pnl_eur"]), float(r["max_dd_pct"])))


def main() -> None:
    p = argparse.ArgumentParser(description="BTC+RS clip exit overlay scan")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--out", default="artifacts/clip_exit_scan.json")
    p.add_argument("--dry-baseline", action="store_true")
    args = p.parse_args()

    ohlc = load_cached(Path(args.cache_dir))
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {args.cache_dir}")
    last = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    policies = POLICIES()
    payload: dict[str, Any] = {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": args.book,
        "last_bar": last,
        "n_bases": len(ohlc),
        "n_policies": len(policies),
        "fill": "wet_next_open_taker",
        "note": (
            "Overlays fire on completed daily closes (same 00:05 cadence as live). "
            "Live baseline has no trail/stop/TP — only SMA50 flatten + weekly RS."
        ),
        "windows": {},
    }
    for name, start in WINDOWS:
        end = last
        print(f"\n== {name} {start} → {end} ==", flush=True)
        rows = [
            run_clip_exits(ohlc, pol, start=start, end=end, book_eur=args.book, model=WET)
            for pol in policies
        ]
        ranked = _rank(rows)
        live = next(r for r in rows if r["policy"] == "live")
        best = ranked[0]
        payload["windows"][name] = {
            "start": start,
            "end": end,
            "live": live,
            "best_pnl": best,
            "ranked": [
                {
                    "policy": r["policy"],
                    "pnl_eur": r["pnl_eur"],
                    "max_dd_pct": r["max_dd_pct"],
                    "calmar": r["calmar"],
                    "ann_pct": r["ann_pct"],
                    "n_trades": r["n_trades"],
                    "n_overlay_exits": r.get("n_overlay_exits"),
                    "year_pnl": r.get("year_pnl"),
                    "end_hold": r.get("end_hold"),
                    "overlay_reasons": r.get("overlay_reasons"),
                    "delta_vs_live": round(r["pnl_eur"] - live["pnl_eur"], 2),
                }
                for r in ranked
            ],
        }
        if args.dry_baseline:
            dry = run_clip_exits(
                ohlc, next(p for p in policies if p.name == "live"),
                start=start, end=end, book_eur=args.book, model=DRY,
            )
            payload["windows"][name]["live_dry"] = dry
        print(_line(live) + "  ← live")
        print(_line(best) + "  ← best pnl")
        for r in ranked[1:6]:
            print(_line(r))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()

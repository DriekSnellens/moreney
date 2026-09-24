"""Run BTC + residual-weekly mix scan on Bitvavo 1d cache.

Usage::

    python -m bot.research.btc_residual_mix
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.btc_residual_mix.engine import run_exit_scan, run_mix_scan, run_owner_grid

WINDOWS = (
    ("fair_2y", "2024-03-16"),
    ("last_12w", "2026-06-26"),
    ("last_90d", "2026-06-21"),
)

KEYS = (
    "clip_live",
    "btc75_res25_floor8",
    "btc75_res25_flat",
    "btc75_res25_hold",
    "btc75_res25_regime",
    "btc50_res50_flat",
    "btc50_res50_hold",
    "btc50_res50_regime",
    "residual_100",
    "btc_sma50",
)


def load_cached(cache_dir: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        path = cache_dir / f"{base}.json"
        if path.exists():
            out[base] = json.loads(path.read_text())
    return out


def _line(label: str, row: dict[str, Any]) -> str:
    return (
        f"  {label:<24} pnl {row['pnl_eur']:+9.0f}  "
        f"dd {row['max_dd_pct'] * 100:5.1f}%  calmar {row['calmar']:5.2f}  "
        f"ann {row['ann_pct'] * 100:6.1f}%  trades {row['n_trades']:3d}  "
        f"hold {row.get('end_hold')}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="BTC + residual-weekly mix scan")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--out", default="artifacts/btc_residual_mix.json")
    p.add_argument("--start", default="")
    p.add_argument("--skip-mix", action="store_true")
    p.add_argument("--skip-exits", action="store_true")
    p.add_argument(
        "--owner-grid",
        action="store_true",
        help="Rank extra directional owner packs by Calmar (research only).",
    )
    args = p.parse_args()

    ohlc = load_cached(Path(args.cache_dir))
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {args.cache_dir}")
    last = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    windows = (("custom", args.start),) if args.start else WINDOWS
    payload: dict[str, Any] = {
        "asof": datetime.now(UTC).isoformat(),
        "last_bar": last,
        "book_eur": args.book,
        "fill": "wet_next_open_taker",
        "note": (
            "BTC fraction + residual-weekly alt (20d skip-1 excess). "
            "flat = SMA50 sells all. hold = never flatten. "
            "regime = SMA50 up keeps BTC+alt, SMA50 down is 100% residual. "
            "Exit overlays hit the alt sleeve only (stop/trail/TP). AlphaI off. Not armed live."
        ),
        "windows": {},
        "exits": {},
    }
    for name, start in windows:
        print(f"\n== {name} {start} → {last}  €{args.book:.0f} ==", flush=True)
        if not args.skip_mix:
            block = run_mix_scan(ohlc, start=start, end=last, book_eur=args.book)
            payload["windows"][name] = block
            for key in KEYS:
                if key in block:
                    print(_line(key, block[key]))
            if name == "last_90d":
                mix = block.get("btc50_res50_hold") or {}
                print("  weeks 50/50 hold", flush=True)
                for w in mix.get("weeks") or []:
                    print(
                        f"    {w['week']} {w['start']}→{w['end']}  {w['hold']:<22} "
                        f"{w['end_eur']:8.0f} {w['pnl_eur']:+8.0f}"
                    )
        if args.owner_grid:
            print(f"  -- owner calmar grid {name} --", flush=True)
            grid = run_owner_grid(ohlc, start=start, end=last, book_eur=args.book)
            payload.setdefault("owner_grid", {})[name] = grid
            print(f"     best {grid.get('best')} calmar {grid.get('best_calmar')}", flush=True)
            for row in (grid.get("ranked") or [])[:12]:
                print(_line(str(row.get("name") or row.get("strategy")), row))
        if args.skip_exits or name == "last_90d":
            continue
        print(f"  -- exits 50/50 SMA50 flatten {name} --", flush=True)
        exits = run_exit_scan(
            ohlc, start=start, end=last, book_eur=args.book, btc_frac=0.5, flatten="all"
        )
        payload["exits"].setdefault(name, {})["flat50"] = exits
        for row in exits["ranked"][:8]:
            print(_line(str(row.get("exit_policy") or row["strategy"]), row))
            print(
                f"     overlay {row.get('n_overlay_exits', 0):3d}  "
                f"delta {row.get('delta_vs_none', 0):+.0f}"
            )
        print(f"  -- exits 50/50 never-flatten {name} --", flush=True)
        hold = run_exit_scan(
            ohlc, start=start, end=last, book_eur=args.book, btc_frac=0.5, flatten="none"
        )
        payload["exits"][name]["hold50"] = hold
        for row in hold["ranked"][:6]:
            print(_line(str(row.get("exit_policy") or row["strategy"]), row))
            print(
                f"     overlay {row.get('n_overlay_exits', 0):3d}  "
                f"delta {row.get('delta_vs_none', 0):+.0f}"
            )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()

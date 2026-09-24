"""Run clip vs short-weakest diversification on Bitvavo 1d cache.

Usage::

    python -m bot.research.clip_short_div
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_short_div.engine import run_clip_short_div

WINDOWS = (
    ("fair_2y", "2024-03-16"),
    ("last_12w", "2026-06-26"),
)


def load_cached(cache_dir: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        path = cache_dir / f"{base}.json"
        if path.exists():
            out[base] = json.loads(path.read_text())
    return out


def _line(label: str, row: dict[str, Any]) -> str:
    corr = row.get("corr_daily")
    corr_s = f"  corr {corr:.2f}" if isinstance(corr, (int, float)) else ""
    return (
        f"  {label:<28} pnl {row['pnl_eur']:+9.0f}  "
        f"dd {row['max_dd_pct'] * 100:5.1f}%  calmar {row['calmar']:5.2f}  "
        f"ann {row['ann_pct'] * 100:6.1f}%  trades {row['n_trades']:3d}{corr_s}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="clip vs short-weakest diversification")
    p.add_argument("--book", type=float, default=24_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--out", default="artifacts/clip_short_div.json")
    p.add_argument("--start", default="")
    args = p.parse_args()

    ohlc = load_cached(Path(args.cache_dir))
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {args.cache_dir}")
    last = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    windows = ((("custom", args.start),) if args.start else WINDOWS)

    payload: dict[str, Any] = {
        "asof": datetime.now(UTC).isoformat(),
        "last_bar": last,
        "n_bases": len(ohlc),
        "total_eur": args.book,
        "fill": "wet_next_open_taker",
        "note": (
            "Independent books, €24k total. Clip = live SMA50 + weekly RS. "
            "Short-weakest = paper SMA20 gate, 15d skip-1 weakest, 10% stop, "
            "max_weight 0.5, cover_on_bull. Donch pair = donch10 + donch_fri10. "
            "AlphaI off. Short PnL is paper — spot cannot short."
        ),
        "windows": {},
    }
    for name, start in windows:
        print(f"\n== {name} {start} → {last}  book €{args.book:.0f} ==", flush=True)
        block = run_clip_short_div(
            ohlc, start=start, end=last, total_eur=args.book
        )
        payload["windows"][name] = block
        print("  gates", block.get("gates"))
        print(_line("clip 24k", block["clip_24k"]))
        print(_line("short 24k paper", block["short_24k"]))
        print(_line("donch pair 24k", block["donch_24k"]))
        print(_line("MIX clip12+short12", block["mix_clip12_short12"]))
        print(_line("MIX clip18+short6", block["mix_clip18_short6"]))
        print(_line("MIX clip12+donch12", block["mix_clip12_donch12"]))
        print(_line("MIX clip12+s6+d6", block["mix_clip12_short6_donch6"]))
        print("  year mix cs", block["mix_clip12_short12"].get("year_pnl"))
        print("  short12 deployed", block["short_12k"].get("deployed_frac"), flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()

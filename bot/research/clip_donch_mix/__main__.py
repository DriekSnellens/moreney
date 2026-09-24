"""Run 12k clip + 12k Donchian over Bitvavo 1d cache.

Usage::

    python -m bot.research.clip_donch_mix
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_donch_mix.engine import run_clip_donch_mix


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
    p = argparse.ArgumentParser(description="12k clip + 12k Donchian mix")
    p.add_argument("--clip", type=float, default=12_000.0)
    p.add_argument("--donch", type=float, default=12_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--out", default="artifacts/clip_donch_12k_mix.json")
    p.add_argument("--start", default="2024-03-16")
    args = p.parse_args()

    ohlc = load_cached(Path(args.cache_dir))
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {args.cache_dir}")
    last = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    print(
        f"window {args.start} → {last}  clip €{args.clip:.0f} + donch €{args.donch:.0f}",
        flush=True,
    )
    block = run_clip_donch_mix(
        ohlc,
        start=args.start,
        end=last,
        clip_eur=args.clip,
        donch_eur=args.donch,
    )
    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "last_bar": last,
        "n_bases": len(ohlc),
        "fill": "wet_next_open_taker",
        "note": (
            "Independent books. Clip = live SMA50 + weekly RS. "
            "Donch pair = donch10 + donch_fri10 equal split (both decide every day). "
            "Allocator = sma20_50 weights; paper short is unused cash. "
            "No AlphaI."
        ),
        **block,
    }
    print(_line("clip 12k", block["clip_12k"]))
    print(_line("donch pair 12k", block["donch_pair_12k"]))
    print(_line("donch alloc 12k", block["donch_alloc_12k"]))
    print(_line("MIX clip+donch 12/12", block["mix_12_12"]))
    print(_line("MIX clip+alloc 12/12", block["mix_12_alloc12"]))
    print(_line("clip 24k only", block["clip_24k"]))
    print(_line("donch pair 24k only", block["donch_pair_24k"]))
    mix = block["mix_12_12"]
    print("year mix", mix.get("year_pnl"))
    print("parts", mix.get("parts"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()

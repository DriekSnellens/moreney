"""Run the consistency lab.

Usage::

    .venv/bin/python -m bot.research.consistency_lab [--only ens_7clocks,baseline]
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from bot.research.consistency_lab.engine import run_grid
from bot.research.factor_scoreboard.__main__ import load_candles


def main() -> None:
    p = argparse.ArgumentParser(description="Consistency lab")
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--start", default="2024-03-16")
    p.add_argument("--is-end", default="2025-12-31")
    p.add_argument("--oos-start", default="2026-01-01")
    p.add_argument("--out", default="artifacts/consistency_lab.json")
    p.add_argument("--no-fresh", action="store_true")
    p.add_argument("--only", default="")
    args = p.parse_args()

    ohlc = load_candles(Path(args.cache_dir), merge_fresh=not args.no_fresh)
    end = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    windows = (
        ("full", args.start, end),
        ("is", args.start, args.is_end),
        ("oos", args.oos_start, end),
    )
    only = [s for s in args.only.split(",") if s]
    rows = run_grid(ohlc, windows=windows, book_eur=args.book, log=print, only=only or None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"end": end, "windows": windows, "rows": rows}, indent=1))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

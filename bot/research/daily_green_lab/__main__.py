"""CLI: ``.venv/bin/python -m bot.research.daily_green_lab``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.daily_green_lab.engine import run_daily_green_lab, to_markdown


def main() -> None:
    p = argparse.ArgumentParser(description="Daily-green €1.7k sleeve search")
    p.add_argument("--book", type=float, default=1_700.0)
    p.add_argument("--days", type=int, default=45)
    p.add_argument("--candles", default="data/residual_wet_candles")
    p.add_argument("--alphai", default="data/research/alphai_sessions_merged.json")
    p.add_argument("--out", default="artifacts/daily_green_lab.json")
    p.add_argument("--md", default="artifacts/daily_green_lab.md")
    args = p.parse_args()

    print(
        f"daily_green_lab book=€{args.book:.0f} days={args.days}",
        flush=True,
    )
    payload = run_daily_green_lab(
        candle_dir=args.candles,
        alphai_path=args.alphai,
        book=args.book,
        days=args.days,
    )
    for wname, block in (payload.get("windows") or {}).items():
        w = block.get("winner") or block.get("best_score") or {}
        print(
            f"== {wname} == {w.get('name')} "
            f"avg€/day={w.get('avg_day_pnl')} "
            f"green={100*float(w.get('pct_days_green') or 0):.0f}% "
            f"in_mkt={100*float(w.get('pct_days_in_market') or 0):.0f}% "
            f"hit100={w.get('n_days_hit_100')}/{w.get('n_days')}",
            flush=True,
        )
    md = to_markdown(payload)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    Path(args.md).write_text(md, encoding="utf-8")
    pkg = Path(__file__).resolve().parent
    (pkg / "RESULTS.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (pkg / "RESULTS.md").write_text(md, encoding="utf-8")
    print(f"wrote {args.md}", flush=True)


if __name__ == "__main__":
    main()

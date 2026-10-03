"""CLI: ``.venv/bin/python -m bot.research.daily_green_lab``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.daily_green_lab.engine import run_daily_green_lab, to_markdown
from bot.research.daily_green_lab.optimize import run_optimize
from bot.research.daily_green_lab.optimize import to_markdown as opt_markdown


def main() -> None:
    p = argparse.ArgumentParser(
        description="Daily-green €1.7k sleeve search — full liquid universe × entry/exit grid"
    )
    p.add_argument("--book", type=float, default=1_700.0)
    p.add_argument("--days", type=int, default=45)
    p.add_argument(
        "--candles",
        default="data/ignition_expand_candles",
        help="OHLCV dir (default: full expand universe, not desk residual)",
    )
    p.add_argument("--alphai", default="data/research/alphai_sessions_merged.json")
    p.add_argument("--out", default="artifacts/daily_green_lab.json")
    p.add_argument("--md", default="artifacts/daily_green_lab.md")
    p.add_argument(
        "--broad",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Broad entry×exit grid (default on); --no-broad for narrower pack",
    )
    p.add_argument(
        "--optimize",
        action="store_true",
        help="Walk-forward optimize (fixed-book sizing, green weeks − DD)",
    )
    p.add_argument("--workers", type=int, default=None)
    args = p.parse_args()

    pkg = Path(__file__).resolve().parent
    if args.optimize:
        print(
            f"daily_green_lab OPTIMIZE book=€{args.book:.0f} candles={args.candles}",
            flush=True,
        )
        payload = run_optimize(
            candle_dir=args.candles,
            alphai_path=args.alphai,
            book=args.book,
            workers=args.workers,
        )
        w = payload.get("winner") or {}
        oos = w.get("oos") or {}
        print(
            f"winner={w.get('name')} OOS €/wk={oos.get('avg_week_pnl')} "
            f"greenW={100*float(oos.get('pct_weeks_green') or 0):.0f}% "
            f"DD={100*float(oos.get('max_dd_pct') or 0):.1f}% "
            f"robust={payload.get('n_robust_oos')}/{payload.get('n_ok')}",
            flush=True,
        )
        md = opt_markdown(payload)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        # slim json for artifacts (drop heavy daily from nested)
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        Path(args.md).write_text(md, encoding="utf-8")
        (pkg / "OPTIMIZE.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (pkg / "OPTIMIZE.md").write_text(md, encoding="utf-8")
        print(f"wrote {args.md}", flush=True)
        return

    print(
        f"daily_green_lab book=€{args.book:.0f} days={args.days} "
        f"candles={args.candles} broad={args.broad}",
        flush=True,
    )
    payload = run_daily_green_lab(
        candle_dir=args.candles,
        alphai_path=args.alphai,
        book=args.book,
        days=args.days,
        workers=args.workers,
        broad=args.broad,
    )
    print(
        f"universe n_bases={payload.get('n_bases')} n_specs={payload.get('n_specs')}",
        flush=True,
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
    (pkg / "RESULTS.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (pkg / "RESULTS.md").write_text(md, encoding="utf-8")
    print(f"wrote {args.md}", flush=True)


if __name__ == "__main__":
    main()

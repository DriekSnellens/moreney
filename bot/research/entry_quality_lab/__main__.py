"""Run the entry-quality / peak-cashing lab.

Usage::

    .venv/bin/python -m bot.research.entry_quality_lab [--family location]
    .venv/bin/python -m bot.research.entry_quality_lab --forensic OP:2026-10-14
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.entry_quality_lab.engine import (
    default_variants,
    features_for,
    run_grid,
    run_variant,
)
from bot.research.entry_quality_lab.flow import load_flow
from bot.research.factor_scoreboard.__main__ import load_candles


def _fmt(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v * 100:+.1f}%" if pct else f"{v:.3f}"


def forensic(ohlc, flow, spec: str) -> None:
    base, date = spec.split(":")
    feats = features_for(ohlc, flow, base, date)
    print(f"\n{base} as seen at the {date} close (buy fills next open):")
    for k, v in feats.items():
        pct = k.startswith(("ext_", "off_", "ret_"))
        print(f"  {k:14s} {_fmt(v, pct)}")
    print("  gate verdicts (False = would have skipped):")
    for name, family, gate, scale, _extra in default_variants(ohlc, flow):
        if gate is not None:
            print(f"    {family:9s} {name:26s} {gate(date, base)}")
        elif scale is not None:
            print(f"    {family:9s} {name:26s} size x{scale(date, base):.2f}")


def entries_table(ohlc, flow, *, start: str, end: str, since: str) -> None:
    row = run_variant(
        ohlc,
        name="baseline",
        gate=None,
        scale=None,
        extra={},
        windows=(("full", start, end),),
        keep_trades=True,
    )
    trades = row["full"].get("trades") or []
    buys = [t for t in trades if t.get("side") == "buy" and t.get("role") == "alt"]
    print(f"\nbaseline alt buys since {since} with entry features on the signal close:")
    hdr = (
        "date",
        "base",
        "ext20",
        "offhi60",
        "rng20",
        "rsi",
        "r1d",
        "impulse",
        "vsurge",
        "tkr3",
        "tz3",
    )
    print("  " + " ".join(f"{h:>8s}" for h in hdr))
    for t in buys:
        fill_date = str(t.get("date") or "")
        if fill_date < since:
            continue
        sig = _prev_bar_date(ohlc, fill_date)
        f = features_for(ohlc, flow, str(t["base"]), sig)
        vals = (
            sig,
            str(t["base"]),
            _fmt(f.get("ext_sma20"), True),
            _fmt(f.get("off_high60"), True),
            _fmt(f.get("range_pos20")),
            _fmt(f.get("rsi14")),
            _fmt(f.get("ret_1d"), True),
            _fmt(f.get("impulse_share")),
            _fmt(f.get("volume_surge3")),
            _fmt(f.get("taker3")),
            _fmt(f.get("taker_z3")),
        )
        print("  " + " ".join(f"{v:>8s}" for v in vals))


def _prev_bar_date(ohlc, date: str) -> str:
    dates = [
        datetime.fromtimestamp(int(r[0]) / 1000, UTC).strftime("%Y-%m-%d") for r in ohlc["BTC"]
    ]
    prev = [d for d in dates if d < date]
    return prev[-1] if prev else date


def main() -> None:
    p = argparse.ArgumentParser(description="Entry-quality and peak-cashing lab")
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--flow-dir", default="data/entry_quality_flow")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--start", default="2024-03-16")
    p.add_argument("--is-end", default="2025-12-31")
    p.add_argument("--oos-start", default="2026-01-01")
    p.add_argument("--out", default="artifacts/entry_quality_lab.json")
    p.add_argument("--no-fresh", action="store_true")
    p.add_argument("--no-flow", action="store_true")
    p.add_argument("--refresh-flow", action="store_true")
    p.add_argument("--family", default=None)
    p.add_argument("--forensic", action="append", default=[], help="BASE:YYYY-MM-DD")
    p.add_argument("--entries-since", default="2026-01-01")
    p.add_argument("--skip-grid", action="store_true")
    args = p.parse_args()

    ohlc = load_candles(Path(args.cache_dir), merge_fresh=not args.no_fresh)
    end = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    flow = None
    if not args.no_flow:
        start_ms = int(datetime(2023, 9, 1, tzinfo=UTC).timestamp() * 1000)
        flow = load_flow(
            ("BTC", *DEFAULT_UNIVERSE),
            cache_dir=Path(args.flow_dir),
            start_ms=start_ms,
            refresh=args.refresh_flow,
            log=print,
        )
    for spec in args.forensic:
        forensic(ohlc, flow, spec)
    if args.entries_since:
        entries_table(ohlc, flow, start=args.start, end=end, since=args.entries_since)
    if args.skip_grid:
        return
    windows = (
        ("full", args.start, end),
        ("is", args.start, args.is_end),
        ("oos", args.oos_start, end),
    )
    rows = run_grid(
        ohlc, flow=flow, windows=windows, book_eur=args.book, log=print, only_family=args.family
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"end": end, "windows": windows, "rows": rows}, indent=1, default=str)
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

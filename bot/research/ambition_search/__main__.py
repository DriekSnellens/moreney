"""Broad ambition search for green weeks + structural PnL.

Usage::

    .venv/bin/python -m bot.research.ambition_search
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.ambition_search.engine import ambition_specs, run_ambition_search
from bot.research.alphai_pack_compare.candles import load_cached, ohlc_dates


def _line(row: dict[str, Any] | None, label: str = "") -> str:
    if not row:
        return f"  {label:<16} (none)"
    return (
        f"  {label:<16} {str(row.get('name')):<54} "
        f"green {float(row.get('pct_weeks_pos') or 0) * 100:5.1f}%  "
        f"worst {float(row.get('worst_week') or 0):+7.0f}  "
        f"pnl {float(row.get('pnl_eur') or 0):+9.0f}  "
        f"dd {float(row.get('max_dd_pct') or 0) * 100:5.1f}%"
    )


def _md(payload: dict[str, Any]) -> str:
    lines = [
        "# Ambition search — structural profit / green weeks",
        "",
        f"asof `{payload.get('asof')}`  book €{payload.get('book_eur'):,.0f}  "
        f"daily specs `{payload.get('n_specs_daily')}`",
        "",
        payload.get("note", ""),
        "",
        "**Honesty:** 100% green weeks on multi-month tapes is usually impossible; "
        "this ranks the closest packs (highest % green weeks among profitable ones).",
        "",
    ]
    for wname, block in (payload.get("windows") or {}).items():
        lines.append(f"## {wname}  `{block.get('start')}` → `{block.get('end')}`")
        lines.append("")
        lines.append(
            f"all-weeks-green count: **{block.get('all_weeks_green_count')}**"
        )
        lines.append("")
        lines.append(_line(block.get("all_weeks_green"), "all green"))
        lines.append(_line(block.get("best_green_rate"), "best green%"))
        lines.append(_line(block.get("best_balanced"), "balanced"))
        lines.append(_line(block.get("best_pnl"), "best pnl"))
        lines.append(_line(block.get("best_low_dd_profit"), "low DD"))
        lines.append("")
        lines.append("Top green-rate packs:")
        lines.append("")
        for row in (block.get("top_green") or [])[:10]:
            lines.append(_line(row, "-"))
        lines.append("")
    ai = payload.get("alphai_overlays")
    if ai:
        lines.append(
            f"## AlphaI overlays  `{ai.get('start')}` → `{ai.get('end')}` (real picks only)"
        )
        lines.append("")
        lines.append(_line(ai.get("best_green_rate"), "best green%"))
        lines.append(_line(ai.get("best_balanced"), "balanced"))
        lines.append(_line(ai.get("best_pnl"), "best pnl"))
        lines.append("")
        for row in (ai.get("ranked_top") or [])[:12]:
            lines.append(_line(row, "-"))
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Ambition search for green weeks")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--alphai-path", default="data/research/alphai_sessions_merged.json")
    p.add_argument("--out", default="artifacts/ambition_search.json")
    p.add_argument("--md", default="artifacts/ambition_search.md")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    p.add_argument("--skip-alphai", action="store_true")
    args = p.parse_args()

    ohlc = load_cached(Path(args.cache_dir))
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC in {args.cache_dir}")
    dates = ohlc_dates(ohlc)
    specs = ambition_specs()
    print(
        f"ambition search specs={len(specs)} workers={args.workers} book=€{args.book:.0f}",
        flush=True,
    )
    result = run_ambition_search(
        ohlc,
        dates=dates,
        book_eur=args.book,
        workers=args.workers,
        alphai_path=None if args.skip_alphai else args.alphai_path,
    )
    for wname, block in (result.get("windows") or {}).items():
        print(f"\n== {wname} ==", flush=True)
        print(_line(block.get("best_green_rate"), "best green%"))
        print(_line(block.get("best_balanced"), "balanced"))
        print(_line(block.get("best_pnl"), "best pnl"))
        print(
            f"  all_weeks_green_count={block.get('all_weeks_green_count')}",
            flush=True,
        )
    if result.get("alphai_overlays"):
        print("\n== alphai overlays ==", flush=True)
        ai = result["alphai_overlays"]
        print(_line(ai.get("best_green_rate"), "best green%"))
        print(_line(ai.get("best_balanced"), "balanced"))
        print(_line(ai.get("best_pnl"), "best pnl"))

    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "last_bar": dates[-1],
        "book_eur": args.book,
        "fill": "wet_next_open_taker",
        "n_specs_daily": len(specs),
        **result,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md = Path(args.md)
    md.write_text(_md(payload), encoding="utf-8")
    pkg = Path(__file__).resolve().parent
    (pkg / "RESULTS.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (pkg / "RESULTS.md").write_text(_md(payload), encoding="utf-8")
    print(f"\nwrote {out}", flush=True)
    print(f"wrote {md}", flush=True)


if __name__ == "__main__":
    main()

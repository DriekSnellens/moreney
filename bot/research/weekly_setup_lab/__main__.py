"""Scan forward-week residual entry params ±AlphaI.

Usage::

    .venv/bin/python -m bot.research.weekly_setup_lab --refresh
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.research.weekly_setup_lab.alphai_map import load_pick_outcome_sessions
from bot.research.weekly_setup_lab.candles import load_cached, refresh_cache
from bot.research.weekly_setup_lab.engine import run_weekly_setup_grid


def _line(label: str, row: dict[str, Any]) -> str:
    return (
        f"  {label:<48} pnl {float(row.get('pnl_eur') or 0):+9.0f}  "
        f"dd {float(row.get('max_dd_pct') or 0) * 100:5.1f}%  "
        f"calmar {float(row.get('calmar') or 0):5.2f}  "
        f"ann {float(row.get('ann_pct') or 0) * 100:6.1f}%  "
        f"trades {int(row.get('n_trades') or 0):3d}"
    )


def _windows(last: str) -> list[tuple[str, str]]:
    last_dt = datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=UTC)
    return [
        ("fair_2y", "2024-03-16"),
        ("last_12w", (last_dt - timedelta(days=84)).strftime("%Y-%m-%d")),
        ("last_90d", (last_dt - timedelta(days=90)).strftime("%Y-%m-%d")),
        ("alphai_overlap", "2026-09-27"),
    ]


def _overview(payload: dict[str, Any]) -> str:
    lines = [
        "# Weekly setup lab — forward entry ±AlphaI",
        "",
        f"asof `{payload.get('asof')}`  last_bar `{payload.get('last_bar')}`  "
        f"book €{payload.get('book_eur'):,.0f}  fill `{payload.get('fill')}`",
        "",
        payload.get("note", ""),
        "",
    ]
    for wname, block in (payload.get("windows") or {}).items():
        lines.append(f"## {wname}  {block.get('start')} → {block.get('end')}")
        lines.append("")
        lines.append(
            f"Best overall: `{block.get('best')}`  "
            f"pnl {float(block.get('best_pnl_eur') or 0):+.0f}  "
            f"calmar {float(block.get('best_calmar') or 0):.2f}"
        )
        lines.append("")
        lines.append("| AlphaI | best pack | pnl | calmar | maxDD | entry | book |")
        lines.append("|---|---|---:|---:|---:|---|---|")
        for mode, row in (block.get("alphai_best") or {}).items():
            lines.append(
                f"| {mode} | `{row.get('name')}` | "
                f"{float(row.get('pnl_eur') or 0):+.0f} | "
                f"{float(row.get('calmar') or 0):.2f} | "
                f"{float(row.get('max_dd_pct') or 0) * 100:.1f}% | "
                f"{row.get('entry')} | {row.get('book')} |"
            )
        lines.append("")
        lines.append("Top 8 packs:")
        lines.append("")
        for row in (block.get("top") or [])[:8]:
            lines.append(
                f"- `{row.get('name')}`  pnl {float(row.get('pnl_eur') or 0):+.0f}  "
                f"calmar {float(row.get('calmar') or 0):.2f}  "
                f"dd {float(row.get('max_dd_pct') or 0) * 100:.1f}%"
            )
        lines.append("")
        lift = block.get("paired_alphai_lift") or []
        if lift:
            lines.append("Largest AlphaI lift vs same entry×book off:")
            lines.append("")
            for row in lift[:6]:
                lines.append(
                    f"- `{row.get('name')}`  Δpnl {float(row.get('delta_pnl') or 0):+.0f}  "
                    f"Δcalmar {float(row.get('delta_calmar') or 0):+.2f}"
                )
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Forward-week residual entry lab ±AlphaI")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--out", default="artifacts/weekly_setup_lab.json")
    p.add_argument("--md", default="artifacts/weekly_setup_lab.md")
    p.add_argument("--alphai-path", default="data/alphai/pick_outcomes.json")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--start", default="", help="If set, only this custom window")
    args = p.parse_args()

    cache = Path(args.cache_dir)
    if args.refresh:
        print("refreshing 1d Bitvavo cache…", flush=True)
        meta = refresh_cache(cache)
        print(f"  ok={meta['ok']} failed={meta['failed']}", flush=True)

    ohlc = load_cached(cache)
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {cache}")
    last = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")

    sessions: list[dict[str, Any]] = []
    alphai_path = Path(args.alphai_path)
    if alphai_path.exists():
        try:
            sessions = load_pick_outcome_sessions(alphai_path)
        except PermissionError:
            # Worker may lack read on operator-owned pick_outcomes; try via copy.
            import subprocess

            raw = subprocess.check_output(["sudo", "-n", "cat", str(alphai_path)], text=True)
            sessions = json.loads(raw).get("sessions") or []

    windows = (("custom", args.start),) if args.start else _windows(last)
    payload: dict[str, Any] = {
        "asof": datetime.now(UTC).isoformat(),
        "last_bar": last,
        "book_eur": args.book,
        "fill": "wet_next_open_taker",
        "note": (
            "Forward-week residual entry grid. Baseline ranks by trailing RS excess; "
            "setup variants add pullback / RSI / extension filters and rank by setup score. "
            "AlphaI off vs causal price proxy (full history) vs real pick_outcomes "
            "(~6d overlap only). Not armed live."
        ),
        "alphai_sessions": len(sessions),
        "windows": {},
    }

    for wname, start in windows:
        if start > last:
            print(f"\n== skip {wname}: start {start} after last {last} ==", flush=True)
            continue
        print(f"\n== {wname} {start} → {last}  €{args.book:.0f} ==", flush=True)
        block = run_weekly_setup_grid(
            ohlc,
            start=start,
            end=last,
            book_eur=args.book,
            window=wname,
            sessions=sessions,
        )
        # Drop full ranked from payload size; keep top + paired + alphai_best.
        slim = {
            k: v
            for k, v in block.items()
            if k != "ranked"
        }
        slim["ranked_tail_names"] = [r["name"] for r in (block.get("ranked") or [])[-5:]]
        payload["windows"][wname] = slim
        print(f"  best {block.get('best')} calmar {block.get('best_calmar')}", flush=True)
        for mode, row in (block.get("alphai_best") or {}).items():
            print(_line(f"ai[{mode}]", row))
        print("  -- top 8 --", flush=True)
        for row in (block.get("top") or [])[:8]:
            print(_line(str(row.get("name")), row))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    md = Path(args.md)
    md.write_text(_overview(payload))
    print(f"\nwrote {out}", flush=True)
    print(f"wrote {md}", flush=True)


if __name__ == "__main__":
    main()

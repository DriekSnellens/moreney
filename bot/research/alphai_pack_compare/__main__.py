"""Run the AlphaI-overlap pack tournament.

Usage::

    .venv/bin/python -m bot.research.alphai_pack_compare --refresh
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.alphai_pack_compare.candles import load_cached, ohlc_dates, refresh_cache
from bot.research.alphai_pack_compare.engine import run_alphai_pack_compare


def _line(label: str, row: dict[str, Any] | None) -> str:
    if not row:
        return f"  {label:<42} (none)"
    return (
        f"  {label:<42} pnl {float(row.get('pnl_eur') or 0):+9.0f}  "
        f"dd {float(row.get('max_dd_pct') or 0) * 100:5.1f}%  "
        f"calmar {float(row.get('calmar') or 0):5.2f}  "
        f"trades {int(row.get('n_trades') or 0):3d}  "
        f"hold {row.get('end_hold')}"
    )


def _md(payload: dict[str, Any]) -> str:
    block = payload.get("result") or {}
    cov = block.get("alphai_coverage") or {}
    lines = [
        "# AlphaI pack compare — real picks only",
        "",
        f"asof `{payload.get('asof')}`  window `{block.get('start')}` → `{block.get('end')}`  "
        f"book €{block.get('book_eur'):,.0f}  decision hour {block.get('decision_hour_utc'):02d}:00 UTC",
        "",
        (
            f"AlphaI coverage: **{cov.get('n_days')} days** "
            f"({cov.get('first_day')} → {cov.get('last_day')}), "
            f"{cov.get('n_sessions')} sessions. "
            "No proxy fills — days without picks stay empty until the first real session."
        ),
        "",
        "## Winners",
        "",
        _line("best calmar", block.get("best_calmar")),
        _line("best pnl", block.get("best_pnl")),
        _line("lowest dd", block.get("lowest_dd")),
        "",
        "## Best per AlphaI mode",
        "",
    ]
    for mode, row in (block.get("alphai_best") or {}).items():
        lines.append(_line(f"ai[{mode}]", row))
    lines.extend(["", "## Pareto (max PnL ∩ min DD)", ""])
    for row in block.get("pareto") or []:
        lines.append(_line(str(row.get("name")), row))
    lines.extend(["", "## Top 12 by calmar", ""])
    for row in (block.get("top") or [])[:12]:
        lines.append(_line(str(row.get("name")), row))
    lifts = block.get("paired_alphai_lift") or []
    if lifts:
        lines.extend(["", "## Largest AlphaI lift vs same pack off", ""])
        for row in lifts[:8]:
            lines.append(
                f"  {row.get('name')}: Δpnl {float(row.get('delta_pnl') or 0):+.0f}  "
                f"Δdd {float(row.get('delta_dd') or 0) * 100:+.1f}pp  "
                f"Δcalmar {float(row.get('delta_calmar') or 0):+.2f}"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="AlphaI-overlap pack tournament")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument(
        "--alphai-path",
        default="data/research/alphai_sessions_merged.json",
        help="Merged real pick_outcomes sessions (no proxy).",
    )
    p.add_argument("--out", default="artifacts/alphai_pack_compare.json")
    p.add_argument("--md", default="artifacts/alphai_pack_compare.md")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--hour", type=int, default=7, help="AlphaI as-of hour UTC")
    args = p.parse_args()

    cache = Path(args.cache_dir)
    if args.refresh:
        print("refreshing 1d Bitvavo cache…", flush=True)
        meta = refresh_cache(cache)
        print(f"  ok={len(meta['ok'])} failed={meta['failed']}", flush=True)

    ohlc = load_cached(cache)
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {cache}")
    dates = ohlc_dates(ohlc)
    last = dates[-1]

    alphai_path = Path(args.alphai_path)
    if not alphai_path.exists():
        raise SystemExit(
            f"missing {alphai_path} — merge pick_outcomes into data/research first"
        )

    # Strict: only the real AlphaI calendar span ∩ candle availability.
    from bot.research.alphai_pack_compare.alphai_map import (
        load_merged_sessions,
        picks_asof_hour,
    )

    sessions = load_merged_sessions(alphai_path)
    daily = picks_asof_hour(sessions, hour_utc=args.hour)
    if not daily:
        raise SystemExit("no AlphaI pick days in merged sessions")
    start = min(daily)
    end = min(max(daily), last)
    if start > end:
        raise SystemExit(f"no overlap: alphai {start}..{max(daily)} vs candles ..{last}")

    print(
        f"\n== alphai_overlap {start} → {end}  €{args.book:.0f}  "
        f"asof-hour {args.hour:02d}:00 UTC ==",
        flush=True,
    )
    result = run_alphai_pack_compare(
        ohlc,
        start=start,
        end=end,
        book_eur=args.book,
        sessions_path=str(alphai_path),
        decision_hour_utc=args.hour,
        dates=dates,
    )
    print(_line("BEST CALMAR", result.get("best_calmar")))
    print(_line("BEST PNL", result.get("best_pnl")))
    print(_line("LOWEST DD", result.get("lowest_dd")))
    print("-- pareto --", flush=True)
    for row in result.get("pareto") or []:
        print(_line(str(row.get("name")), row))
    print("-- top 10 --", flush=True)
    for row in (result.get("top") or [])[:10]:
        print(_line(str(row.get("name")), row))

    # Slim payload: drop full weeks from every ranked row except winners.
    def slim_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        out = dict(row)
        weeks = out.get("weeks")
        if isinstance(weeks, list) and len(weeks) > 8:
            out["weeks"] = weeks[:2] + weeks[-2:]
            out["weeks_truncated"] = True
        return out

    slim = {
        **result,
        "best_calmar": slim_row(result.get("best_calmar")),
        "best_pnl": slim_row(result.get("best_pnl")),
        "lowest_dd": slim_row(result.get("lowest_dd")),
        "pareto": [slim_row(r) for r in (result.get("pareto") or [])],
        "top": [slim_row(r) for r in (result.get("top") or [])],
        "ranked": [
            {k: v for k, v in r.items() if k != "weeks"} for r in (result.get("ranked") or [])
        ],
        "alphai_best": {
            k: slim_row(v) for k, v in (result.get("alphai_best") or {}).items()
        },
    }
    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "last_bar": last,
        "fill": "wet_next_open_taker",
        "note": (
            "Tournament of residual/clip packs on the real AlphaI pick_outcomes "
            "overlap only. No price-proxy AlphaI. Gate = residual winner must be "
            "an AlphaI pick; intersect = strongest residual name that is a pick."
        ),
        "result": slim,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md = Path(args.md)
    md.write_text(_md(payload), encoding="utf-8")
    # Tracked copies beside the module (artifacts/ is gitignored).
    pkg = Path(__file__).resolve().parent
    (pkg / "RESULTS.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (pkg / "RESULTS.md").write_text(_md(payload), encoding="utf-8")
    print(f"\nwrote {out}", flush=True)
    print(f"wrote {md}", flush=True)
    print(f"wrote {pkg / 'RESULTS.json'}", flush=True)


if __name__ == "__main__":
    main()

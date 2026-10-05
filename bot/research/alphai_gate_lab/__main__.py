"""Scan AlphaI gate variants for residual_full.

Usage::

    .venv/bin/python -m bot.research.alphai_gate_lab
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.alphai_gate_lab.engine import run_gate_lab
from bot.research.alphai_pack_compare.alphai_map import (
    load_merged_sessions,
    picks_asof_hour,
)
from bot.research.alphai_pack_compare.candles import load_cached, ohlc_dates


def _line(label: str, row: dict[str, Any] | None) -> str:
    if not row:
        return f"  {label:<48} (none)"
    return (
        f"  {label:<48} pnl {float(row.get('pnl_eur') or 0):+9.0f}  "
        f"dd {float(row.get('max_dd_pct') or 0) * 100:5.1f}%  "
        f"score {float(row.get('short_score') or 0):+8.0f}  "
        f"trades {int(row.get('n_trades') or 0):3d}  "
        f"hold {row.get('end_hold')}"
    )


def _md(payload: dict[str, Any]) -> str:
    r = payload.get("result") or {}
    cov = r.get("alphai_coverage") or {}
    lines = [
        "# AlphaI gate lab — residual_full",
        "",
        f"asof `{payload.get('asof')}`  window `{r.get('start')}` → `{r.get('end')}`  "
        f"book €{r.get('book_eur'):,.0f}",
        "",
        (
            f"Real AlphaI only: **{cov.get('n_days')} days** "
            f"({cov.get('first_day')} → {cov.get('last_day')}). "
            "Modes: gate / intersect / prefer / overlap_or_rs / excess override. "
            "Allow-sets: as-of 07/13/16, top-k ranks, day union."
        ),
        "",
        "## Baseline vs best gate",
        "",
        _line("off (no AlphaI)", r.get("off")),
        _line("best gated", r.get("best_gate")),
        _line("best that beats off (PnL/DD)", r.get("best_beats_off")),
        "",
        "## Top 15 (short_score = pnl − book×dd)",
        "",
    ]
    for row in (r.get("top") or [])[:15]:
        lines.append(_line(str(row.get("name")), row))
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="AlphaI gate optimization for residual_full")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--alphai-path", default="data/research/alphai_sessions_merged.json")
    p.add_argument("--out", default="artifacts/alphai_gate_lab.json")
    p.add_argument("--md", default="artifacts/alphai_gate_lab.md")
    args = p.parse_args()

    ohlc = load_cached(Path(args.cache_dir))
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {args.cache_dir}")
    dates = ohlc_dates(ohlc)
    last = dates[-1]
    sessions = load_merged_sessions(args.alphai_path)
    daily = picks_asof_hour(sessions, hour_utc=7)
    if not daily:
        raise SystemExit("no AlphaI days")
    start = min(daily)
    end = min(max(daily), last)

    print(f"\n== gate lab {start} → {end}  €{args.book:.0f} ==", flush=True)
    result = run_gate_lab(
        ohlc,
        start=start,
        end=end,
        dates=dates,
        sessions_path=args.alphai_path,
        book_eur=args.book,
    )
    print(_line("OFF", result.get("off")))
    print(_line("BEST GATE", result.get("best_gate")))
    print(_line("BEATS OFF", result.get("best_beats_off")))
    print("-- top 12 --", flush=True)
    for row in (result.get("top") or [])[:12]:
        print(_line(str(row.get("name")), row))

    slim_ranked = []
    for row in result.get("ranked") or []:
        out = {k: v for k, v in row.items() if k != "weeks"}
        slim_ranked.append(out)
    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "last_bar": last,
        "fill": "wet_next_open_taker",
        "note": (
            "residual_full gate optimization on real AlphaI overlap only. "
            "prefer/overlap keep RS when AlphaI disagrees; override keeps RS "
            "when excess clears a threshold; top_k tightens the allow-list."
        ),
        "result": {
            **result,
            "off": {k: v for k, v in (result.get("off") or {}).items() if k != "weeks"},
            "best_gate": {
                k: v for k, v in (result.get("best_gate") or {}).items() if k != "weeks"
            },
            "best_beats_off": (
                {k: v for k, v in (result.get("best_beats_off") or {}).items() if k != "weeks"}
                if result.get("best_beats_off")
                else None
            ),
            "top": [{k: v for k, v in row.items() if k != "weeks"} for row in (result.get("top") or [])],
            "ranked": slim_ranked,
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path = Path(args.md)
    md_path.write_text(_md(payload), encoding="utf-8")
    pkg = Path(__file__).resolve().parent
    (pkg / "RESULTS.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (pkg / "RESULTS.md").write_text(_md(payload), encoding="utf-8")
    print(f"\nwrote {out}", flush=True)
    print(f"wrote {md_path}", flush=True)


if __name__ == "__main__":
    main()

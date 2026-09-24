"""Run the exhaustive owner Calmar tournament.

Usage::

    python -m bot.research.owner_tournament
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_exit_lab.engine import WET
from bot.research.owner_tournament.engine import (
    baseline_rows,
    coarse_specs,
    neighborhood_specs,
    pick_champion,
    rank_rows,
    run_spec_grid,
    year_ok,
)


def load_cached(cache_dir: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        path = cache_dir / f"{base}.json"
        if path.exists():
            out[base] = json.loads(path.read_text())
    return out


def _line(row: dict[str, Any]) -> str:
    yp = row.get("year_pnl") or {}
    years = " ".join(f"{k}:{v:+.0f}" for k, v in yp.items()) if isinstance(yp, dict) else ""
    return (
        f"  {str(row.get('name')):<42} "
        f"pnl {float(row.get('pnl_eur') or 0):+9.0f}  "
        f"dd {float(row.get('max_dd_pct') or 0) * 100:5.1f}%  "
        f"calmar {float(row.get('calmar') or 0):6.3f}  "
        f"{years}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Exhaustive directional owner Calmar tournament")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--out", default="artifacts/owner_tournament.json")
    p.add_argument("--start", default="2024-03-16")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    p.add_argument("--skip-coarse", action="store_true")
    p.add_argument("--skip-fine", action="store_true")
    args = p.parse_args()

    ohlc = load_cached(Path(args.cache_dir))
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {args.cache_dir}")
    last = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    workers = max(1, int(args.workers))
    print(
        f"owner tournament {args.start} → {last}  €{args.book:.0f}  workers={workers}",
        flush=True,
    )

    all_rows: list[dict[str, Any]] = []
    print("  baselines (clip / donchian / btc)", flush=True)
    all_rows.extend(
        baseline_rows(ohlc, start=args.start, end=last, book_eur=args.book, model=WET)
    )

    coarse = coarse_specs()
    if not args.skip_coarse:
        print(f"  coarse residual grid {len(coarse)} packs", flush=True)
        all_rows.extend(
            run_spec_grid(
                ohlc,
                coarse,
                start=args.start,
                end=last,
                book_eur=args.book,
                workers=workers,
            )
        )
    ranked = rank_rows(all_rows)
    champ = pick_champion(ranked)
    print("  coarse top 12", flush=True)
    for row in ranked[:12]:
        print(_line(row), flush=True)
    print(
        f"  champion so far {champ.get('name')}  "
        f"calmar {champ.get('calmar')}  {champ.get('rule')}",
        flush=True,
    )

    if not args.skip_fine:
        seeds = []
        if champ.get("pack"):
            seeds.append(champ["pack"])
        for row in ranked[:5]:
            if row.get("family") == "residual":
                seeds.append(row)
        seen_names = {str(r.get("name")) for r in all_rows}
        fine: list[dict[str, Any]] = []
        for seed in seeds:
            for spec in neighborhood_specs(seed):
                if spec["name"] not in seen_names:
                    seen_names.add(spec["name"])
                    fine.append(spec)
        print(f"  fine neighborhood {len(fine)} packs", flush=True)
        if fine:
            all_rows.extend(
                run_spec_grid(
                    ohlc,
                    fine,
                    start=args.start,
                    end=last,
                    book_eur=args.book,
                    workers=workers,
                )
            )

    ranked = rank_rows(all_rows)
    champ = pick_champion(ranked)
    raw = ranked[0] if ranked else {}
    print("  final top 15", flush=True)
    for row in ranked[:15]:
        print(_line(row), flush=True)
    print(
        f"  BEST (robust) {champ.get('name')} calmar {champ.get('calmar')} "
        f"pnl {champ.get('pnl_eur')} dd {champ.get('max_dd_pct')} {champ.get('rule')}",
        flush=True,
    )
    print(
        f"  BEST (raw calmar) {raw.get('name')} calmar {raw.get('calmar')} "
        f"pnl {raw.get('pnl_eur')} dd {raw.get('max_dd_pct')}",
        flush=True,
    )

    # Recent window only for the robust champion + raw top + clip — not for picking.
    recent_start = "2026-06-26"
    follow = []
    for row in (champ.get("pack"), raw):
        if row and row.get("family") == "residual" and row.get("lookback_days") is not None:
            follow.append(
                {
                    "family": "residual",
                    "name": f"recent_{row['name']}",
                    "btc_frac": row.get("btc_frac") or 0.5,
                    "lookback_days": row.get("lookback_days") or 10,
                    "skip_days": row.get("skip_days") or 1,
                    "trail_pct": row.get("trail_pct") or 0.0,
                    "flatten": "all",
                    "sma_n": row.get("sma_n") or 50,
                    "rebalance_days": row.get("rebalance_days") or 7,
                    "excess_floor": row.get("excess_floor") or 0.0,
                    "n_alts": row.get("n_alts") or 1,
                    "require_alt_sma": bool(row.get("require_alt_sma")),
                }
            )
    recent_rows = []
    if follow:
        print(f"  last_12w check {recent_start} → {last}", flush=True)
        recent_rows = run_spec_grid(
            ohlc,
            follow,
            start=recent_start,
            end=last,
            book_eur=args.book,
            workers=min(workers, 2),
        )
        for row in recent_rows:
            print(_line(row), flush=True)

    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "start": args.start,
        "end": last,
        "book_eur": args.book,
        "fill": "wet_next_open_taker",
        "n_packs": len(ranked),
        "note": (
            "Exhaustive directional owner tournament. Rank by 2.5y Calmar. "
            "Champion requires every calendar year PnL >= 0 and DD <= 50% if any "
            "pack qualifies. AlphaI/15m/HFT/shorts not on this tape. Not armed live."
        ),
        "champion": champ,
        "raw_best": {
            "name": raw.get("name"),
            "calmar": raw.get("calmar"),
            "pnl_eur": raw.get("pnl_eur"),
            "max_dd_pct": raw.get("max_dd_pct"),
            "year_pnl": raw.get("year_pnl"),
            "pack": raw,
        },
        "recent": recent_rows,
        "top": ranked[:40],
        "n_year_positive": sum(1 for r in ranked if year_ok(r)),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()

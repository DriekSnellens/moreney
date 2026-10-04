"""CLI: ``.venv/bin/python -m bot.research.daily_green_lab``."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.research.daily_green_lab.engine import (
    Spec,
    _alphai_daily,
    _load_dir,
    run_daily_green_lab,
    simulate,
    to_markdown,
)
from bot.research.daily_green_lab.optimize import run_optimize
from bot.research.daily_green_lab.optimize import to_markdown as opt_markdown

# Armed pack from walk-forward optimize (see OPTIMIZE.md / SLEEVE.md).
ARMED = Spec(
    name="brk20_day_t12_h5_hs5_btc1_fl0.00",
    pick="brk20_day",
    trail_pct=0.12,
    time_max_days=5,
    hard_stop_pct=0.05,
    require_btc_sma=True,
    excess_floor=0.0,
    lookback=1,
    force_daily=False,
)


def _slim(row: dict) -> dict:
    keep = {
        "ok",
        "name",
        "pick",
        "trail_pct",
        "time_max_days",
        "hard_stop_pct",
        "require_btc_sma",
        "excess_floor",
        "sizing",
        "pnl_eur",
        "end_equity",
        "return_pct",
        "avg_day_pnl",
        "pct_days_green",
        "n_days_green",
        "n_days",
        "pct_days_in_market",
        "n_days_hit_100",
        "best_day",
        "worst_day",
        "max_dd_pct",
        "n_trades",
        "n_weeks",
        "pct_weeks_green",
        "avg_week_pnl",
        "best_week",
        "worst_week",
        "score",
    }
    return {k: row[k] for k in keep if k in row}


def _compound_markdown(payload: dict) -> str:
    lines = [
        "# €1.700 sleeve — fixed book vs compound",
        "",
        f"asof `{payload.get('asof')}`  pack **`{payload.get('pack')}`**  "
        f"universe **{payload.get('n_bases')}**  start book €{payload.get('book_eur'):,.0f}",
        "",
        "Wet next-open, fee 15 bp/side, slip 10 bp, BTC>SMA50.",
        "",
        "| Mode | Size rule |",
        "|---|---|",
        "| `fixed_book_cap` | `notion = min(cash×0.98, €1700)` — live sleeve |",
        "| `compound` | `notion = cash×0.98` — volle equity herbeleggen |",
        "",
        "## Compare",
        "",
        "| Window | Mode | PnL | Return | end eq | avg€/dag | greenW | maxDD | trades |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for wname, block in (payload.get("windows") or {}).items():
        for mode in ("fixed_book_cap", "compound"):
            r = (block.get(mode) or {})
            if not r.get("ok"):
                continue
            lines.append(
                f"| `{wname}` | `{mode}` | "
                f"{float(r.get('pnl_eur') or 0):+.0f} | "
                f"{100 * float(r.get('return_pct') or 0):+.0f}% | "
                f"€{float(r.get('end_equity') or 0):,.0f} | "
                f"{float(r.get('avg_day_pnl') or 0):+.1f} | "
                f"{100 * float(r.get('pct_weeks_green') or 0):.0f}% | "
                f"{100 * float(r.get('max_dd_pct') or 0):.1f}% | "
                f"{int(r.get('n_trades') or 0)} |"
            )
    lines += [
        "",
        "## Compound weeks (last 6w)",
        "",
        "| Week | Fixed € | Compound € |",
        "|---|---:|---:|",
    ]
    fw = ((payload.get("windows") or {}).get("last_6w") or {}).get("fixed_book_cap") or {}
    cw = ((payload.get("windows") or {}).get("last_6w") or {}).get("compound") or {}
    weeks = sorted(
        set((fw.get("weeks") or {}) | (cw.get("weeks") or {})),
        reverse=False,
    )
    for wk in weeks:
        lines.append(
            f"| {wk} | {float((fw.get('weeks') or {}).get(wk, 0)):+.2f} | "
            f"{float((cw.get('weeks') or {}).get(wk, 0)):+.2f} |"
        )
    lines += [
        "",
        "## Note",
        "",
        "Live paper sleeve blijft **fixed_book_cap** (`size_to_book=true`). "
        "Compound is alleen research — na een spike schaalt ticket-size mee "
        "en DD in euro's groeit mee.",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.daily_green_lab --compound",
        "```",
        "",
    ]
    return "\n".join(lines)


def run_compound_compare(
    *,
    candle_dir: str,
    alphai_path: str,
    book: float,
) -> dict:
    ohlc = _load_dir(Path(candle_dir))
    alphai = _alphai_daily(Path(alphai_path)) if Path(alphai_path).exists() else {}
    btc = ohlc.get("BTC") or []
    if not btc:
        raise SystemExit("no BTC candles")
    # Candle ts is ms since epoch (same as engine._by_date).
    end = datetime.fromtimestamp(float(btc[-1][0]) / 1000.0, tz=UTC).strftime("%Y-%m-%d")
    full_start = datetime.fromtimestamp(float(btc[0][0]) / 1000.0, tz=UTC).strftime(
        "%Y-%m-%d"
    )
    # Pin end to last wet research asof when candles run ahead of live desk.
    end = min(end, "2026-10-02")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    windows = {
        "last_6w": ((end_dt - timedelta(days=42)).strftime("%Y-%m-%d"), end),
        "last_3m": ((end_dt - timedelta(days=92)).strftime("%Y-%m-%d"), end),
        "last_1y": ((end_dt - timedelta(days=365)).strftime("%Y-%m-%d"), end),
        "full": (max(full_start, "2024-06-15"), end),
    }
    out_windows: dict = {}
    for wname, (w0, w1) in windows.items():
        block = {}
        for compound in (False, True):
            row = simulate(
                ohlc,
                ARMED,
                start=w0,
                end=w1,
                book=book,
                alphai_by_day=alphai,
                compound=compound,
            )
            key = "compound" if compound else "fixed_book_cap"
            block[key] = row
            print(
                f"{wname} {key}: pnl={row.get('pnl_eur')} "
                f"ret={100 * float(row.get('return_pct') or 0):.0f}% "
                f"DD={100 * float(row.get('max_dd_pct') or 0):.1f}% "
                f"greenW={100 * float(row.get('pct_weeks_green') or 0):.0f}%",
                flush=True,
            )
        out_windows[wname] = {
            "start": w0,
            "end": w1,
            "fixed_book_cap": block["fixed_book_cap"],
            "compound": block["compound"],
            "fixed_book_cap_summary": _slim(block["fixed_book_cap"]),
            "compound_summary": _slim(block["compound"]),
        }
    return {
        "asof": datetime.now(UTC).isoformat(),
        "pack": ARMED.name,
        "book_eur": book,
        "n_bases": len(ohlc),
        "candle_dir": candle_dir,
        "windows": out_windows,
    }


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
    p.add_argument(
        "--compound",
        action="store_true",
        help="Replay armed pack fixed_book_cap vs compound on 6w/3m/1y/full",
    )
    p.add_argument("--workers", type=int, default=None)
    args = p.parse_args()

    pkg = Path(__file__).resolve().parent
    if args.compound:
        print(
            f"daily_green_lab COMPOUND book=€{args.book:.0f} candles={args.candles}",
            flush=True,
        )
        payload = run_compound_compare(
            candle_dir=args.candles,
            alphai_path=args.alphai,
            book=args.book,
        )
        # Slim artifact json (drop heavy daily from nested except last_6w compound)
        slim = {
            "asof": payload["asof"],
            "pack": payload["pack"],
            "book_eur": payload["book_eur"],
            "n_bases": payload["n_bases"],
            "candle_dir": payload["candle_dir"],
            "windows": {},
        }
        for wname, block in payload["windows"].items():
            slim["windows"][wname] = {
                "start": block["start"],
                "end": block["end"],
                "fixed_book_cap": block["fixed_book_cap_summary"],
                "compound": block["compound_summary"],
                "fixed_weeks": (block["fixed_book_cap"] or {}).get("weeks"),
                "compound_weeks": (block["compound"] or {}).get("weeks"),
            }
            if wname == "last_6w":
                slim["windows"][wname]["compound_trades"] = (
                    block["compound"] or {}
                ).get("trades")
                slim["windows"][wname]["fixed_trades"] = (
                    block["fixed_book_cap"] or {}
                ).get("trades")
        md = _compound_markdown(
            {
                **payload,
                "windows": {
                    k: {
                        "fixed_book_cap": {
                            **v["fixed_book_cap_summary"],
                            "weeks": v["fixed_book_cap"].get("weeks"),
                        },
                        "compound": {
                            **v["compound_summary"],
                            "weeks": v["compound"].get("weeks"),
                        },
                    }
                    for k, v in payload["windows"].items()
                },
            }
        )
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        out_json = Path(args.out)
        out_md = Path(args.md)
        if out_json.name == "daily_green_lab.json":
            out_json = Path("artifacts/daily_green_compound.json")
            out_md = Path("artifacts/daily_green_compound.md")
        out_json.write_text(json.dumps(slim, indent=2), encoding="utf-8")
        out_md.write_text(md, encoding="utf-8")
        (pkg / "COMPOUND.json").write_text(json.dumps(slim, indent=2), encoding="utf-8")
        (pkg / "COMPOUND.md").write_text(md, encoding="utf-8")
        moon = Path(__file__).resolve().parents[1] / "moonshot_preimage"
        if moon.is_dir():
            (moon / "COMPOUND.json").write_text(
                json.dumps(slim, indent=2), encoding="utf-8"
            )
            (moon / "COMPOUND.md").write_text(md, encoding="utf-8")
        print(f"wrote {out_md}", flush=True)
        return

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

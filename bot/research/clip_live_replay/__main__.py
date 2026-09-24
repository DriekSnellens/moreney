"""Replay the armed live clip pack on cached Bitvavo 1d bars.

Usage::

    python -m bot.research.clip_live_replay --book 2500 --start 2026-08-24
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import WET
from bot.research.clip_live_replay.engine import live_pack_knobs, run_live_pack


def load_cached(cache_dir: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        path = cache_dir / f"{base}.json"
        if path.exists():
            out[base] = json.loads(path.read_text())
    return out


def _line(label: str, row: dict[str, Any]) -> str:
    return (
        f"  {label:<22} pnl {row['pnl_eur']:+9.2f}  "
        f"end {row['end_eur']:8.2f}  dd {row['max_dd_pct'] * 100:5.1f}%  "
        f"trades {row['n_trades']:3d}  hold {row.get('end_hold')}"
    )


def _svg(curve: list[list[Any]], path: Path) -> None:
    if not curve:
        return
    xs = list(range(len(curve)))
    ys = [float(p[1]) for p in curve]
    w, h, pad = 720, 220, 16
    min_y, max_y = min(ys), max(ys)
    span = max(max_y - min_y, 1.0)

    def sx(i: int) -> float:
        return pad + i / max(len(xs) - 1, 1) * (w - 2 * pad)

    def sy(v: float) -> float:
        return h - pad - (v - min_y) / span * (h - 2 * pad)
    pts = " ".join(f"{sx(i):.1f},{sy(y):.1f}" for i, y in enumerate(ys))
    up = ys[-1] >= ys[0]
    stroke = "#34D399" if up else "#F87171"
    fill = "rgba(52,211,153,.22)" if up else "rgba(248,113,113,.22)"
    start_s = curve[0][0]
    end_s = curve[-1][0]
    lo = min(ys)
    hi = max(ys)
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">'
        f'<rect width="{w}" height="{h}" fill="#0b1220"/>'
        f'<polygon fill="{fill}" points="{pad},{h - pad} {pts} {w - pad},{h - pad}"/>'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="2.4" points="{pts}"/>'
        f'<text x="{pad}" y="18" fill="#9aa4b2" font-size="12" font-family="sans-serif">'
        f"€2,500 clip {start_s}–{end_s}</text>"
        f'<text x="{w - pad}" y="18" fill="{stroke}" font-size="12" '
        f'text-anchor="end" font-family="sans-serif">{ys[-1]:,.2f} €</text>'
        f'<text x="{pad}" y="{h - 4}" fill="#9aa4b2" font-size="11" font-family="sans-serif">'
        f"min {lo:,.0f}  max {hi:,.0f}</text>"
        f"</svg>\n"
    )


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "curve"}


def main() -> None:
    p = argparse.ArgumentParser(description="Wet replay of the armed live clip pack")
    p.add_argument("--book", type=float, default=2500.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--out", default="artifacts/clip_2500_1m.json")
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--days", type=int, default=31)
    args = p.parse_args()

    ohlc = load_cached(Path(args.cache_dir))
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {args.cache_dir}")
    last = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC)
    end = args.end or last.strftime("%Y-%m-%d")
    start = args.start or (last - timedelta(days=max(1, args.days - 1))).strftime("%Y-%m-%d")
    knobs = live_pack_knobs()
    print(f"== live clip pack  {start} → {end}  €{args.book:.0f} ==", flush=True)
    print(
        f"  knobs  {int(knobs['btc_frac'] * 100)}/"
        f"{int((1 - knobs['btc_frac']) * 100)}  "
        f"lb{knobs['lookback_days']} skip{knobs['skip_days']}  "
        f"floor {knobs['excess_floor']:.0%}  "
        f"sma{knobs['sma_n']} flatten={knobs['flatten']}  "
        f"trail {knobs['trail_pct']:.0%}  weekly",
        flush=True,
    )
    live = run_live_pack(ohlc, start=start, end=end, book_eur=args.book, model=WET)
    hold = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=args.book,
        btc_frac=1.0,
        flatten="none",
        model=WET,
        keep_weeks=True,
        strategy="btc_hold",
    )
    sma = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=args.book,
        btc_frac=1.0,
        flatten="all",
        sma_n=50,
        model=WET,
        keep_weeks=True,
        strategy="btc_sma50",
    )
    print(_line("live_clip_pack", live))
    print(_line("btc_hold", hold))
    print(_line("btc_sma50", sma))
    print("  weeks", flush=True)
    for w in live.get("weeks") or []:
        print(
            f"    {w['week']} {w['start']}→{w['end']}  {w['hold']:<22} "
            f"{w['end_eur']:8.2f} {w['pnl_eur']:+8.2f}",
            flush=True,
        )
    context: dict[str, Any] = {}
    last_s = last.strftime("%Y-%m-%d")
    for cname, cstart in (
        ("last_12w", (last - timedelta(days=89)).strftime("%Y-%m-%d")),
        ("last_90d", (last - timedelta(days=90)).strftime("%Y-%m-%d")),
    ):
        crow = run_live_pack(ohlc, start=cstart, end=last_s, book_eur=args.book, model=WET)
        btc = run_btc_residual(
            ohlc,
            start=cstart,
            end=last_s,
            book_eur=args.book,
            btc_frac=1.0,
            flatten="none",
            model=WET,
            strategy="btc_hold",
        )
        context[cname] = {
            "start": cstart,
            "end": last_s,
            "clip": {
                "pnl_eur": crow["pnl_eur"],
                "pnl_pct": crow["pnl_pct"],
                "end_eur": crow["end_eur"],
                "max_dd_pct": crow["max_dd_pct"],
                "n_trades": crow["n_trades"],
                "end_hold": crow.get("end_hold"),
            },
            "btc_hold": {
                "pnl_eur": btc["pnl_eur"],
                "pnl_pct": btc["pnl_pct"],
                "end_eur": btc["end_eur"],
                "max_dd_pct": btc["max_dd_pct"],
            },
        }
        print(_line(f"ctx_{cname}", crow), flush=True)
    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "start": start,
        "end": end,
        "book_eur": args.book,
        "fill": "wet_next_open_taker",
        "note": (
            "Armed live clip pack on Bitvavo 1d tape: 20% BTC / 80% residual alt, "
            "SMA50 flatten all, 10d skip-1 RS, excess floor 4%, 10% alt-trail, weekly. "
            "AlphaI is not on this tape. 15m satellite is not included. "
            "Does not change live bags. One-month Calmar/ann is not a run-rate."
        ),
        "pack": live.get("pack"),
        "live_clip_pack": _strip(live),
        "btc_hold": _strip(hold),
        "btc_sma50": _strip(sma),
        "context": context,
        "curve": live.get("curve"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    svg_path = out.with_suffix(".svg")
    _svg(list(live.get("curve") or []), svg_path)
    print(f"wrote {out}", flush=True)
    print(f"wrote {svg_path}", flush=True)


if __name__ == "__main__":
    main()

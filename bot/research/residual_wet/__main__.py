"""Fetch Bitvavo 1d OHLC and run residual-weekly vs clip, dry vs wet.

Usage::

    python -m bot.research.residual_wet
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.residual_wet.engine import DRY, WET, run_clip, run_residual

BITVAVO_CANDLES = "https://api.bitvavo.com/v2/{market}/candles"
WINDOWS = (
    ("fair_2y", "2024-03-16", "2026-09-19"),
    ("since_2025", "2025-01-01", "2026-09-19"),
    ("last_90d", "2026-06-21", "2026-09-19"),
)


def _ms(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def fetch_daily(
    base: str, start_ms: int, end_ms: int, *, pause_sec: float = 0.2
) -> list[list[float]]:
    rows: dict[int, list[float]] = {}
    cursor = start_ms
    page_ms = 800 * 86_400_000
    while cursor < end_ms:
        page_end = min(end_ms, cursor + page_ms)
        url = (
            f"{BITVAVO_CANDLES.format(market=f'{base}-EUR')}"
            f"?interval=1d&start={cursor}&end={page_end}&limit=1000"
        )
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
                raw = json.load(resp)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            break
        for r in raw:
            rows[int(r[0])] = [
                int(r[0]),
                float(r[1]),
                float(r[2]),
                float(r[3]),
                float(r[4]),
                float(r[5]),
            ]
        cursor = page_end
        time.sleep(pause_sec)
    return [rows[k] for k in sorted(rows)]


def load_ohlc(
    bases: list[str],
    *,
    start: str,
    end: str,
    cache_dir: Path,
    refresh: bool = False,
) -> dict[str, list[list[float]]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    start_ms = _ms(start)
    end_ms = _ms(end) + 86_400_000
    out: dict[str, list[list[float]]] = {}
    for base in bases:
        path = cache_dir / f"{base}.json"
        rows: list[list[float]] = []
        if path.exists() and not refresh:
            rows = json.loads(path.read_text())
        stale_head = rows and int(rows[0][0]) > start_ms + 5 * 86_400_000
        stale_tail = rows and int(rows[-1][0]) < end_ms - 3 * 86_400_000
        need = not rows or stale_head or stale_tail
        if need:
            rows = fetch_daily(base, start_ms, end_ms)
            if rows:
                path.write_text(json.dumps(rows))
        if rows:
            out[base] = rows
        print(f"{base:5s} {len(rows):4d} days", flush=True)
    return out


def _run_window(
    ohlc: dict[str, list[list[float]]], start: str, end: str, book_eur: float
) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "residual_weekly": {
            "dry": run_residual(ohlc, start=start, end=end, book_eur=book_eur, model=DRY),
            "wet": run_residual(ohlc, start=start, end=end, book_eur=book_eur, model=WET),
        },
        "btc_rs_clip": {
            "dry": run_clip(ohlc, start=start, end=end, book_eur=book_eur, model=DRY),
            "wet": run_clip(ohlc, start=start, end=end, book_eur=book_eur, model=WET),
        },
    }


def _line(label: str, row: dict[str, Any]) -> str:
    return (
        f"  {label:<22} pnl {row['pnl_eur']:+9.0f}  "
        f"dd {row['max_dd_pct']*100:5.1f}%  calmar {row['calmar']:5.2f}  "
        f"ann {row['ann_pct']*100:6.1f}%  trades {row['n_trades']:3d}  hold {row.get('end_hold')}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Residual-weekly vs clip, dry vs wet Bitvavo 1d")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--out", default="artifacts/residual_wet_vs_dry.json")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--warmup-days", type=int, default=80)
    args = p.parse_args()

    fetch_start = (
        datetime.strptime(WINDOWS[0][1], "%Y-%m-%d") - timedelta(days=args.warmup_days)
    ).strftime("%Y-%m-%d")
    fetch_end = datetime.now(UTC).strftime("%Y-%m-%d")
    bases = ["BTC", *DEFAULT_UNIVERSE]
    print(f"fetch {fetch_start} → {fetch_end}  n={len(bases)}", flush=True)
    ohlc = load_ohlc(
        bases,
        start=fetch_start,
        end=fetch_end,
        cache_dir=Path(args.cache_dir),
        refresh=args.refresh,
    )
    if "BTC" not in ohlc:
        raise SystemExit("no BTC candles")

    last = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    payload: dict[str, Any] = {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": args.book,
        "last_bar": last,
        "n_bases": len(ohlc),
        "fill_models": {
            "dry": {
                "fill": "completed_close",
                "slip": DRY.base_slip,
                "fee_side": DRY.fee_side,
                "impact_k": DRY.impact_k,
            },
            "wet": {
                "fill": "next_open",
                "slip": WET.base_slip,
                "fee_side": WET.fee_side,
                "impact_k": WET.impact_k,
                "impact_cap": WET.impact_cap,
                "alphai": False,
            },
        },
        "windows": {},
    }
    for name, start, end in WINDOWS:
        use_end = min(end, last)
        print(f"\n== {name} {start} → {use_end} ==", flush=True)
        block = _run_window(ohlc, start, use_end, args.book)
        payload["windows"][name] = block
        for strat in ("residual_weekly", "btc_rs_clip"):
            for model in ("dry", "wet"):
                print(_line(f"{strat}/{model}", block[strat][model]))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()

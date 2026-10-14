"""Run the factor scoreboard on the residual tape.

Usage::

    .venv/bin/python -m bot.research.factor_scoreboard [--refresh-external]

Fresh Bitvavo daily bars are merged into the cached candles in memory only.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.factor_scoreboard.engine import run_grid
from bot.research.factor_scoreboard.external import (
    fear_greed_by_day,
    funding_daily_mean,
    funding_history,
)


def load_candles(cache_dir: Path, *, merge_fresh: bool) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        path = cache_dir / f"{base}.json"
        if path.exists():
            out[base] = json.loads(path.read_text())
    if not merge_fresh:
        return out
    for base in list(out):
        url = f"https://api.bitvavo.com/v2/{base}-EUR/candles?interval=1d&limit=40"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310
                rows = json.load(resp)
        except Exception:  # noqa: BLE001
            continue
        have = {int(r[0]): r for r in out[base]}
        for r in rows:
            ts = int(r[0])
            have[ts] = [ts, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        out[base] = [have[k] for k in sorted(have)]
        time.sleep(0.03)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Residual factor scoreboard")
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--start", default="2024-03-16")
    p.add_argument("--is-end", default="2025-12-31")
    p.add_argument("--oos-start", default="2026-01-01")
    p.add_argument("--out", default="artifacts/factor_scoreboard.json")
    p.add_argument("--no-fresh", action="store_true", help="do not merge fresh Bitvavo bars")
    p.add_argument("--refresh-external", action="store_true")
    p.add_argument("--no-external", action="store_true", help="skip Fear & Greed and funding")
    args = p.parse_args()

    ohlc = load_candles(Path(args.cache_dir), merge_fresh=not args.no_fresh)
    if "BTC" not in ohlc:
        raise SystemExit("no BTC candles")
    end = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    fng = fund = None
    if not args.no_external:
        try:
            fng = fear_greed_by_day(refresh=args.refresh_external)
            fund = funding_daily_mean(
                funding_history(("BTC", *DEFAULT_UNIVERSE), refresh=args.refresh_external)
            )
        except Exception as exc:  # noqa: BLE001
            print(f"external data unavailable: {exc}")
    windows = (
        ("full", args.start, end),
        ("is", args.start, args.is_end),
        ("oos", args.oos_start, end),
    )
    rows = run_grid(ohlc, fng=fng, fund=fund, windows=windows, book_eur=args.book, log=print)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "asof": datetime.now(UTC).isoformat(),
                "last_bar": end,
                "book_eur": args.book,
                "windows": windows,
                "rows": rows,
            },
            indent=1,
        )
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

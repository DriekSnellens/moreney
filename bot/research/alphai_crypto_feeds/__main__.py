"""Fetch the AlphaI crypto archive, record radar and sentiment, replay residual.

Usage::

    .venv/bin/python -m bot.research.alphai_crypto_feeds
    .venv/bin/python -m bot.research.alphai_crypto_feeds --replay-only
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.core.config import get_settings
from bot.integrations.alphai.client import AlphaIClient
from bot.integrations.alphai.regime import _resolve_api_key
from bot.research.alphai_crypto_feeds.archive import (
    ARCHIVE_PATH,
    LEDGER_PATH,
    fetch_archive,
    load_archive,
    record_forward,
    save_archive,
)
from bot.research.alphai_crypto_feeds.features import publication_date
from bot.research.alphai_crypto_feeds.replay import run_replay
from bot.research.factor_scoreboard.__main__ import load_candles


def _client() -> AlphaIClient:
    key = _resolve_api_key(get_settings())
    if not key:
        raise SystemExit("AlphaI API key is not configured")
    return AlphaIClient(key)


def _overlap(archive: dict[str, Any], tape_end: str) -> tuple[str, str]:
    dates = [
        day
        for row in archive.get("rows") or []
        if (day := publication_date(row.get("published")))
    ]
    if not dates:
        raise SystemExit("archive has no published dates")
    start = min(dates)
    end = min(max(dates), tape_end)
    return start, end


def _line(row: dict[str, Any] | None) -> str:
    if not row:
        return "(none)"
    pnl = float(row.get("pnl_eur") or 0.0)
    dd = float(row.get("max_dd_pct") or 0.0) * 100.0
    trades = int(row.get("n_trades") or 0)
    return f"pnl {pnl:+.0f}  dd {dd:5.1f}%  trades {trades:3d}  hold {row.get('end_hold')}"


def _markdown(
    archive: dict[str, Any],
    replay: dict[str, Any],
    forward: dict[str, Any] | None,
) -> str:
    off = replay.get("off") or {}
    lines = [
        "# AlphaI crypto feeds — residual overlap",
        "",
        (
            f"Archive `{archive.get('from_date')}` → `{archive.get('to_date')}`, "
            f"{archive.get('n_rows')} crypto stories, complete `{archive.get('complete')}`."
        ),
        (
            f"Published `{archive.get('first_published')}` → `{archive.get('last_published')}`. "
            f"Replay `{replay.get('start')}` → `{replay.get('end')}`, "
            f"book €{replay.get('book_eur'):,.0f}, wet next-open, live residual knobs."
        ),
        "",
        "Radar and the seven-day sentiment summary are snapshots. They are recorded",
        "on the forward ledger and are not in this replay. AlphaI's own docs say the",
        "radar is not a point-in-time backtest. The news archive does not reach",
        "before 2026, so this overlap cannot clear the two-period bar.",
        "",
        "A day with no archived story is left on the residual rank. A covered day",
        "with no buy name goes to cash under gate, intersect and prefer. Avoid",
        "blocks only names the live scorer marks avoid. Nothing is armed.",
        "",
        "## Residual, no AlphaI",
        "",
        f"- off: {_line(off)}",
        "",
        "## Archived news on the same window",
        "",
        "| Cell | PnL | maxDD | trades | covered days | empty buy days |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for cell in replay.get("cells") or []:
        lines.append(
            "| {name} | {pnl:+.0f} | {dd:.1f}% | {trades} | {covered} | {empty} |".format(
                name=cell["name"],
                pnl=float(cell.get("pnl_eur") or 0.0),
                dd=float(cell.get("max_dd_pct") or 0.0) * 100.0,
                trades=int(cell.get("n_trades") or 0),
                covered=int(cell.get("covered_days") or 0),
                empty=int(cell.get("empty_buy_days") or 0),
            )
        )
    off_pnl = float(off.get("pnl_eur") or 0.0)
    off_trades = int(off.get("n_trades") or 0)
    cells = list(replay.get("cells") or [])
    changed = [
        cell
        for cell in cells
        if abs(float(cell.get("pnl_eur") or 0.0) - off_pnl) > 0.5
        or int(cell.get("n_trades") or 0) != off_trades
    ]
    if not changed:
        changed_bit = "Every cell matched the ungated book."
    else:
        best = max(changed, key=lambda cell: float(cell.get("pnl_eur") or 0.0))
        best_pnl = float(best.get("pnl_eur") or 0.0)
        if best_pnl > off_pnl + 0.5:
            verdict = "It finishes ahead of the ungated book on this overlap only."
        else:
            verdict = "It does not beat the ungated book."
        changed_bit = (
            f"The highest cell that changes a fill is {best['name']} at "
            f"{best_pnl:+.0f} "
            f"(drawdown {float(best.get('max_dd_pct') or 0.0) * 100:.1f}%). "
            f"{verdict}"
        )
    lines.extend(
        [
            "",
            "## Reading",
            "",
            (
                f"Ungated residual on this overlap finished {off_pnl:+.0f}. "
                f"{changed_bit} "
                "Rules are the live daily-recommendation scorer over the trailing "
                "1, 3 and 7 publication days, locked before the replay. "
                "The Basic plan keeps 90 days and refuses the boundary date, so "
                "2024–2025 is absent and the two-period bar is not in play. "
                "Nothing is armed."
            ),
            "",
            "## Forward snapshot",
            "",
        ]
    )
    if not forward:
        lines.append("No forward snapshot on this run.")
    else:
        radar = forward.get("radar") or {}
        counts = ", ".join(f"{key}={row.get('n')}" for key, row in radar.items())
        lines.append(
            f"Radar rows: {counts or 'none'}. "
            f"Sentiment tickers: {len(forward.get('sentiment') or {})}. "
            f"Errors: {len(forward.get('errors') or [])}."
        )
        if archive.get("error"):
            lines.extend(["", f"Archive error: `{archive.get('error')}`"])
    lines.extend(
        [
            "",
            "Reproduce:",
            "",
            "```bash",
            ".venv/bin/python -m bot.research.alphai_crypto_feeds",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaI crypto feeds vs residual_full")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--cache-dir", default="data/residual_wet_candles")
    parser.add_argument("--archive", default=str(ARCHIVE_PATH))
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    parser.add_argument("--out", default="bot/research/alphai_crypto_feeds/RESULTS.md")
    parser.add_argument("--book", type=float, default=20_000.0)
    parser.add_argument("--max-calls", type=int, default=700)
    parser.add_argument("--skip-forward", action="store_true")
    args = parser.parse_args()

    archive_path = Path(args.archive)
    forward: dict[str, Any] | None = None
    if args.replay_only:
        archive = load_archive(archive_path)
        ledger = Path(args.ledger)
        if ledger.exists():
            last = ledger.read_text(encoding="utf-8").strip().splitlines()[-1]
            forward = json.loads(last)
    else:
        client = _client()
        archive = fetch_archive(client, max_calls=args.max_calls)
        save_archive(archive, archive_path)
        print(
            f"archive rows {archive.get('n_rows')} complete {archive.get('complete')} "
            f"remaining {archive.get('rate_limit_remaining')}",
            flush=True,
        )
        if args.skip_forward:
            ledger = Path(args.ledger)
            if ledger.exists():
                last = ledger.read_text(encoding="utf-8").strip().splitlines()[-1]
                forward = json.loads(last)
        else:
            forward = record_forward(client, path=Path(args.ledger))

    ohlc = load_candles(Path(args.cache_dir), merge_fresh=True)
    if "BTC" not in ohlc:
        raise SystemExit(f"no BTC candles in {args.cache_dir}")
    tape_end = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if tape_end == today and len(ohlc["BTC"]) > 1:
        tape_end = datetime.fromtimestamp(int(ohlc["BTC"][-2][0]) / 1000, UTC).strftime(
            "%Y-%m-%d"
        )
    start, end = _overlap(archive, tape_end)
    print(f"replay {start} → {end}", flush=True)
    replay = run_replay(
        ohlc,
        list(archive.get("rows") or []),
        start=start,
        end=end,
        book_eur=args.book,
    )
    print(f"off {_line(replay.get('off'))}", flush=True)
    for cell in replay["cells"]:
        print(f"{cell['name']} {_line(cell)} covered {cell['covered_days']}", flush=True)
    text = _markdown(archive, replay, forward)
    out = Path(args.out)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()

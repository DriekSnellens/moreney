#!/usr/bin/env python3
"""Check AlphaI symbol coverage for Moreney liquid EUR bases.

Requires ALPHAI_API_KEY (https://alphai.io/developers).

Usage:
  ALPHAI_API_KEY=ak_live_... PYTHONPATH=. python scripts/check_alphai_symbol_coverage.py
  ALPHAI_API_KEY=... python scripts/check_alphai_symbol_coverage.py --json-out data/research/alphai_symbol_coverage.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.integrations.alphai.client import AlphaIClient  # noqa: E402
from bot.integrations.alphai.symbols import (  # noqa: E402
    LIQUID_EUR_BASES,
    alphai_candidates_for_base,
    alphai_crypto_ticker,
)


@dataclass
class SymbolCheck:
    base: str
    alphai_ticker: str
    status: str  # found | not_found | error
    asset_type: str | None = None
    name: str | None = None
    note: str | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write full report JSON here",
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=Path("data/alphai/symbol_cache.json"),
        help="Symbol list cache (one /api/symbols/ call when stale)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ALPHAI_API_KEY", "").strip()
    if not api_key:
        print(
            "ERROR: set ALPHAI_API_KEY (https://alphai.io/developers → Get API key)",
            file=sys.stderr,
        )
        return 2

    client = AlphaIClient(api_key)
    crypto = client.load_or_fetch_symbol_cache(args.cache_path)
    results: list[SymbolCheck] = []

    for base in LIQUID_EUR_BASES:
        ticker = alphai_crypto_ticker(base)
        row = SymbolCheck(base=base, alphai_ticker=ticker, status="not_found")
        for cand in alphai_candidates_for_base(base):
            if cand in crypto:
                row.status = "found"
                row.alphai_ticker = cand
                detail = client.get_symbol(cand)
                if isinstance(detail, dict):
                    row.asset_type = str(detail.get("asset_type") or "")
                    row.name = str(detail.get("name") or "")[:60]
                break
        if row.status == "not_found":
            row.note = "not in cached /api/symbols/ crypto list"
        results.append(row)

    found = [r for r in results if r.status == "found"]
    missing = [r for r in results if r.status == "not_found"]
    errors = [r for r in results if r.status == "error"]

    print(f"AlphaI coverage: {len(found)}/{len(results)} bases found")
    print(f"rate_limit_remaining={client.last_rate_limit.remaining}\n")
    print(f"{'BASE':<8} {'AlphaI':<14} {'TYPE':<8} NAME")
    print("-" * 72)
    for r in sorted(results, key=lambda x: (x.status != "found", x.base)):
        print(
            f"{r.base:<8} {r.alphai_ticker:<14} "
            f"{(r.asset_type or '-'):<8} "
            f"{(r.name or r.note or '')[:40]}"
        )

    if missing:
        print("\nNot in AlphaI:", ", ".join(r.base for r in missing))
    if errors:
        print("\nErrors:", ", ".join(f"{r.base}({r.note})" for r in errors))

    report = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(results),
        "found": len(found),
        "rate_limit_remaining": client.last_rate_limit.remaining,
        "not_found": [asdict(r) for r in missing],
        "errors": [asdict(r) for r in errors],
        "all": [asdict(r) for r in results],
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json_out}")

    return 0 if not missing and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

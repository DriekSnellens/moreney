#!/usr/bin/env python3
"""Seed today's AlphaI daily crypto buy recommendations (force refresh).

Usage:
  ALPHAI_API_KEY=... PYTHONPATH=. python scripts/seed_daily_crypto_recommendations.py
  python scripts/seed_daily_crypto_recommendations.py --json-out data/alphai/daily_recommendations.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.core.config import Settings  # noqa: E402
from bot.integrations.alphai.client import AlphaIClient  # noqa: E402
from bot.integrations.alphai.daily_recommendations import (  # noqa: E402
    maybe_refresh_daily,
    save_daily_recommendations,
)
from bot.integrations.alphai.regime import _parse_csv_bases  # noqa: E402
from bot.integrations.alphai.symbols import LIQUID_EUR_BASES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Override output path (default from settings)",
    )
    args = parser.parse_args()

    settings = Settings()
    key = None
    if settings.alphai_api_key is not None:
        key = settings.alphai_api_key.get_secret_value()
    key = (key or os.environ.get("ALPHAI_API_KEY") or "").strip()
    if not key:
        print("ERROR: set ALPHAI_API_KEY in .env or environment", file=sys.stderr)
        return 2

    out_path = args.json_out or Path(
        getattr(
            settings,
            "alphai_daily_recommendations_path",
            "data/alphai/daily_recommendations.json",
        )
    )
    focus = _parse_csv_bases(
        getattr(settings, "live_micro_focus_bases", "") or "",
        fallback=set(LIQUID_EUR_BASES),
    )
    client = AlphaIClient(key)
    report = maybe_refresh_daily(
        client,
        out_path,
        focus_bases=focus,
        enabled=True,
        min_relevance=int(
            getattr(settings, "alphai_daily_recommendations_min_relevance", 6) or 6
        ),
        top_n=int(getattr(settings, "alphai_daily_recommendations_top_n", 8) or 8),
        update_hour_local=int(
            getattr(settings, "alphai_daily_recommendations_hour", 12) or 12
        ),
        interval_minutes=int(
            getattr(settings, "alphai_recommendations_interval_minutes", 15) or 15
        ),
        interval_hours=int(
            getattr(settings, "alphai_recommendations_interval_hours", 1) or 1
        ),
        macro_caution=False,
        force=True,
    )
    if not report:
        print("ERROR: generation failed", file=sys.stderr)
        return 1
    if args.json_out:
        save_daily_recommendations(args.json_out, report)

    picks = report.get("picks") or []
    avoid = report.get("avoid") or []
    print(f"Session {report.get('session_id')} · {len(picks)} buy picks · quota remaining {report.get('rate_limit_remaining')}\n")
    print(f"{'#':<3} {'BASE':<8} {'SCORE':<8} NOTE")
    print("-" * 72)
    for p in picks:
        print(
            f"{p.get('rank', '-'):<3} {p.get('base', ''):<8} {p.get('score', ''):<8} "
            f"{(p.get('note') or '')[:48]}"
        )
    if avoid:
        print("\nAvoid:", ", ".join(str(a.get("base")) for a in avoid if isinstance(a, dict)))
    print(f"\nWrote {out_path}")
    print(json.dumps({"session_id": report.get("session_id"), "picks": picks[:5]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

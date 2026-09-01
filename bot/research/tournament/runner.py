"""CLI: python -m bot.research.tournament.runner"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.tournament.engine import run_tournament


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Strategy Research Tournament")
    p.add_argument("--path", type=Path, default=Path("data/research_marketdata"))
    p.add_argument(
        "--readiness",
        type=Path,
        default=Path("data/market_data_research_report.json"),
    )
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--max-events", type=int, default=None)
    p.add_argument("--stride", type=int, default=1)
    args = p.parse_args(argv)

    results = run_tournament(
        research_path=args.path,
        readiness_report=args.readiness,
        out_dir=args.out,
        max_events=args.max_events,
        stride=args.stride,
    )

    board = []
    for row in results.get("scoreboard") or []:
        sid = row["STRATEGY"]
        cand = (results.get("candidates") or {}).get(sid) or {}
        board.append(
            {
                "strategy": sid,
                "VERDICT": row.get("VERDICT"),
                "FAILED_GATE": row.get("FAILED_GATE"),
                "DEV_SIGNALS": row.get("DEV_SIGNALS"),
                "OOS_SIGNALS": row.get("OOS_SIGNALS"),
                "EXPECTED_NET": row.get("EXPECTED_NET"),
                "EXECUTION_NET": row.get("EXECUTION_NET"),
                "notes": (cand.get("notes") or [])[:2],
            }
        )

    print(
        json.dumps(
            {
                "STATUS": results.get("STATUS"),
                "DATASET_ID": results.get("DATASET_ID"),
                "DATA_DURATION": results.get("DATA_DURATION"),
                "DEVELOPMENT_WINDOW": results.get("DEVELOPMENT_WINDOW"),
                "FREEZE_BOUNDARY": results.get("FREEZE_BOUNDARY"),
                "OOS_WINDOW": results.get("OOS_WINDOW"),
                "TOURNAMENT": board,
                "PAPER_CANDIDATES": results.get("PAPER_CANDIDATES"),
                "ALL_STRATEGIES_REJECTED": results.get("ALL_STRATEGIES_REJECTED"),
                "PERFORMANCE": results.get("PERFORMANCE"),
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

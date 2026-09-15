"""CLI: python -m bot.research.forensics.runner"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.forensics.engine import run_forensics


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Concentration forensics (research only)")
    p.add_argument("--path", type=Path, default=Path("data/research_marketdata"))
    p.add_argument(
        "--tournament",
        type=Path,
        default=Path("data/research_tournament_report.json"),
    )
    p.add_argument("--out", type=Path, default=Path("data/research_forensics"))
    p.add_argument(
        "--docs",
        type=Path,
        default=Path("docs/CONCENTRATION_FORENSICS_REPORT.md"),
    )
    p.add_argument("--max-events", type=int, default=None)
    p.add_argument("--stride", type=int, default=4)
    p.add_argument("--no-llm", action="store_true")
    args = p.parse_args(argv)

    result = run_forensics(
        research_path=args.path,
        tournament_report=args.tournament,
        out_dir=args.out,
        docs_path=args.docs,
        max_events=args.max_events,
        stride=args.stride,
        llm_enabled=not args.no_llm,
    )
    print(
        json.dumps(
            {
                "DATASET": result.get("DATASET"),
                "STRATEGIES_ANALYZED": result.get("STRATEGIES_ANALYZED"),
                "CROSS_VENUE_DISLOCATION": result.get("CROSS_VENUE_DISLOCATION"),
                "SHORT_HORIZON_MEAN_REVERSION": result.get("SHORT_HORIZON_MEAN_REVERSION"),
                "NEW_HYPOTHESES_CREATED": result.get("NEW_HYPOTHESES_CREATED"),
                "LLM_USED": result.get("LLM_USED"),
                "PRODUCTION_TRADING_CHANGED": False,
                "NEXT_RESEARCH_ACTION": result.get("NEXT_RESEARCH_ACTION"),
                "STATUS": result.get("STATUS"),
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

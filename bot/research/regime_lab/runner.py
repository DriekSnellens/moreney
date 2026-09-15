"""CLI: python -m bot.research.regime_lab.runner"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.regime_lab.engine import run_regime_lab


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Regime hypothesis lab (H-0005 / H-0007)")
    p.add_argument("--path", type=Path, default=Path("data/research_marketdata"))
    p.add_argument(
        "--readiness",
        type=Path,
        default=Path("data/market_data_research_report.json"),
    )
    p.add_argument("--out", type=Path, default=Path("data/regime_hypothesis_lab"))
    p.add_argument("--max-events", type=int, default=None)
    p.add_argument("--stride", type=int, default=4)
    p.add_argument("--no-llm", action="store_true")
    args = p.parse_args(argv)
    result = run_regime_lab(
        research_path=args.path,
        readiness_report=args.readiness,
        out_dir=args.out,
        max_events=args.max_events,
        stride=args.stride,
        llm_enabled=not args.no_llm,
    )
    print(
        json.dumps(
            {
                "H-0005": _card(result.get("H-0005") or {}),
                "H-0007": _card(result.get("H-0007") or {}),
                "CONTROL_RESULTS": result.get("CONTROL_RESULTS"),
                "LLM_USED": result.get("LLM_USED"),
                "NEW_HYPOTHESES": result.get("NEW_HYPOTHESES"),
                "PRODUCTION_EXECUTION": "DISABLED",
                "NEXT_ACTION": result.get("NEXT_ACTION"),
                "DATA_STATUS": result.get("DATA_STATUS"),
                "STATUS": result.get("STATUS"),
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def _card(c: dict) -> dict:
    return {
        "DATA_STATUS": c.get("DATA_STATUS"),
        "DEV_RESULT": c.get("DEV_RESULT"),
        "OOS_RESULT": c.get("OOS_RESULT"),
        "VERDICT": c.get("VERDICT"),
        "NET/fill": c.get("NET/fill"),
        "SAMPLE_COUNT": c.get("SAMPLE_COUNT"),
        "STABILITY": c.get("STABILITY"),
        "TOP_CONCENTRATION": c.get("TOP_CONCENTRATION"),
    }


if __name__ == "__main__":
    raise SystemExit(main())

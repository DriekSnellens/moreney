"""CLI: python -m bot.research.robustness.runner"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.robustness.engine import run_robustness_lab


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Edge robustness lab (H-0005 / H-0007)")
    p.add_argument("--path", type=Path, default=Path("data/research_marketdata"))
    p.add_argument(
        "--first-lab",
        type=Path,
        default=Path("data/regime_hypothesis_lab/results.json"),
    )
    p.add_argument("--out", type=Path, default=Path("data/edge_robustness_lab"))
    p.add_argument("--max-events", type=int, default=None)
    p.add_argument("--stride", type=int, default=4)
    args = p.parse_args(argv)
    result = run_robustness_lab(
        research_path=args.path,
        first_lab_results=args.first_lab,
        out_dir=args.out,
        max_events=args.max_events,
        stride=args.stride,
    )
    print(json.dumps(_final(result), indent=2, sort_keys=True, default=str))
    return 0


def _final(result: dict) -> dict:
    h5 = result.get("H-0005") or {}
    h7 = result.get("H-0007") or {}
    return {
        "ACCOUNTING_AUDIT": result.get("ACCOUNTING_AUDIT"),
        "H-0005": _card(h5),
        "H-0007": _card7(h7),
        "PRODUCTION_EXECUTION": "DISABLED",
    }


def _card(c: dict) -> dict:
    return {
        "MECHANICAL_VERDICT": c.get("MECHANICAL_VERDICT"),
        "INTERPRETATION_VERDICT": c.get("INTERPRETATION_VERDICT"),
        "GATE_SELECTIVITY": c.get("GATE_SELECTIVITY"),
        "INCREMENTAL_VALUE": c.get("INCREMENTAL_VALUE"),
        "EDGE_TO_COST_RATIO": c.get("EDGE_TO_COST_RATIO"),
        "MODEL_UNCERTAINTY": (c.get("MODEL_UNCERTAINTY") or {}).get(
            "EDGE_TO_MODEL_UNCERTAINTY_RATIO"
        ),
        "BREAK_EVEN_ADVERSE": c.get("BREAK_EVEN_ADVERSE"),
        "BREAK_EVEN_FEES": c.get("BREAK_EVEN_FEES"),
        "BREAK_EVEN_SLIPPAGE": c.get("BREAK_EVEN_SLIPPAGE"),
        "INDEPENDENT_OOS_WINDOWS": c.get("INDEPENDENT_OOS_WINDOWS"),
        "REPLICATION_STATUS": c.get("REPLICATION_STATUS"),
        "FINAL_RESEARCH_DECISION": c.get("FINAL_RESEARCH_DECISION"),
    }


def _card7(c: dict) -> dict:
    d = _card(c)
    d["REGIME_DIVERSITY"] = c.get("REGIME_DIVERSITY")
    return d


if __name__ == "__main__":
    raise SystemExit(main())

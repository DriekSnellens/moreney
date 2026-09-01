"""CLI: python -m bot.research.autonomous.runner"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.core.config import get_settings
from bot.research.autonomous.director import ResearchDirector
from bot.research.llm.ollama import build_provider_from_settings
from bot.research.llm.provider import FakeResearchLLMProvider


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Autonomous local LLM research director")
    p.add_argument("--dataset-id", type=str, default=None)
    p.add_argument("--path", type=Path, default=Path("data/research_marketdata"))
    p.add_argument(
        "--readiness",
        type=Path,
        default=Path("data/market_data_research_report.json"),
    )
    p.add_argument("--max-hypotheses", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--llm-disabled", action="store_true")
    p.add_argument("--analyze-existing", action="store_true")
    p.add_argument(
        "--fake-llm",
        action="store_true",
        help="Use built-in fake provider (tests / offline demo)",
    )
    args = p.parse_args(argv)

    settings = get_settings()
    provider = None
    if args.fake_llm or args.llm_disabled:
        # llm-disabled still constructs director; fake only when requested
        if args.fake_llm:
            from bot.research.llm.schemas import HypothesisBatch

            provider = FakeResearchLLMProvider(
                responses=[
                    {
                        "analysis": {
                            "observations": [
                                {
                                    "evidence_id": "prior",
                                    "observation": "All prior families rejected on costs or OOS",
                                    "confidence": "medium",
                                }
                            ]
                        },
                        "hypotheses": [
                            {
                                "title": "Slow-horizon imbalance after dislocation",
                                "mechanism": (
                                    "Order book imbalance may forecast short-horizon "
                                    "reversion only when cross-venue dislocation is elevated"
                                ),
                                "why_now": "Prior imbalance alone was cost-negative",
                                "not_equivalent_to": [],
                                "difference_from_prior_failures": (
                                    "Adds dislocation conditioning not tested as primary gate"
                                ),
                                "strategy_family": "order_book_imbalance",
                                "required_features": ["depth_imbalance", "spread"],
                                "required_horizons_ms": [1000, 2000],
                                "signal_concept": "Imbalance signal only when spread stressed",
                                "expected_failure_modes": ["still cost-negative", "concentrated"],
                                "economic_mechanism": "Predictive edge must exceed taker fees",
                                "execution_assumption": "trade_through_conservative",
                                "information_value": "HIGH",
                                "priority": 1,
                                "what_we_learn_if_fails": (
                                    "Conditioning imbalance on dislocation does not clear costs"
                                ),
                            }
                        ],
                    }
                ]
            )

    director = ResearchDirector(settings=settings, provider=provider)
    if provider is None and not args.llm_disabled:
        director.provider = build_provider_from_settings(settings)

    report = director.run(
        dataset_id=args.dataset_id,
        research_path=args.path,
        readiness_report=args.readiness,
        dry_run=args.dry_run,
        llm_disabled=args.llm_disabled,
        analyze_existing=args.analyze_existing,
        max_hypotheses=args.max_hypotheses,
    )
    print(
        json.dumps(
            {
                "LLM_STATUS": report.get("LLM_STATUS"),
                "model": report.get("model"),
                "dry_run": report.get("dry_run"),
                "hypotheses_proposed": report.get("hypotheses_proposed"),
                "rejected_duplicate": report.get("rejected_duplicate"),
                "rejected_validator": report.get("rejected_validator"),
                "accepted_for_experiment": report.get("accepted_for_experiment"),
                "experiments_completed": report.get("experiments_completed"),
                "multiple_testing_exposure": report.get("multiple_testing_exposure"),
                "PAPER_CANDIDATES": ((report.get("tournament") or {}).get("PAPER_CANDIDATES")),
                "note": report.get("note"),
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

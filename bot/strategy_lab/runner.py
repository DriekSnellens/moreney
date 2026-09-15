"""CLI: python -m bot.strategy_lab.runner"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.strategy_lab.tournament import run_tournament


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategy Research Lab tournament")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("data/research_marketdata"),
        help="Research market-data root",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory (default data/strategy_lab/<dataset_id>)",
    )
    parser.add_argument("--dataset-id", type=str, default=None)
    parser.add_argument(
        "--no-synthetic",
        action="store_true",
        help="Fail instead of using synthetic tape when observed tape is thin",
    )
    parser.add_argument("--cycles", type=int, default=80, help="Synthetic cycle count")
    parser.add_argument("--dev-frac", type=float, default=0.70)
    parser.add_argument(
        "--max-events",
        type=int,
        default=120_000,
        help="Max OBSERVED events to stream (memory bound; default 120000)",
    )
    parser.add_argument("--stride", type=int, default=1, help="Keep every Nth JSONL line")
    parser.add_argument(
        "--outcome-mode",
        choices=("trade_through", "shadow"),
        default="trade_through",
        help="Accept outcomes: trade_through haircut (default) or shadow expected NET",
    )
    args = parser.parse_args(argv)

    results = run_tournament(
        dataset_id=args.dataset_id,
        research_path=args.path,
        out_dir=args.out,
        use_synthetic_if_thin=not args.no_synthetic,
        n_synthetic_cycles=args.cycles,
        development_frac=args.dev_frac,
        max_events=args.max_events,
        stride=args.stride,
        outcome_mode=args.outcome_mode,
    )
    print(json.dumps(
        {
            "dataset_id": results["dataset_id"],
            "data_label": results["data_label"],
            "fingerprint": results["fingerprints"]["tournament"],
            "leaderboard": [
                {
                    "strategy": r["strategy"],
                    "verdict": r["verdict"],
                    "net": r["net"],
                    "oos_net": r["oos_net"],
                    "velocity": r["capital_velocity"],
                }
                for r in results["leaderboard"]
            ],
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

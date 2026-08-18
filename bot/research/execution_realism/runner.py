"""CLI: python -m bot.research.execution_realism.runner"""

from __future__ import annotations

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description="Execution realism and counterfactual validation lab")
    p.add_argument("--mode", choices=("screen", "full", "stress"), default="screen")
    p.add_argument("--strategies", default="H-0005")
    p.add_argument("--max-events", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    print(f"EXECUTION_REALISM_LAB mode={args.mode} strategies={args.strategies}")
    print("PRODUCTION_EXECUTION: DISABLED")
    if args.dry_run:
        print("DRY_RUN: would run execution realism simulation")
        return
    from bot.research.execution_realism.engine import run_execution_realism

    result = run_execution_realism(
        mode=args.mode,
        strategies=args.strategies.split(","),
        max_events=args.max_events,
    )
    print(f"VERDICT: {result.get('VERDICT')}")
    print(f"CANONICAL_REPLAY_NET: {result.get('CANONICAL_REPLAY_NET')}")
    print(f"REALISTIC_EXECUTION_NET: {result.get('REALISTIC_EXECUTION_NET')}")
    print(f"DELTA: {result.get('DELTA')}")
    print(f"FILL_SURVIVAL_PCT: {result.get('FILL_SURVIVAL_PCT')}")
    print(f"NEW_STRATEGIES_CREATED: []")
    print(f"PRODUCTION_EXECUTION: DISABLED")


if __name__ == "__main__":
    main()

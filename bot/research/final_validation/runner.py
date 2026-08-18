"""CLI: python -m bot.research.final_validation.runner"""

from __future__ import annotations

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description="Final validation of parent cross-venue dislocation")
    p.add_argument("--run-id", default=None)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--max-events", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    print("CROSS_VENUE_DISLOCATION_FINAL_VALIDATION")
    print("PRODUCTION_EXECUTION: DISABLED")
    print("HYPOTHESIS_GENERATOR_ENABLED: False")
    if args.dry_run:
        print("DRY_RUN: would replay frozen 5-scenario matrix on parent universe")
        return
    from bot.research.final_validation.engine import run_final_validation

    result = run_final_validation(run_id=args.run_id, resume=args.resume, max_events=args.max_events)
    print()
    print(f"FINAL_VALIDATION_VERDICT: {result.get('FINAL_VALIDATION_VERDICT')}")
    print()
    print("WHY:")
    for line in result.get("WHY") or []:
        print(f"- {line}")
    print()
    print(f"NEXT_ACTION: {result.get('NEXT_ACTION')}")
    print()
    print("PRODUCTION_EXECUTION: DISABLED")
    print(f"NEW_STRATEGIES_CREATED: {result.get('NEW_STRATEGIES_CREATED')}")


if __name__ == "__main__":
    main()

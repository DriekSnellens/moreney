"""CLI: python -m bot.research.execution_realism.runner"""

from __future__ import annotations

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description="Execution realism and counterfactual validation lab")
    p.add_argument("--mode", choices=("screen", "full", "stress"), default="screen")
    p.add_argument("--strategies", default="H-0005")
    p.add_argument("--max-events", type=int, default=None, help="Limit tape events (testing only; None=full)")
    p.add_argument("--run-id", default=None, help="Stable id for incremental artifacts / resume")
    p.add_argument("--resume", action="store_true", help="Skip valid matching window/scenario artifacts")
    p.add_argument("--workers", type=int, default=1, help="Reserved; sequential streaming is the default")
    p.add_argument(
        "--legacy-in-memory",
        action="store_true",
        help="Retain all waterfalls (debug/fixtures only; will OOM on full tape)",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    print(f"EXECUTION_REALISM_LAB mode={args.mode} strategies={args.strategies}")
    print("PRODUCTION_EXECUTION: DISABLED")
    if args.run_id:
        print(f"run_id={args.run_id} resume={args.resume}")
    if args.dry_run:
        print("DRY_RUN: would run execution realism simulation")
        return
    from bot.research.execution_realism.engine import run_execution_realism

    result = run_execution_realism(
        mode=args.mode,
        strategies=args.strategies.split(","),
        max_events=args.max_events,
        run_id=args.run_id,
        resume=args.resume,
        workers=args.workers,
        legacy_in_memory=args.legacy_in_memory,
    )
    print(f"VERDICT: {result.get('VERDICT')}")
    print(f"CANONICAL_REPLAY_NET: {result.get('CANONICAL_REPLAY_NET')}")
    print(f"REALISTIC_EXECUTION_NET: {result.get('REALISTIC_EXECUTION_NET')}")
    print(f"DELTA: {result.get('DELTA')}")
    print(f"FILL_SURVIVAL_PCT: {result.get('FILL_SURVIVAL_PCT')}")
    streaming = result.get("STREAMING") or {}
    if streaming:
        print(f"STREAMING run_id={streaming.get('run_id')} written={streaming.get('artifacts_written')} skipped={streaming.get('artifacts_skipped')}")
        print(f"peak_rss_mb={streaming.get('peak_rss_mb')} rss_mb={streaming.get('rss_mb')}")
    print(f"NEW_STRATEGIES_CREATED: []")
    print(f"PRODUCTION_EXECUTION: DISABLED")


if __name__ == "__main__":
    main()

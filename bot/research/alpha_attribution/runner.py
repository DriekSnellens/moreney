"""CLI: python -m bot.research.alpha_attribution.runner"""

from __future__ import annotations

import argparse

from bot.research.alpha_attribution.engine import run_alpha_attribution


def main() -> None:
    p = argparse.ArgumentParser(description="Alpha attribution lab (H-0005 forensic, no new strategy)")
    p.add_argument("--no-live", action="store_true", help="Audit stored paired windows only")
    p.add_argument("--max-events", type=int, default=None)
    args = p.parse_args()
    payload = run_alpha_attribution(live=not args.no_live, max_events=args.max_events)
    print(f"PAIRED_DELTA_ACCOUNTING_AUDIT: {payload.get('PAIRED_DELTA_ACCOUNTING_AUDIT')}")
    print(f"PARENT_REPLAY_NET: {payload.get('PARENT_REPLAY_NET')}")
    print(f"H-0005_REPLAY_NET: {payload.get('H-0005_REPLAY_NET')}")
    print(f"EXCLUDED_SIGNAL_NET: {payload.get('EXCLUDED_SIGNAL_NET')}")
    print(f"RETAINED_SIGNAL_NET: {payload.get('RETAINED_SIGNAL_NET')}")
    print(f"WHY_H0005_UNDERPERFORMED: {payload.get('WHY_H0005_UNDERPERFORMED')}")
    print(f"CONTEXT_DEPENDENCY: {payload.get('CONTEXT_DEPENDENCY')}")
    print(f"NEW_STRATEGIES_CREATED: {payload.get('NEW_STRATEGIES_CREATED')}")
    print(f"PRODUCTION_EXECUTION: {payload.get('PRODUCTION_EXECUTION')}")


if __name__ == "__main__":
    main()

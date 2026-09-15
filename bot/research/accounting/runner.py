"""CLI: python -m bot.research.accounting.runner"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.accounting.engine import run_canonical_accounting
from bot.research.accounting.legacy import scan_legacy_fields


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Canonical replay accounting (H-0005 / H-0007)")
    p.add_argument("--path", type=Path, default=Path("data/research_marketdata"))
    p.add_argument("--first-lab", type=Path, default=Path("data/regime_hypothesis_lab/results.json"))
    p.add_argument("--robustness", type=Path, default=Path("data/edge_robustness_lab/results.json"))
    p.add_argument("--out", type=Path, default=Path("data/research"))
    p.add_argument("--max-events", type=int, default=None)
    p.add_argument("--stride", type=int, default=4)
    p.add_argument("--stored-only", action="store_true", help="Do not re-walk the tape")
    args = p.parse_args(argv)
    result = run_canonical_accounting(
        research_path=args.path,
        first_lab_results=args.first_lab,
        robustness_results=args.robustness,
        out_dir=args.out,
        live=not args.stored_only,
        max_events=args.max_events,
        stride=args.stride,
    )
    print(json.dumps(_final(result), indent=2, sort_keys=True, default=str))
    return 0


def _final(result: dict) -> dict:
    h5 = result.get("H-0005") or {}
    h7 = result.get("H-0007") or {}
    first5 = (h5.get("first_lab_oos") or {}).get("EXECUTION_REPLAY_WORLD") or {}
    paired = (h5.get("PAIRED_PARENT_CHILD") or {}).get("aggregate") or {}
    sel = h7.get("GATE_SELECTIVITY") or {}
    return {
        "ACCOUNTING_AUDIT": result.get("ACCOUNTING_AUDIT"),
        "H-0005": {
            "CANONICAL_REPLAY_NET": (first5.get("replay_net_eur") or {}).get("value"),
            "CANONICAL_REPLAY_NET_PER_FILL": (first5.get("replay_net_per_fill_eur") or {}).get("value"),
            "CANONICAL_REPLAY_NET_PER_SIGNAL": (first5.get("replay_net_per_signal_eur") or {}).get("value"),
            "PARENT_CHILD_PAIRED_DELTA": paired.get("aggregate_delta"),
            "REPLICATION_STATUS": (h5.get("REPLICATION") or {}).get("state"),
            "RESEARCH_DECISION": h5.get("RESEARCH_DECISION"),
        },
        "H-0007": {
            "GATE_SELECTIVITY": sel.get("selectivity"),
            "RESEARCH_DECISION": h7.get("RESEARCH_DECISION"),
        },
        "ROBUST_PAPER_CANDIDATES": result.get("ROBUST_PAPER_CANDIDATES"),
        "PRODUCTION_EXECUTION": "DISABLED",
        "PERFORMANCE": result.get("PERFORMANCE"),
        "legacy_scan": scan_legacy_fields("."),
    }


if __name__ == "__main__":
    raise SystemExit(main())

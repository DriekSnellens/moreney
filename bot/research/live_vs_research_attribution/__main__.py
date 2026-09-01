"""CLI entry for live vs research attribution audit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bot.research.live_vs_research_attribution.runner import run_attribution_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live vs Research Expectancy Attribution Audit")
    parser.add_argument(
        "--audit-path",
        type=Path,
        default=Path("data/live_audit.jsonl"),
        help="Live audit JSONL path",
    )
    parser.add_argument(
        "--bridge-path",
        type=Path,
        default=Path("data/live_micro_bridge_state.json"),
        help="Bridge state JSON path",
    )
    parser.add_argument(
        "--session-path",
        type=Path,
        default=Path("data/live_micro_session_status.json"),
        help="Live micro session status JSON path",
    )
    parser.add_argument(
        "--research-dir",
        type=Path,
        default=Path("data/research"),
        help="Research artifacts directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/research/live_vs_research_attribution.json"),
        help="Machine-readable output JSON",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("docs/LIVE_VS_RESEARCH_ATTRIBUTION_REPORT.md"),
        help="Markdown report path",
    )
    args = parser.parse_args(argv)

    report = run_attribution_audit(
        audit_path=args.audit_path,
        bridge_path=args.bridge_path,
        session_path=args.session_path,
        research_dir=args.research_dir,
        output_path=args.output,
        report_path=args.report,
    )
    print(f"Attribution audit complete: {args.output}")
    print(f"Report written: {args.report}")
    print(f"Root causes ranked: {len(report.get('root_causes', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

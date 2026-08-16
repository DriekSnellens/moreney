"""CLI for market-data research infrastructure report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.market_data.research.study import build_infrastructure_report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Market-data research infrastructure report")
    p.add_argument("--path", type=Path, default=Path("data/research_marketdata"))
    p.add_argument("--out", type=Path, default=Path("data/market_data_research_report.json"))
    args = p.parse_args(argv)
    report = build_infrastructure_report(research_path=args.path)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "final_verdict": report["final_verdict"],
                "event_count": report["event_count"],
                "supported_horizons": report["supported_horizons"],
                "unsupported_horizons": report["unsupported_horizons"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI / library entry for lead-lag research study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from bot.opportunity.lead_lag.study import build_study


def run_lead_lag_study(
    *,
    market_data_dir: Path | str | None = "data/market_data",
    out_path: Path | None = None,
    use_synthetic_if_empty: bool = False,
) -> dict[str, Any]:
    report = build_study(
        market_data_dir=market_data_dir,
        use_synthetic_if_empty=use_synthetic_if_empty,
    )
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Lead-lag hedged dislocation research (shadow-only)")
    p.add_argument("--market-data", type=Path, default=Path("data/market_data"))
    p.add_argument("--out", type=Path, default=Path("data/lead_lag_report.json"))
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="Use synthetic tape only for tooling tests — never claim live-equivalent.",
    )
    args = p.parse_args(argv)
    report = run_lead_lag_study(
        market_data_dir=args.market_data,
        out_path=args.out,
        use_synthetic_if_empty=args.synthetic,
    )
    print(
        json.dumps(
            {
                "verdict": report["O_final_verdict"],
                "data_quality": report["C_timestamp_audit"]["overall_quality"],
                "has_tape": report["C_timestamp_audit"]["has_synchronized_tape"],
                "observation_count": report["observation_count"],
                "execution_enabled_default": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

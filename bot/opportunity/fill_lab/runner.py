"""CLI / library entry for fill-mechanism sensitivity study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def run_fill_mechanism_study(
    *,
    paper_path: Path | str = "data/paper_25000live.json",
    out_path: Path | None = None,
) -> dict[str, Any]:
    from bot.opportunity.fill_lab.study import build_study

    report = build_study(str(paper_path))
    # Flat keys for CLI / dashboard consumers
    rec = report.get("H_production_recommendation") or {}
    report["success_letter"] = rec.get("success_criterion")
    report["recommendation"] = rec.get("primary")
    report["production_pnl_source"] = "TRADE_THROUGH_ONLY"
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fill mechanism sensitivity study (experimental / observational)")
    p.add_argument("--paper", type=Path, default=Path("data/paper_25000live.json"))
    p.add_argument("--out", type=Path, default=Path("data/fill_mechanism_report.json"))
    args = p.parse_args(argv)
    report = run_fill_mechanism_study(paper_path=args.paper, out_path=args.out)
    print(
        json.dumps(
            {
                "success_letter": report.get("success_letter"),
                "recommendation": report.get("recommendation"),
                "also": (report.get("H_production_recommendation") or {}).get("also"),
                "baseline_fills": (report.get("baseline_fingerprint") or {}).get("baseline_fill_count"),
                "quotes": report.get("quotes_count"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

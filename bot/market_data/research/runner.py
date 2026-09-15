"""CLI for research tape discovery, validation, manifest, and acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.market_data.research.finalize import run_finalization, write_finalization_artifacts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Discover research tape, validate, build manifest, run acceptance"
    )
    p.add_argument("--path", type=Path, default=Path("data/research_marketdata"))
    p.add_argument("--out", type=Path, default=Path("data/market_data_research_report.json"))
    p.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/research_marketdata_manifest.json"),
    )
    p.add_argument("--max-integrity-events", type=int, default=None)
    args = p.parse_args(argv)

    report = run_finalization(
        research_path=args.path,
        max_integrity_events=args.max_integrity_events,
    )
    write_finalization_artifacts(
        report, report_path=args.out, manifest_path=args.manifest
    )

    print(
        json.dumps(
            {
                "RECORDER_STATUS": report["RECORDER_STATUS"],
                "DATASET_ID": report["DATASET_ID"],
                "EVENT_COUNT": report["EVENT_COUNT"],
                "DURATION": report["DURATION"],
                "VENUES": report["VENUES"],
                "SYMBOLS": report["SYMBOLS"],
                "RECORDER_DROPS": report["RECORDER_DROPS"],
                "WRITE_ERRORS": report["WRITE_ERRORS"],
                "TIMESTAMP_COVERAGE": {
                    k: {
                        "exchange_ts_pct": v.get("exchange_ts_pct"),
                        "received_ts_pct": v.get("received_ts_pct"),
                        "monotonic_ts_pct": v.get("monotonic_ts_pct"),
                        "sequence_pct": v.get("sequence_pct"),
                    }
                    for k, v in (report["TIMESTAMP_COVERAGE"] or {}).items()
                },
                "SYNC_COVERAGE": {
                    "targets_sampled": (report.get("SYNC_COVERAGE") or {}).get(
                        "targets_sampled"
                    ),
                    "by_tolerance_ms": {
                        k: {"usable_rate": (v or {}).get("usable_rate")}
                        for k, v in (
                            (report.get("SYNC_COVERAGE") or {}).get("by_tolerance_ms") or {}
                        ).items()
                    },
                },
                "SUPPORTED_HORIZONS": report["SUPPORTED_HORIZONS"],
                "FINAL_VERDICT": report["FINAL_VERDICT"],
                "NEXT_ACTION": report["NEXT_ACTION"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

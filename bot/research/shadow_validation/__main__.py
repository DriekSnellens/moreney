"""CLI: rebuild or inspect a shadow validation run.

python -m bot.research.shadow_validation --run-id <id> --resume
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.shadow_validation.artifacts import run_dir_for
from bot.research.shadow_validation.identity import load_frozen_strategy
from bot.research.shadow_validation.protocol import PRODUCTION_EXECUTION_ENABLED
from bot.research.shadow_validation.reducer import reduce_run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Shadow paper validation reducer")
    p.add_argument("--run-id", type=str, default=None)
    p.add_argument("--run-dir", type=Path, default=None)
    p.add_argument("--resume", action="store_true", help="Verify identity then rebuild scorecard")
    args = p.parse_args(argv)
    if PRODUCTION_EXECUTION_ENABLED:
        raise SystemExit("production execution is enabled; refusing")
    directory = args.run_dir or (run_dir_for(args.run_id) if args.run_id else None)
    if directory is None:
        p.error("provide --run-id or --run-dir")
    ident = load_frozen_strategy(directory)
    out = reduce_run(directory, identity=ident)
    print(json.dumps({
        "VALIDATION_INTEGRITY": out["VALIDATION_INTEGRITY"],
        "SHADOW_VALIDATION_VERDICT": (out["decision"] or {}).get("SHADOW_VALIDATION_VERDICT"),
        "n_records": out["n_records"],
        "production_execution": "DISABLED",
        "scorecard_G": (out["scorecard"] or {}).get("G_CURRENT_VERDICT"),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

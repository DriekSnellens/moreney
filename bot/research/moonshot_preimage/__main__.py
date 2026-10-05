"""CLI: ``.venv/bin/python -m bot.research.moonshot_preimage``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.moonshot_preimage.engine import run_moonshot_preimage, to_markdown


def main() -> None:
    p = argparse.ArgumentParser(description="Moonshot preimage precision lab")
    p.add_argument("--candles", default="data/ignition_expand_candles")
    p.add_argument("--alphai", default="data/research/alphai_sessions_merged.json")
    p.add_argument("--horizon", type=int, default=7)
    p.add_argument("--out", default="artifacts/moonshot_preimage.json")
    p.add_argument("--md", default="artifacts/moonshot_preimage.md")
    args = p.parse_args()

    payload = run_moonshot_preimage(
        candle_dir=args.candles,
        alphai_path=args.alphai,
        horizon=args.horizon,
    )
    md = to_markdown(payload)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    Path(args.md).write_text(md, encoding="utf-8")
    pkg = Path(__file__).resolve().parent
    (pkg / "RESULTS.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (pkg / "RESULTS.md").write_text(md, encoding="utf-8")

    best = payload.get("best_precision_n20") or {}
    print(
        f"bases={payload['n_bases']} train={payload['base_rate_train']['n']} "
        f"test={payload['base_rate_test']['n']} "
        f"base_p50={100*payload['base_rate_test']['p50']:.1f}%",
        flush=True,
    )
    if best:
        print(
            f"best n>=20: {best.get('rule')} "
            f"P50={100*best['test']['p50']:.1f}% n={best['test']['n']} "
            f"lift×{best.get('lift50_test')}",
            flush=True,
        )
    hit60 = payload.get("rules_hitting_60pct_precision_n10") or []
    print(f"rules ≥60% P50 with n≥10: {len(hit60)}", flush=True)
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()

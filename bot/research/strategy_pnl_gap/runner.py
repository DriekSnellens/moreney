"""Run strategy PnL gap analysis."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.research.strategy_pnl_gap.analyze import analyze_gap
from bot.research.strategy_pnl_gap.loaders import load_gap_data
from bot.research.strategy_pnl_gap.report import write_report

_REPO = Path(__file__).resolve().parents[3]
_DEFAULT_JSON = _REPO / "data" / "research" / "strategy_pnl_gap.json"
_DEFAULT_MD = _REPO / "docs" / "PAPER_VS_RESEARCH_PNL_GAP_REPORT.md"


def _serialize(obj: object) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _serialize(v) for k, v in asdict(obj).items()}  # type: ignore[arg-type]
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def run_analysis(
    *,
    json_out: Path | None = None,
    md_out: Path | None = None,
) -> dict[str, Any]:
    data = load_gap_data()
    analysis = analyze_gap(data)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "data": _serialize(data),
        "analysis": _serialize(analysis),
    }
    json_path = json_out or _DEFAULT_JSON
    md_path = md_out or _DEFAULT_MD
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(write_report(payload), encoding="utf-8")
    return payload

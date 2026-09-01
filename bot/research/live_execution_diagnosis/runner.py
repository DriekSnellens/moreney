"""Run live execution diagnosis and write JSON + markdown report."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.research.live_execution_diagnosis.buy_fill_gap import analyze_buy_fill_gap
from bot.research.live_execution_diagnosis.errors import analyze_exchange_errors
from bot.research.live_execution_diagnosis.report import write_report

_REPO = Path(__file__).resolve().parents[3]
_DEFAULT_AUDIT = _REPO / "data" / "live_audit.jsonl"
_DEFAULT_SESSION = _REPO / "data" / "live_micro_session_status.json"
_DEFAULT_BRIDGE = _REPO / "data" / "live_micro_bridge_state.json"
_DEFAULT_JSON = _REPO / "data" / "research" / "live_execution_diagnosis.json"
_DEFAULT_MD = _REPO / "docs" / "LIVE_EXECUTION_DIAGNOSIS_REPORT.md"


class _Enc(json.JSONEncoder):
    def default(self, o: object) -> object:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def _serialize(obj: object) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _serialize(v) for k, v in asdict(obj).items()}  # type: ignore[arg-type]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return list(obj)
    return obj


def run_diagnosis(
    *,
    audit_path: Path | None = None,
    session_status_path: Path | None = None,
    bridge_state_path: Path | None = None,
    json_out: Path | None = None,
    md_out: Path | None = None,
) -> dict[str, Any]:
    audit = audit_path or _DEFAULT_AUDIT
    session = session_status_path or _DEFAULT_SESSION
    bridge = bridge_state_path or _DEFAULT_BRIDGE
    json_path = json_out or _DEFAULT_JSON
    md_path = md_out or _DEFAULT_MD

    errors = analyze_exchange_errors(audit)
    gap = analyze_buy_fill_gap(audit, session_status_path=session, bridge_state_path=bridge)

    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "inputs": {
            "audit_path": str(audit),
            "session_status_path": str(session),
            "bridge_state_path": str(bridge),
        },
        "exchange_errors": _serialize(errors),
        "buy_fill_gap": _serialize(gap),
    }

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, cls=_Enc), encoding="utf-8")
    md_path.write_text(write_report(payload), encoding="utf-8")
    return payload

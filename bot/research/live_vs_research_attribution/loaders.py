"""Load existing telemetry for attribution audit."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

_ZERO = Decimal("0")


def _parse_ts(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


@dataclass
class LiveFillRecord:
    event_id: str
    ts: datetime
    venue: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal | None
    notional_eur: Decimal
    status: str
    exchange_order_id: str | None
    order_id: str | None


@dataclass
class AuditEvent:
    event_id: str
    ts: datetime
    event_type: str
    payload: dict[str, Any]


@dataclass
class LoadedData:
    audit_events: list[AuditEvent] = field(default_factory=list)
    live_fills: list[LiveFillRecord] = field(default_factory=list)
    order_blocked: list[dict[str, Any]] = field(default_factory=list)
    order_exceptions: list[dict[str, Any]] = field(default_factory=list)
    bridge_state: dict[str, Any] = field(default_factory=dict)
    session_status: dict[str, Any] = field(default_factory=dict)
    final_validation: dict[str, Any] = field(default_factory=dict)
    execution_realism: dict[str, Any] = field(default_factory=dict)
    phase21: dict[str, Any] = field(default_factory=dict)
    ablation: dict[str, Any] = field(default_factory=dict)
    capital_allocation: dict[str, Any] = field(default_factory=dict)
    economic_parity: list[dict[str, Any]] = field(default_factory=list)
    shadow_observations: list[dict[str, Any]] = field(default_factory=list)
    intelligence_state: dict[str, Any] = field(default_factory=dict)
    attribution_state: dict[str, Any] = field(default_factory=dict)
    missing_sources: list[str] = field(default_factory=list)


def load_audit(audit_path: Path) -> tuple[list[AuditEvent], list[LiveFillRecord], list[dict], list[dict]]:
    events: list[AuditEvent] = []
    fills: list[LiveFillRecord] = []
    blocked: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []

    for row in _load_jsonl(audit_path):
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        evt = AuditEvent(
            event_id=str(row.get("id", "")),
            ts=ts,
            event_type=str(row.get("type", "")),
            payload=dict(row.get("payload") or {}),
        )
        events.append(evt)

        if evt.event_type == "micro_order_result":
            p = evt.payload
            status = str(p.get("status", ""))
            rec = LiveFillRecord(
                event_id=evt.event_id,
                ts=ts,
                venue=str(p.get("venue", "")),
                symbol=str(p.get("symbol", "")).upper(),
                side=str(p.get("side", "")).lower(),
                quantity=_d(p.get("filled_quantity") or p.get("quantity")),
                price=_d(p.get("average_price")) if p.get("average_price") is not None else None,
                notional_eur=_d(p.get("notional_eur")),
                status=status,
                exchange_order_id=str(p.get("exchange_order_id") or "") or None,
                order_id=str(p.get("order_id") or "") or None,
            )
            if status == "filled" and rec.quantity > 0:
                fills.append(rec)
        elif evt.event_type == "order_blocked":
            blocked.append({"ts": ts.isoformat(), **evt.payload})
        elif evt.event_type == "micro_order_exception":
            exceptions.append({"ts": ts.isoformat(), **evt.payload})

    return events, fills, blocked, exceptions


def load_all(
    *,
    audit_path: Path,
    bridge_path: Path,
    session_path: Path,
    research_dir: Path,
) -> LoadedData:
    data = LoadedData()
    missing: list[str] = []

    events, fills, blocked, exceptions = load_audit(audit_path)
    data.audit_events = events
    data.live_fills = fills
    data.order_blocked = blocked
    data.order_exceptions = exceptions
    if not audit_path.is_file():
        missing.append(str(audit_path))

    bridge = _load_json(bridge_path)
    data.bridge_state = bridge or {}
    if bridge is None and bridge_path.is_file():
        missing.append(f"{bridge_path} (parse error)")

    session = _load_json(session_path)
    data.session_status = session or {}
    if session is None and session_path.is_file():
        missing.append(f"{session_path} (parse error)")

    fv = _load_json(research_dir / "final_validation" / "results.json")
    data.final_validation = fv or {}
    if fv is None:
        missing.append("data/research/final_validation/results.json")

    er = _load_json(research_dir / "execution_realism_results.json")
    data.execution_realism = er or {}

    p21 = _load_json(research_dir / "execution_intelligence_phase21.json")
    data.phase21 = p21 or {}

    abl = _load_json(research_dir / "execution_intelligence_ablation.json")
    data.ablation = abl or {}

    cap = _load_json(research_dir / "capital_allocation_phase3.json")
    data.capital_allocation = cap or {}

    ep_path = research_dir / "economic_parity" / "audit.jsonl"
    data.economic_parity = _load_jsonl(ep_path)

    sv_path = research_dir / "shadow_validation" / "observations.jsonl"
    data.shadow_observations = _load_jsonl(sv_path)

    intel = _load_json(Path("data/live_micro_intelligence_state.json"))
    data.intelligence_state = intel or {}

    attr = _load_json(Path("data/live_micro_attribution_state.json"))
    data.attribution_state = attr or {}

    data.missing_sources = missing
    return data


def base_from_symbol(symbol: str) -> str:
    sym = symbol.upper().replace("-", "").replace("/", "")
    if sym.endswith("EUR"):
        return sym[:-3]
    return sym

"""Load live session / history / paper lab snapshots for underperformance diagnosis."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

_ZERO = Decimal("0")


def _dec(value: Any) -> Decimal:
    try:
        if value is None:
            return _ZERO
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return _ZERO


@dataclass
class DaySlice:
    day: str
    n_points: int
    portfolio_start: Decimal
    portfolio_end: Decimal
    realized_start: Decimal
    realized_end: Decimal
    session_start: Decimal
    session_end: Decimal
    free_start: Decimal
    free_end: Decimal
    session_peak: Decimal


@dataclass
class LoadedUnderperformance:
    status: dict[str, Any] = field(default_factory=dict)
    bridge: dict[str, Any] = field(default_factory=dict)
    days: list[DaySlice] = field(default_factory=list)
    paper_lab_realized: Decimal | None = None
    paper_lab_equity: Decimal | None = None
    last_session_report: dict[str, Any] = field(default_factory=dict)
    focus_bases: set[str] = field(default_factory=set)
    loaded_at: str = ""


def load_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_dashboard_days(path: Path) -> list[DaySlice]:
    if not path.exists():
        return []
    by_day: dict[str, list[dict[str, Any]]] = {}
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            day = str(row.get("t") or "")[:10]
            if not day:
                continue
            by_day.setdefault(day, []).append(row)
    out: list[DaySlice] = []
    for day in sorted(by_day):
        xs = by_day[day]
        a, b = xs[0], xs[-1]
        peak = max((_dec(r.get("session_pnl_eur")) for r in xs), default=_ZERO)
        out.append(
            DaySlice(
                day=day,
                n_points=len(xs),
                portfolio_start=_dec(a.get("portfolio_eur")),
                portfolio_end=_dec(b.get("portfolio_eur")),
                realized_start=_dec(a.get("realized_pnl_eur")),
                realized_end=_dec(b.get("realized_pnl_eur")),
                session_start=_dec(a.get("session_pnl_eur")),
                session_end=_dec(b.get("session_pnl_eur")),
                free_start=_dec(a.get("free_eur")),
                free_end=_dec(b.get("free_eur")),
                session_peak=peak,
            )
        )
    return out


def load_paper_lab(path: Path) -> tuple[Decimal | None, Decimal | None]:
    if not path.exists():
        return None, None
    data = json.loads(path.read_text())
    tracker = data.get("tracker") or {}
    return _dec(tracker.get("realized_pnl")), _dec(tracker.get("current_equity"))


def load_all(
    *,
    data_dir: Path = Path("./data"),
) -> LoadedUnderperformance:
    status = load_status(data_dir / "live_micro_session_status.json")
    bridge = status.get("bridge") or {}
    focus_raw = (
        "ETH,SOL,XRP,ADA,LINK,DOT,AVAX,NEAR,ATOM,DOGE,LTC,"
        "ARB,OP,SUI,APT,UNI,AAVE,BNB,BCH,TRX"
    )
    trail = bridge.get("trail_take_profit") or {}
    # Prefer live-reported focus if present later; fall back to session default.
    focus = {p.strip().upper() for p in focus_raw.split(",") if p.strip()}
    lab_pnl, lab_eq = load_paper_lab(data_dir / "paper_lab_strategy.json")
    report_path = data_dir / "live_micro_session_report.json"
    last_report = load_status(report_path) if report_path.exists() else {}
    return LoadedUnderperformance(
        status=status,
        bridge=bridge,
        days=load_dashboard_days(data_dir / "dashboard_history.jsonl"),
        paper_lab_realized=lab_pnl,
        paper_lab_equity=lab_eq,
        last_session_report=last_report,
        focus_bases=focus,
        loaded_at=datetime.utcnow().isoformat() + "Z",
    )


__all__ = [
    "DaySlice",
    "LoadedUnderperformance",
    "load_all",
    "_dec",
    "_ZERO",
]

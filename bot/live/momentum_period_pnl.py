"""Period net PnL for the dual-sleeve momentum desk (operator calendar).

Sums closed ``exit.net_eur`` from sleeve JSONL ledgers. Period boundaries use
Europe/Amsterdam so "deze week / deze maand" match what the operator means.
Open mark-to-market is tracked separately and never mixed into realized.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_OPERATOR_TZ = ZoneInfo("Europe/Amsterdam")


@dataclass(frozen=True)
class PeriodNet:
    week_eur: float
    month_eur: float
    all_time_eur: float
    day_eur: float
    trades_week: int
    trades_month: int
    trades_all_time: int
    trades_day: int


@dataclass(frozen=True)
class DeskEarnings:
    core: PeriodNet
    volatile: PeriodNet
    combined: PeriodNet
    open_mtm_eur: float
    as_of: str
    tz: str = "Europe/Amsterdam"


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # ms or s heuristics
        v = float(raw)
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=UTC)
    text = str(raw).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def operator_day_start(now: datetime | None = None) -> datetime:
    local = (now or datetime.now(UTC)).astimezone(_OPERATOR_TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def operator_week_start(now: datetime | None = None) -> datetime:
    local = (now or datetime.now(UTC)).astimezone(_OPERATOR_TZ)
    monday = local.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = monday.fromordinal(monday.toordinal() - monday.weekday())
    return monday.astimezone(UTC)


def operator_month_start(now: datetime | None = None) -> datetime:
    local = (now or datetime.now(UTC)).astimezone(_OPERATOR_TZ)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def load_exit_fills(path: str | Path | None) -> list[dict[str, Any]]:
    """Load closed-trade rows (``event == exit``) from a sleeve ledger."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, Mapping):
            continue
        if str(row.get("event") or row.get("type") or "") != "exit":
            continue
        ts = _parse_ts(row.get("ts") or row.get("t") or row.get("closed_at"))
        if ts is None:
            continue
        try:
            net = float(row.get("net_eur") if row.get("net_eur") is not None else row.get("pnl_eur") or 0.0)
        except (TypeError, ValueError):
            net = 0.0
        out.append(
            {
                "ts": ts,
                "net_eur": net,
                "base": row.get("base"),
                "reason": row.get("reason"),
            }
        )
    return out


def sum_period(
    exits: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    all_time_fallback: float | None = None,
) -> PeriodNet:
    """Sum exit nets for day / week / month / all-time."""
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    day0 = operator_day_start(now_utc)
    week0 = operator_week_start(now_utc)
    month0 = operator_month_start(now_utc)

    day = week = month = all_time = 0.0
    n_day = n_week = n_month = n_all = 0
    for row in exits:
        ts = row.get("ts")
        if not isinstance(ts, datetime):
            ts = _parse_ts(ts)
        if ts is None:
            continue
        try:
            net = float(row.get("net_eur") or 0.0)
        except (TypeError, ValueError):
            continue
        all_time += net
        n_all += 1
        if ts >= month0:
            month += net
            n_month += 1
        if ts >= week0:
            week += net
            n_week += 1
        if ts >= day0:
            day += net
            n_day += 1

    if n_all == 0 and all_time_fallback is not None:
        all_time = float(all_time_fallback)

    return PeriodNet(
        week_eur=round(week, 2),
        month_eur=round(month, 2),
        all_time_eur=round(all_time, 2),
        day_eur=round(day, 2),
        trades_week=n_week,
        trades_month=n_month,
        trades_all_time=n_all,
        trades_day=n_day,
    )


def _combine(a: PeriodNet, b: PeriodNet) -> PeriodNet:
    return PeriodNet(
        week_eur=round(a.week_eur + b.week_eur, 2),
        month_eur=round(a.month_eur + b.month_eur, 2),
        all_time_eur=round(a.all_time_eur + b.all_time_eur, 2),
        day_eur=round(a.day_eur + b.day_eur, 2),
        trades_week=a.trades_week + b.trades_week,
        trades_month=a.trades_month + b.trades_month,
        trades_all_time=a.trades_all_time + b.trades_all_time,
        trades_day=a.trades_day + b.trades_day,
    )


def compute_desk_earnings(
    *,
    core_ledger_path: str | Path | None,
    volatile_ledger_path: str | Path | None = None,
    core_status: Mapping[str, Any] | None = None,
    volatile_status: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> DeskEarnings:
    """Build operator earnings for core, volatile, and combined sleeves."""
    core_status = core_status or {}
    volatile_status = volatile_status or {}
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)

    core_exits = load_exit_fills(core_ledger_path)
    vol_exits = load_exit_fills(volatile_ledger_path)

    core = sum_period(
        core_exits,
        now=now_utc,
        all_time_fallback=_as_float(core_status.get("realized_total_eur")),
    )
    volatile = sum_period(
        vol_exits,
        now=now_utc,
        all_time_fallback=_as_float(volatile_status.get("realized_total_eur")),
    )
    open_mtm = _as_float(core_status.get("unrealized_net_eur")) + _as_float(
        volatile_status.get("unrealized_net_eur")
    )
    return DeskEarnings(
        core=core,
        volatile=volatile,
        combined=_combine(core, volatile),
        open_mtm_eur=round(open_mtm, 2),
        as_of=now_utc.astimezone(_OPERATOR_TZ).isoformat(),
    )


def _as_float(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def earnings_as_dict(e: DeskEarnings) -> dict[str, Any]:
    def _p(p: PeriodNet) -> dict[str, Any]:
        return {
            "day_eur": p.day_eur,
            "week_eur": p.week_eur,
            "month_eur": p.month_eur,
            "all_time_eur": p.all_time_eur,
            "trades_day": p.trades_day,
            "trades_week": p.trades_week,
            "trades_month": p.trades_month,
            "trades_all_time": p.trades_all_time,
        }

    return {
        "tz": e.tz,
        "as_of": e.as_of,
        "open_mtm_eur": e.open_mtm_eur,
        "core": _p(e.core),
        "volatile": _p(e.volatile),
        "combined": _p(e.combined),
    }

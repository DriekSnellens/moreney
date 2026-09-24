"""Period net PnL for the dual-sleeve momentum desk (operator calendar).

Sums closed ``exit.net_eur`` from sleeve JSONL ledgers. ``combined`` is live
venue fills only; paper/dry-run fills live in ``paper``. Period boundaries use
Europe/Amsterdam so "deze week / deze maand" match what the operator means.
Open mark-to-market is tracked separately (live vs paper) and never mixed
into realized.
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
    paper: PeriodNet
    open_mtm_eur: float
    paper_open_mtm_eur: float
    as_of: str
    tz: str = "Europe/Amsterdam"
    short_weakest: PeriodNet | None = None
    donchian: PeriodNet | None = None
    clip: PeriodNet | None = None


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
                "dry_run": row.get("dry_run"),
                "venue": row.get("venue"),
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


def _zero() -> PeriodNet:
    return PeriodNet(
        week_eur=0.0,
        month_eur=0.0,
        all_time_eur=0.0,
        day_eur=0.0,
        trades_week=0,
        trades_month=0,
        trades_all_time=0,
        trades_day=0,
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


def sleeve_is_live(status: Mapping[str, Any] | None, *, default_live: bool = False) -> bool:
    """True only when the sleeve is placing real venue fills."""
    st = status or {}
    if bool(st.get("paper_only")):
        return False
    if st.get("allow_live") is False:
        return False
    dry = st.get("dry_run")
    if dry is True:
        return False
    if dry is False:
        return True
    return bool(default_live)


def _row_is_paper(row: Mapping[str, Any], *, sleeve_live: bool) -> bool:
    """Classify one exit. Explicit live fills stay live even if the sleeve later goes paper."""
    dry = row.get("dry_run")
    if dry is True or str(dry).lower() in {"true", "1", "yes"}:
        return True
    venue = str(row.get("venue") or "").strip().lower()
    if venue in {"paper", "synthetic"}:
        return True
    if dry is False or str(dry).lower() in {"false", "0", "no"}:
        return False
    return not sleeve_live


def _partition(
    exits: Sequence[Mapping[str, Any]], *, sleeve_live: bool
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    live: list[Mapping[str, Any]] = []
    paper: list[Mapping[str, Any]] = []
    for row in exits:
        if _row_is_paper(row, sleeve_live=sleeve_live):
            paper.append(row)
        else:
            live.append(row)
    return live, paper


def _sum_split(
    exits: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    sleeve_live: bool,
    realized_fallback: float | None,
) -> tuple[PeriodNet, PeriodNet]:
    live_rows, paper_rows = _partition(exits, sleeve_live=sleeve_live)
    live = sum_period(
        live_rows,
        now=now,
        all_time_fallback=realized_fallback if sleeve_live else None,
    )
    paper = sum_period(
        paper_rows,
        now=now,
        all_time_fallback=realized_fallback if not sleeve_live else None,
    )
    return live, paper


def compute_desk_earnings(
    *,
    core_ledger_path: str | Path | None,
    volatile_ledger_path: str | Path | None = None,
    core_status: Mapping[str, Any] | None = None,
    volatile_status: Mapping[str, Any] | None = None,
    short_weakest_ledger_path: str | Path | None = None,
    short_weakest_status: Mapping[str, Any] | None = None,
    donchian_ledger_path: str | Path | None = None,
    donchian_status: Mapping[str, Any] | None = None,
    clip_ledger_path: str | Path | None = None,
    clip_status: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> DeskEarnings:
    """Live net vs paper net. ``combined`` is live venue fills only."""
    core_status = core_status or {}
    volatile_status = volatile_status or {}
    short_weakest_status = short_weakest_status or {}
    donchian_status = donchian_status or {}
    clip_status = clip_status or {}
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)

    def _open(status: Mapping[str, Any], live: bool) -> tuple[float, float]:
        mtm = _as_float(status.get("unrealized_net_eur"))
        return (mtm, 0.0) if live else (0.0, mtm)

    core_live_flag = sleeve_is_live(core_status, default_live=True)
    vol_live_flag = sleeve_is_live(volatile_status, default_live=False)
    sw_live_flag = sleeve_is_live(short_weakest_status, default_live=False)
    dc_live_flag = sleeve_is_live(donchian_status, default_live=False)
    clip_live_flag = sleeve_is_live(clip_status, default_live=False)

    core_live, core_paper = _sum_split(
        load_exit_fills(core_ledger_path),
        now=now_utc,
        sleeve_live=core_live_flag,
        realized_fallback=_as_float(core_status.get("realized_total_eur")),
    )
    vol_live, vol_paper = _sum_split(
        load_exit_fills(volatile_ledger_path),
        now=now_utc,
        sleeve_live=vol_live_flag,
        realized_fallback=_as_float(volatile_status.get("realized_total_eur")),
    )
    sw_live, sw_paper = _sum_split(
        load_exit_fills(short_weakest_ledger_path),
        now=now_utc,
        sleeve_live=sw_live_flag,
        realized_fallback=_as_float(short_weakest_status.get("realized_total_eur")),
    )
    dc_live, dc_paper = _sum_split(
        load_exit_fills(donchian_ledger_path),
        now=now_utc,
        sleeve_live=dc_live_flag,
        realized_fallback=_as_float(donchian_status.get("realized_total_eur")),
    )
    clip_live, clip_paper = _sum_split(
        load_exit_fills(clip_ledger_path),
        now=now_utc,
        sleeve_live=clip_live_flag,
        realized_fallback=_as_float(clip_status.get("realized_total_eur")),
    )

    core = _combine(core_live, core_paper)
    volatile = _combine(vol_live, vol_paper)
    short_weakest = _combine(sw_live, sw_paper)
    donchian = _combine(dc_live, dc_paper)
    clip = _combine(clip_live, clip_paper)

    live = _combine(_combine(core_live, vol_live), _combine(sw_live, dc_live))
    live = _combine(live, clip_live)
    paper = _combine(_combine(core_paper, vol_paper), _combine(sw_paper, dc_paper))
    paper = _combine(paper, clip_paper)

    live_open = paper_open = 0.0
    for st, flag in (
        (core_status, core_live_flag),
        (volatile_status, vol_live_flag),
        (short_weakest_status, sw_live_flag),
        (donchian_status, dc_live_flag),
        (clip_status, clip_live_flag),
    ):
        lo, po = _open(st, flag)
        live_open += lo
        paper_open += po

    has_sw = bool(short_weakest_ledger_path or short_weakest_status)
    has_dc = bool(donchian_ledger_path or donchian_status)
    has_clip = bool(clip_ledger_path or clip_status)
    return DeskEarnings(
        core=core,
        volatile=volatile,
        combined=live,
        paper=paper,
        open_mtm_eur=round(live_open, 2),
        paper_open_mtm_eur=round(paper_open, 2),
        as_of=now_utc.astimezone(_OPERATOR_TZ).isoformat(),
        short_weakest=short_weakest if has_sw else None,
        donchian=donchian if has_dc else None,
        clip=clip if has_clip else None,
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

    out = {
        "tz": e.tz,
        "as_of": e.as_of,
        "open_mtm_eur": e.open_mtm_eur,
        "paper_open_mtm_eur": e.paper_open_mtm_eur,
        "core": _p(e.core),
        "volatile": _p(e.volatile),
        "combined": _p(e.combined),
        "paper": _p(e.paper),
    }
    if e.short_weakest is not None:
        out["short_weakest"] = _p(e.short_weakest)
    if e.donchian is not None:
        out["donchian"] = _p(e.donchian)
    if e.clip is not None:
        out["clip"] = _p(e.clip)
    return out

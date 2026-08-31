"""Time-series snapshots for the live dashboard (portfolio, PnL, cash)."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("./data/dashboard_history.jsonl")
_MAX_POINTS = 4320  # ~72h at 1/min
_MIN_INTERVAL_SEC = 55.0
_last_record_mono = 0.0
_OPERATOR_TZ = ZoneInfo("Europe/Amsterdam")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def history_path() -> Path:
    return _DEFAULT_PATH


def clear_history(*, path: Path | None = None) -> None:
    """Remove dashboard chart history for a clean operator slate."""
    global _last_record_mono  # noqa: PLW0603
    target = path or history_path()
    try:
        if target.exists():
            target.unlink()
    except OSError:
        logger.exception("dashboard history clear failed path=%s", target)
    _last_record_mono = 0.0


def extract_metrics(payload: dict[str, Any]) -> dict[str, str] | None:
    """Pull chart fields from a live dashboard payload or session snapshot."""
    session = payload.get("session") or payload
    bridge = session.get("bridge") or {}
    diag = bridge.get("diagnostics") or {}
    observe = payload.get("observe") or {}

    portfolio = _to_decimal(
        session.get("portfolio_value_eur") or bridge.get("portfolio_value_eur")
    )
    if portfolio is None:
        portfolio = _to_decimal(observe.get("total_value_eur"))
    realized = _to_decimal(
        session.get("realized_trade_pnl_eur")
        or bridge.get("realized_trade_pnl_eur")
        or session.get("netto_winst_eur")
        or bridge.get("netto_winst_eur")
    )
    unrealized = _to_decimal(
        bridge.get("unrealized_mtm_eur") or diag.get("unrealized_mtm_eur")
    )
    winnable = _to_decimal(
        bridge.get("winnable_mtm_eur") or diag.get("winnable_mtm_eur")
    )
    free = _to_decimal(bridge.get("free_quote_eur") or bridge.get("remaining_eur"))
    if free is None:
        total_free = Decimal("0")
        found = False
        for entry in observe.get("balances") or []:
            if not isinstance(entry, dict):
                continue
            nested = entry.get("balances") if isinstance(entry.get("balances"), list) else None
            rows = nested if nested is not None else ([entry] if entry.get("asset") else [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("asset") or "").upper() != "EUR":
                    continue
                amt = _to_decimal(row.get("available") if row.get("available") is not None else row.get("free"))
                if amt is None:
                    amt = _to_decimal(row.get("total"))
                if amt is not None:
                    total_free += amt
                    found = True
        if found:
            free = total_free

    if portfolio is None and realized is None:
        return None

    running = bool(session.get("running") or session.get("task_running"))
    out: dict[str, str] = {
        "t": datetime.now(UTC).isoformat(),
        "running": "1" if running else "0",
    }
    if portfolio is not None:
        out["portfolio_eur"] = str(portfolio)
    if realized is not None:
        out["realized_pnl_eur"] = str(realized)
    if unrealized is not None:
        out["unrealized_eur"] = str(unrealized)
    if winnable is not None:
        out["winnable_eur"] = str(winnable)
    if free is not None:
        out["free_eur"] = str(free)
    start = _to_decimal(
        session.get("starting_portfolio_eur") or bridge.get("starting_portfolio_eur")
    )
    if start is not None and portfolio is not None:
        out["session_pnl_eur"] = str(portfolio - start)
    return out


def record_snapshot(
    payload: dict[str, Any],
    *,
    path: Path | None = None,
    force: bool = False,
) -> bool:
    """Append one history point (throttled). Returns True if written."""
    global _last_record_mono
    now = time.monotonic()
    if not force and now - _last_record_mono < _MIN_INTERVAL_SEC:
        return False
    point = extract_metrics(payload)
    if point is None:
        return False
    target = path or history_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(point, separators=(",", ":")) + "\n")
        _trim_file(target)
        _last_record_mono = now
        return True
    except Exception:  # noqa: BLE001
        logger.exception("dashboard history record failed path=%s", target)
        return False


def _trim_file(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= _MAX_POINTS:
        return
    keep = lines[-_MAX_POINTS:]
    path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")


def load_history(*, path: Path | None = None, limit: int = 720) -> list[dict[str, Any]]:
    """Return the most recent history points (default ~12h at 1/min)."""
    target = path or history_path()
    if not target.exists():
        return []
    cap = max(10, min(int(limit), _MAX_POINTS))
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-cap:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def seed_from_session_status(status: dict[str, Any]) -> None:
    """Record one point from session manager tick (same throttle)."""
    record_snapshot({"session": status})


def _parse_history_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def operator_day_start_utc(*, now: datetime | None = None) -> datetime:
    """Today 00:00 Europe/Amsterdam, as UTC."""
    local = (now or datetime.now(UTC)).astimezone(_OPERATOR_TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def operator_week_start_utc(*, now: datetime | None = None) -> datetime:
    """Monday 00:00 Europe/Amsterdam of the current week, as UTC."""
    local = (now or datetime.now(UTC)).astimezone(_OPERATOR_TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start - timedelta(days=start.weekday())
    return start.astimezone(UTC)


def realized_delta_since(
    history: list[dict[str, Any]],
    *,
    current_realized: Decimal | None,
    since: datetime,
) -> Decimal | None:
    """Net change in cumulative realized PnL since ``since``.

    Baseline = last history point at/before ``since``, else first point after.
    """
    if current_realized is None or not history:
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    else:
        since = since.astimezone(UTC)

    last_before: Decimal | None = None
    first_after: Decimal | None = None
    for row in history:
        ts = _parse_history_ts(row.get("t"))
        if ts is None:
            continue
        realized = _to_decimal(row.get("realized_pnl_eur"))
        if realized is None:
            continue
        if ts <= since:
            last_before = realized
        elif first_after is None:
            first_after = realized
    baseline = last_before if last_before is not None else first_after
    if baseline is None:
        return None
    return current_realized - baseline


def daily_realized_delta(
    history: list[dict[str, Any]],
    *,
    current_realized: Decimal | None,
    now: datetime | None = None,
) -> Decimal | None:
    """Net realized since local midnight (Europe/Amsterdam)."""
    return realized_delta_since(
        history,
        current_realized=current_realized,
        since=operator_day_start_utc(now=now),
    )


def weekly_realized_delta(
    history: list[dict[str, Any]],
    *,
    current_realized: Decimal | None,
    days: int = 7,
    now: datetime | None = None,
) -> Decimal | None:
    """Net realized this calendar week (Mon–Sun, Europe/Amsterdam).

    ``days`` kept for compatibility; ``days < 7`` uses a rolling window.
    """
    if days < 7:
        since = (now or datetime.now(UTC)) - timedelta(days=max(1, days))
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        return realized_delta_since(
            history, current_realized=current_realized, since=since
        )
    return realized_delta_since(
        history,
        current_realized=current_realized,
        since=operator_week_start_utc(now=now),
    )


def recent_fills_for_display(diag: dict[str, Any], *, limit: int = 8) -> list[dict[str, Any]]:
    """Operator fill feed — in-memory session fills, audit log as fallback."""
    fills = [f for f in (diag.get("recent_live_fills") or []) if isinstance(f, dict)]
    if fills:
        fills.sort(key=lambda f: str(f.get("ts") or ""))
        return fills[-limit:]
    try:
        from bot.live.dashboard_reconcile import _audit_fills_since

        since = datetime.now(UTC) - timedelta(hours=24)
        audit_rows = _audit_fills_since(since)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for row in audit_rows[-limit:]:
        qty = row.get("qty")
        px = row.get("price")
        if qty is None or px is None:
            continue
        notional = (Decimal(str(qty)) * Decimal(str(px))).quantize(Decimal("0.01"))
        ts = row.get("ts")
        out.append(
            {
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts or ""),
                "venue": row.get("venue"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "qty": str(qty),
                "price": str(px),
                "notional_eur": str(notional),
                "source": row.get("source") or "audit",
            }
        )
    return out


def chart_history_points(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop reconcile replay stairs (zero MTM snapshots) — keep live session points."""
    live: list[dict[str, Any]] = []
    for row in history:
        unreal = _to_decimal(row.get("unrealized_eur"))
        winn = _to_decimal(row.get("winnable_eur"))
        if unreal is not None and winn is not None and unreal == 0 and winn == 0:
            continue
        live.append(row)
    return live if live else history


def _calendar_pnl_for_payload(
    payload: dict[str, Any],
    *,
    current_realized: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """Prefer exchange-FIFO calendar PnL; fall back to clean history delta."""
    from bot.live.dashboard_pnl import calendar_pnl_for_metrics

    daily, weekly, source = calendar_pnl_for_metrics()
    if daily is not None or weekly is not None:
        return daily, weekly, source or "exchange_fifo"

    session = payload.get("session") or {}
    extra = session.get("calendar_pnl") or {}
    daily = _to_decimal(extra.get("daily_eur"))
    weekly = _to_decimal(extra.get("weekly_eur"))
    if daily is not None or weekly is not None:
        return daily, weekly, str(extra.get("source") or "exchange_fifo")

    hist = chart_history_points(load_history(limit=7 * 24 * 60))
    return (
        daily_realized_delta(hist, current_realized=current_realized),
        weekly_realized_delta(hist, current_realized=current_realized),
        "history_delta",
    )


def metrics_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact KPI dict for JSON polling."""
    session = payload.get("session") or {}
    bridge = session.get("bridge") or {}
    diag = bridge.get("diagnostics") or {}
    observe = payload.get("observe") or {}

    portfolio = _to_decimal(
        session.get("portfolio_value_eur") or bridge.get("portfolio_value_eur")
    )
    if portfolio is None:
        portfolio = _to_decimal(observe.get("total_value_eur"))
    realized = _to_decimal(
        session.get("realized_trade_pnl_eur")
        or bridge.get("realized_trade_pnl_eur")
        or session.get("netto_winst_eur")
        or bridge.get("netto_winst_eur")
    )
    unrealized = _to_decimal(
        bridge.get("unrealized_mtm_eur") or diag.get("unrealized_mtm_eur")
    )
    winnable = _to_decimal(
        bridge.get("winnable_mtm_eur") or diag.get("winnable_mtm_eur")
    )
    free = _to_decimal(bridge.get("free_quote_eur") or bridge.get("remaining_eur"))
    start = _to_decimal(
        session.get("starting_portfolio_eur") or bridge.get("starting_portfolio_eur")
    )
    session_pnl = (portfolio - start) if portfolio is not None and start is not None else None
    session_start_realized = _to_decimal(
        bridge.get("session_start_realized_eur")
    )
    session_realized = (
        (realized - session_start_realized)
        if realized is not None and session_start_realized is not None
        else None
    )
    tx = (
        session.get("session_live_transaction_count")
        or bridge.get("session_live_transaction_count")
        or session.get("live_transaction_count")
        or bridge.get("live_transaction_count")
        or 0
    )
    try:
        tx_n = int(tx or 0)
    except (TypeError, ValueError):
        tx_n = 0

    recent_fills = recent_fills_for_display(diag, limit=8)
    last_fill = recent_fills[-1] if recent_fills else None

    daily_realized, weekly_realized, pnl_source = _calendar_pnl_for_payload(
        payload, current_realized=realized
    )

    return {
        "updated_at": session.get("updated_at"),
        "running": bool(session.get("task_running"))
        if session.get("task_running") is not None
        else bool(session.get("running") or session.get("task_running")),
        "task_running": bool(session.get("task_running"))
        if session.get("task_running") is not None
        else None,
        "stale": bool(session.get("stale")),
        "portfolio_eur": float(portfolio) if portfolio is not None else None,
        "realized_pnl_eur": float(realized) if realized is not None else None,
        "session_realized_eur": float(session_realized) if session_realized is not None else None,
        "daily_realized_eur": float(daily_realized) if daily_realized is not None else None,
        "weekly_realized_eur": float(weekly_realized) if weekly_realized is not None else None,
        "daily_realized_source": pnl_source,
        "weekly_realized_source": pnl_source,
        "weekly_target_low_eur": 140.0,
        "weekly_target_high_eur": 350.0,
        "weekly_pace_realistic_eur": 35.0,
        "daily_target_low_eur": 20.0,
        "daily_target_high_eur": 50.0,
        "unrealized_eur": float(unrealized) if unrealized is not None else None,
        "winnable_eur": float(winnable) if winnable is not None else None,
        "session_pnl_eur": float(session_pnl) if session_pnl is not None else None,
        "free_eur": float(free) if free is not None else None,
        "tx_count": tx_n,
        "portfolio_holdings": bridge.get("portfolio_holdings") or [],
        "recent_fills": recent_fills,
        "last_fill": last_fill,
    }


def chart_series_from_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    labels: list[str] = []
    portfolio: list[float | None] = []
    realized: list[float | None] = []
    unrealized: list[float | None] = []
    winnable: list[float | None] = []
    session_pnl: list[float | None] = []

    def _f(key: str, row: dict[str, Any]) -> float | None:
        raw = row.get(key)
        if raw is None or raw == "":
            return None
        try:
            return float(str(raw))
        except (TypeError, ValueError):
            return None

    for row in chart_history_points(history):
        t = str(row.get("t") or "")
        labels.append(t[11:16] if len(t) >= 16 else t[-8:] or "—")
        portfolio.append(_f("portfolio_eur", row))
        realized.append(_f("realized_pnl_eur", row))
        unrealized.append(_f("unrealized_eur", row))
        winnable.append(_f("winnable_eur", row))
        session_pnl.append(_f("session_pnl_eur", row))

    return {
        "labels": labels,
        "portfolio": portfolio,
        "realized": realized,
        "unrealized": unrealized,
        "winnable": winnable,
        "session_pnl": session_pnl,
    }

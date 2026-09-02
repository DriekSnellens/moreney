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
_history_cache: list[dict[str, Any]] | None = None
_history_cache_mtime: float = 0.0
_history_cache_path: Path | None = None


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
    global _last_record_mono, _history_cache, _history_cache_mtime, _history_cache_path  # noqa: PLW0603
    target = path or history_path()
    try:
        if target.exists():
            target.unlink()
    except OSError:
        logger.exception("dashboard history clear failed path=%s", target)
    _last_record_mono = 0.0
    _history_cache = None
    _history_cache_mtime = 0.0
    _history_cache_path = None


def _invalidate_history_cache() -> None:
    global _history_cache, _history_cache_mtime  # noqa: PLW0603
    _history_cache = None
    _history_cache_mtime = 0.0


def _history_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        return 0.0


def _read_jsonl_tail(path: Path, limit: int) -> list[str]:
    """Read the last ``limit`` non-empty lines without loading the whole file."""
    if limit <= 0 or not path.exists():
        return []
    chunk_size = 65536
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size <= 0:
                return []
            buf = b""
            pos = size
            raw_lines: list[bytes] = []
            while pos > 0 and len(raw_lines) <= limit:
                read_size = min(chunk_size, pos)
                pos -= read_size
                fh.seek(pos)
                buf = fh.read(read_size) + buf
                raw_lines = buf.splitlines()
            tail = raw_lines[-limit:] if len(raw_lines) > limit else raw_lines
            return [
                line.decode("utf-8", errors="replace")
                for line in tail
                if line.strip()
            ]
    except OSError:
        logger.exception("dashboard history tail read failed path=%s", path)
        return []


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
        _invalidate_history_cache()
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
    _invalidate_history_cache()


def _parse_history_lines(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
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


def load_history(*, path: Path | None = None, limit: int = 720) -> list[dict[str, Any]]:
    """Return the most recent history points (default ~12h at 1/min)."""
    global _history_cache, _history_cache_mtime, _history_cache_path  # noqa: PLW0603

    target = path or history_path()
    if not target.exists():
        return []
    cap = max(10, min(int(limit), _MAX_POINTS))
    mtime = _history_mtime(target)
    if (
        _history_cache is not None
        and _history_cache_path == target
        and mtime == _history_cache_mtime
        and len(_history_cache) >= cap
    ):
        return _history_cache[-cap:]

    cache_cap = max(cap, min(_MAX_POINTS, 1440))
    lines = _read_jsonl_tail(target, cache_cap)
    rows = _parse_history_lines(lines)
    _history_cache = rows
    _history_cache_mtime = mtime
    _history_cache_path = target
    return rows[-cap:]


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


def portfolio_delta_since(
    history: list[dict[str, Any]],
    *,
    current_portfolio: Decimal | None,
    since: datetime,
) -> Decimal | None:
    """Net change in portfolio equity since ``since``."""
    if current_portfolio is None or not history:
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
        portfolio = _to_decimal(row.get("portfolio_eur"))
        if portfolio is None:
            continue
        if ts <= since:
            last_before = portfolio
        elif first_after is None:
            first_after = portfolio
    baseline = last_before if last_before is not None else first_after
    if baseline is None:
        return None
    return current_portfolio - baseline


def daily_portfolio_delta(
    history: list[dict[str, Any]],
    *,
    current_portfolio: Decimal | None,
    now: datetime | None = None,
) -> Decimal | None:
    """Portfolio equity change since local midnight (Europe/Amsterdam)."""
    return portfolio_delta_since(
        history,
        current_portfolio=current_portfolio,
        since=operator_day_start_utc(now=now),
    )


def today_portfolio_pnl(
    *,
    portfolio: Decimal | None,
    session_pnl: Decimal | None,
    daily_realized: Decimal | None,
    history: list[dict[str, Any]],
    now: datetime | None = None,
) -> Decimal | None:
    """Operator-facing PnL for today — equity delta, not Geïnd + total open MTM."""
    delta = daily_portfolio_delta(
        history, current_portfolio=portfolio, now=now
    )
    if delta is not None:
        return delta
    if daily_realized is not None:
        return daily_realized
    return session_pnl


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

    hist = chart_history_points(load_history(limit=720))
    return (
        daily_realized_delta(hist, current_realized=current_realized),
        weekly_realized_delta(hist, current_realized=current_realized),
        "history_delta",
    )


def enrich_session_from_bridge(
    session: dict[str, Any],
    bridge: Any,
) -> dict[str, Any]:
    """Merge live bridge snapshot into session status for dashboard KPIs."""
    if bridge is None:
        return session
    out = dict(session)
    try:
        snap = bridge.snapshot_bridge()
    except Exception:  # noqa: BLE001
        logger.exception("bridge snapshot_bridge failed for dashboard")
        return session
    out["bridge"] = snap
    for key, attr in (
        ("portfolio_value_eur", "portfolio_value_eur"),
        ("starting_portfolio_eur", "starting_portfolio_eur"),
        ("realized_trade_pnl_eur", "realized_trade_pnl_eur"),
        ("netto_winst_eur", "realized_trade_pnl_eur"),
        ("session_live_fill_count", "session_live_fill_count"),
        ("session_live_transaction_count", "session_live_transaction_count"),
        ("live_fill_count", "session_live_fill_count"),
        ("live_transaction_count", "session_live_transaction_count"),
    ):
        val = getattr(bridge, attr, None)
        if val is not None:
            out[key] = str(val)
    return out


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
    open_unrealized = unrealized
    hist = payload.get("history")
    if not isinstance(hist, list):
        hist = load_history(limit=720)
    portfolio_pnl = today_portfolio_pnl(
        portfolio=portfolio,
        session_pnl=session_pnl,
        daily_realized=daily_realized,
        history=hist if isinstance(hist, list) else [],
    )

    cap_deployed = _to_decimal(diag.get("capital_deployed_eur"))
    net_cap_hour = None
    if daily_realized is not None and cap_deployed and cap_deployed > 0:
        started = session.get("started_at") or session.get("updated_at")
        ts = _parse_history_ts(started)
        if ts is not None:
            elapsed = Decimal(str(max(60.0, (datetime.now(UTC) - ts).total_seconds())))
            from bot.strategies.opportunity_economics import compute_net_eur_per_capital_hour

            net_cap_hour = compute_net_eur_per_capital_hour(
                realized_net_eur=daily_realized,
                capital_deployed_eur=cap_deployed,
                elapsed_seconds=elapsed,
            )

    available_capital = _to_decimal(
        bridge.get("remaining_eur") or bridge.get("free_quote_eur")
    )
    venue_bv = diag.get("venue_economics_bitvavo") or {}
    venue_okx = diag.get("venue_economics_okx") or {}
    from bot.integrations.alphai.status import alphai_metrics

    alphai = alphai_metrics(session, bridge)

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
        "harvested_today_eur": float(daily_realized) if daily_realized is not None else None,
        "open_unrealized_eur": float(open_unrealized) if open_unrealized is not None else None,
        "portfolio_pnl_eur": float(portfolio_pnl) if portfolio_pnl is not None else None,
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
        "entry_quality_candidates": diag.get("entry_quality_candidates"),
        "entry_quality_normal": diag.get("entry_quality_normal"),
        "entry_quality_reduced": diag.get("entry_quality_reduced"),
        "entry_quality_rejected": diag.get("entry_quality_rejected"),
        "average_headroom_pct": diag.get("average_headroom_pct"),
        "average_extension_pct": diag.get("average_extension_pct"),
        "average_entry_quality": diag.get("average_entry_quality"),
        "average_required_move_pct": diag.get("average_required_move_pct"),
        "average_recommended_size_multiplier": diag.get(
            "average_recommended_size_multiplier"
        ),
        "capital_efficiency_candidates": diag.get("capital_efficiency_candidates"),
        "capital_efficiency_reduced": diag.get("capital_efficiency_reduced"),
        "capital_efficiency_rejected": diag.get("capital_efficiency_rejected"),
        "venue_bitvavo_selected": diag.get("venue_bitvavo_selected"),
        "venue_okx_selected": diag.get("venue_okx_selected"),
        "mfe_capture_samples": diag.get("mfe_capture_samples"),
        "average_mfe_capture_ratio": diag.get("average_mfe_capture_ratio"),
        "average_hold_minutes": diag.get("average_hold_minutes"),
        "net_eur_per_hour": diag.get("net_eur_per_hour"),
        "capital_deployed_eur": diag.get("capital_deployed_eur"),
        "capital_locked_eur": diag.get("capital_locked_eur"),
        "capital_utilization_pct": diag.get("capital_utilization_pct"),
        "realized_net_eur_session": diag.get("realized_net_eur_session"),
        "headroom_reject": diag.get("headroom_reject"),
        "extension_reject": diag.get("extension_reject"),
        "continuity_reject": diag.get("continuity_reject"),
        "headroom_unknown": diag.get("headroom_unknown"),
        "adaptive_trail_hold": diag.get("adaptive_trail_hold"),
        "adaptive_trail_harvest": diag.get("adaptive_trail_harvest"),
        "opportunity_candidates": diag.get("opportunity_candidates"),
        "opportunity_high_quality": diag.get("opportunity_high_quality"),
        "opportunity_reduced": diag.get("opportunity_reduced"),
        "opportunity_rejected": diag.get("opportunity_rejected"),
        "best_opportunity_symbol": diag.get("best_opportunity_symbol"),
        "best_opportunity_venue": diag.get("best_opportunity_venue"),
        "best_opportunity_score": diag.get("best_opportunity_score"),
        "best_opportunity_net_eur": diag.get("best_opportunity_net_eur"),
        "best_opportunity_net_eur_per_hour": diag.get(
            "best_opportunity_net_eur_per_hour"
        ),
        "best_opportunity_headroom_pct": diag.get("best_opportunity_headroom_pct"),
        "best_opportunity_extension_pct": diag.get("best_opportunity_extension_pct"),
        "best_opportunity_hold_minutes": diag.get("best_opportunity_hold_minutes"),
        "average_opportunity_score": diag.get("average_opportunity_score"),
        "capital_allocator_selected": diag.get("capital_allocator_selected"),
        "capital_allocator_skipped": diag.get("capital_allocator_skipped"),
        "volatility_reject": diag.get("volatility_reject"),
        "spread_reject": diag.get("spread_reject"),
        "timing_reject": diag.get("timing_reject"),
        "net_eur_per_capital_hour": (
            str(net_cap_hour.quantize(Decimal("0.0001"))) if net_cap_hour is not None else None
        ),
        "available_capital_eur": float(available_capital) if available_capital is not None else None,
        "venue_economics_bitvavo_candidates": venue_bv.get("trade_count")
        if isinstance(venue_bv, dict)
        else None,
        "venue_economics_bitvavo_avg_net": venue_bv.get("average_net_eur")
        if isinstance(venue_bv, dict)
        else None,
        "venue_economics_okx_candidates": venue_okx.get("trade_count")
        if isinstance(venue_okx, dict)
        else None,
        "venue_economics_okx_avg_net": venue_okx.get("average_net_eur")
        if isinstance(venue_okx, dict)
        else None,
        "execution_fill_rate": diag.get("execution_fill_rate"),
        "execution_maker_fill_rate": diag.get("execution_maker_fill_rate"),
        "execution_taker_fill_rate": diag.get("execution_taker_fill_rate"),
        "execution_toxic_fill_rate": diag.get("execution_toxic_fill_rate"),
        "execution_cancel_rate": diag.get("execution_cancel_rate"),
        "execution_replace_rate": diag.get("execution_replace_rate"),
        "execution_order_churn": diag.get("execution_order_churn"),
        "execution_observation_cancels": diag.get("execution_observation_cancels"),
        "market_regime": diag.get("market_regime"),
        "market_regime_confidence": diag.get("market_regime_confidence"),
        "market_regime_reasons": diag.get("market_regime_reasons"),
        "data_freshness_score": diag.get("data_freshness_score"),
        "regime_return_5m": diag.get("regime_return_5m"),
        "regime_realized_volatility": diag.get("regime_realized_volatility"),
        "regime_orderbook_imbalance": diag.get("regime_orderbook_imbalance"),
        "intelligence_observation_mode": diag.get("intelligence_observation_mode"),
        "capital_available_eur": diag.get("capital_available_eur"),
        "capital_reserved_eur": diag.get("capital_reserved_eur"),
        "capital_deployable_eur": diag.get("capital_deployable_eur"),
        "capital_reserve_need_pct": diag.get("capital_reserve_need_pct"),
        "adverse_selection_reject": diag.get("adverse_selection_reject"),
        "regime_reject": diag.get("regime_reject"),
        "stale_data_reject": diag.get("stale_data_reject"),
        **alphai,
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

"""Exchange-FIFO calendar PnL for operator KPIs (day/week, Europe/Amsterdam)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.live.dashboard_history import operator_day_start_utc, operator_week_start_utc

logger = logging.getLogger(__name__)

_CACHE_PATH = Path("./data/dashboard_pnl_cache.json")
_ANCHOR_PATH = Path("./data/dashboard_pnl_anchor.json")
_CACHE_TTL_SEC = 90.0
_last_refresh_mono = 0.0
_cache: dict[str, Any] = {}
_refresh_task: asyncio.Task[None] | None = None


def set_operator_pnl_anchor(when: datetime | None = None) -> datetime:
    """Pin Geïnd/week KPIs to start at ``when`` (operator dashboard reset)."""
    when = when or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    else:
        when = when.astimezone(UTC)
    payload = {"operator_anchor_utc": when.isoformat()}
    try:
        _ANCHOR_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ANCHOR_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        logger.exception("operator PnL anchor persist failed")
    # Immediate clean slate until the next exchange refresh.
    global _last_refresh_mono, _cache  # noqa: PLW0603
    _cache = {
        "updated_at": when.isoformat(),
        "source": "operator_reset",
        "day_start_utc": when.isoformat(),
        "week_start_utc": when.isoformat(),
        "operator_anchor_utc": when.isoformat(),
        "daily_eur": "0.00",
        "weekly_eur": "0.00",
        "sell_count": 0,
        "by_base_eur": {},
        "error": None,
    }
    _last_refresh_mono = time.monotonic()
    _persist_cache(_cache)
    return when


def clear_operator_pnl_anchor() -> None:
    try:
        if _ANCHOR_PATH.exists():
            _ANCHOR_PATH.unlink()
    except OSError:
        logger.exception("operator PnL anchor clear failed")


def get_operator_pnl_anchor() -> datetime | None:
    if not _ANCHOR_PATH.exists():
        return None
    try:
        raw = json.loads(_ANCHOR_PATH.read_text(encoding="utf-8"))
        ts = str((raw or {}).get("operator_anchor_utc") or "")
        if not ts:
            return None
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _effective_since(window_start: datetime) -> datetime:
    """Calendar window start, or operator reset anchor if still in today's NL day."""
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=UTC)
    else:
        window_start = window_start.astimezone(UTC)
    anchor = get_operator_pnl_anchor()
    if anchor is None:
        return window_start
    day = operator_day_start_utc()
    if anchor < day:
        clear_operator_pnl_anchor()
        return window_start
    return max(window_start, anchor)


_DEFAULT_PNL_BASES = frozenset(
    {
        "ETH",
        "SOL",
        "XRP",
        "ADA",
        "LINK",
        "DOT",
        "AVAX",
        "NEAR",
        "ATOM",
        "DOGE",
        "LTC",
        "ARB",
        "OP",
        "SUI",
        "APT",
        "UNI",
        "AAVE",
        "BNB",
        "BCH",
        "TRX",
        "INJ",
        "TAO",
    }
)


def _collect_bases(bridge: Any) -> set[str]:
    """Bases to fetch for calendar FIFO — include sold-out coins, not only holdings."""
    bases: set[str] = set(_DEFAULT_PNL_BASES)
    quote = str(getattr(bridge, "_quote", "EUR") or "EUR")
    exclude = getattr(bridge, "_exclude_bases", set()) or set()
    allowed = getattr(bridge, "_allowed_bases", None)
    venues = sorted(getattr(bridge, "_execute_venues", None) or {"bitvavo"})
    raw = getattr(bridge, "_venue_raw_balances", {}) or {}
    for venue in venues:
        for bal in raw.get(venue) or []:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == quote or asset in exclude:
                continue
            if allowed is not None and asset not in allowed:
                continue
            bases.add(asset)
    focus = getattr(bridge, "_focus_bases", None) or set()
    for asset in focus:
        a = str(asset or "").upper()
        if a and a != quote and a not in exclude:
            bases.add(a)
    for key in getattr(bridge, "_trail", {}) or {}:
        # trail keys are "venue:BASE"
        part = str(key).split(":", 1)[-1].upper()
        if part and part != quote and part not in exclude:
            bases.add(part)
    for key in getattr(bridge, "_cost_lots", {}) or {}:
        part = str(key).split(":", 1)[-1].upper()
        if part and part != quote and part not in exclude:
            bases.add(part)
    if allowed is not None:
        bases &= {str(a).upper() for a in allowed}
    return bases


async def _fetch_all_exchange_trades(
    bridge: Any,
    *,
    seed_ms: int,
) -> list[dict[str, Any]]:
    """Fetch exchange fills for all configured venues/bases (parallel)."""
    from bot.live.dashboard_reconcile import _fetch_exchange_trades

    venues = sorted(getattr(bridge, "_execute_venues", None) or {"bitvavo"})
    bases = sorted(_collect_bases(bridge))
    tasks = [
        _fetch_exchange_trades(bridge, venue=venue, base=base, since_ms=seed_ms)
        for venue in venues
        for base in bases
    ]
    if not tasks:
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_trades: list[dict[str, Any]] = []
    for row in results:
        if isinstance(row, Exception):
            logger.warning("calendar PnL trade fetch failed: %s", row)
            continue
        if row:
            all_trades.extend(row)
    all_trades.sort(key=lambda r: r["ts_ms"])
    return all_trades


async def compute_realized_since(
    bridge: Any,
    since: datetime,
    *,
    seed_days: int = 21,
) -> tuple[Decimal | None, list[dict[str, Any]]]:
    """Net realized PnL from exchange fills since ``since`` (FIFO, fees included).

    Returns ``(realized_eur, sell_events)``. Lots are per venue+base.
    """
    from bot.live.dashboard_reconcile import _replay_fifo

    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    else:
        since = since.astimezone(UTC)
    since_ms = int(since.timestamp() * 1000)
    seed_ms = since_ms - max(1, seed_days) * 24 * 3600 * 1000
    all_trades = await _fetch_all_exchange_trades(bridge, seed_ms=seed_ms)
    if not all_trades:
        return None, []
    _, realized_since, sell_events, _ = _replay_fifo(
        all_trades, since_ms=since_ms, quote=str(getattr(bridge, "_quote", "EUR") or "EUR")
    )
    return realized_since, sell_events


async def compute_realized_windows(
    bridge: Any,
    day_start: datetime,
    week_start: datetime,
    *,
    seed_days: int = 21,
) -> tuple[Decimal | None, Decimal | None, list[dict[str, Any]]]:
    """Fetch exchange fills once and derive daily + weekly FIFO PnL."""
    from bot.live.dashboard_reconcile import _replay_fifo

    if day_start.tzinfo is None:
        day_start = day_start.replace(tzinfo=UTC)
    else:
        day_start = day_start.astimezone(UTC)
    if week_start.tzinfo is None:
        week_start = week_start.replace(tzinfo=UTC)
    else:
        week_start = week_start.astimezone(UTC)

    earliest = min(day_start, week_start)
    since_ms_day = int(day_start.timestamp() * 1000)
    since_ms_week = int(week_start.timestamp() * 1000)
    seed_ms = int(earliest.timestamp() * 1000) - max(1, seed_days) * 24 * 3600 * 1000
    quote = str(getattr(bridge, "_quote", "EUR") or "EUR")

    all_trades = await _fetch_all_exchange_trades(bridge, seed_ms=seed_ms)
    if not all_trades:
        return None, None, []

    _, daily, sell_events, _ = _replay_fifo(
        all_trades, since_ms=since_ms_day, quote=quote
    )
    if day_start == week_start:
        return daily, daily, sell_events
    _, weekly, _, _ = _replay_fifo(all_trades, since_ms=since_ms_week, quote=quote)
    return daily, weekly, sell_events


async def refresh_calendar_pnl_cache(
    bridge: Any,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Refresh day/week net realized from exchange FIFO (throttled)."""
    global _last_refresh_mono, _cache  # noqa: PLW0603

    now_mono = time.monotonic()
    if not force and now_mono - _last_refresh_mono < _CACHE_TTL_SEC and _cache.get("daily_eur") is not None:
        return dict(_cache)

    day_start = _effective_since(operator_day_start_utc())
    week_start = _effective_since(operator_week_start_utc())
    daily: Decimal | None = None
    weekly: Decimal | None = None
    sell_events: list[dict[str, Any]] = []
    err: str | None = None
    try:
        daily, weekly, sell_events = await compute_realized_windows(
            bridge, day_start, week_start
        )
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:200]
        logger.exception("calendar PnL refresh failed")

    by_base: dict[str, float] = {}
    for ev in sell_events:
        key = f"{ev.get('venue')}:{ev.get('base')}"
        try:
            by_base[key] = by_base.get(key, 0.0) + float(ev.get("pnl_eur") or 0)
        except (TypeError, ValueError):
            continue
    top = sorted(by_base.items(), key=lambda kv: kv[1])
    updated = datetime.now(UTC).isoformat()
    anchor = get_operator_pnl_anchor()
    _cache = {
        "updated_at": updated,
        "source": "exchange_fifo" if anchor is None else "exchange_fifo_since_reset",
        "day_start_utc": day_start.isoformat(),
        "week_start_utc": week_start.isoformat(),
        "operator_anchor_utc": anchor.isoformat() if anchor else None,
        "daily_eur": str(daily.quantize(Decimal("0.01"))) if daily is not None else None,
        "weekly_eur": str(weekly.quantize(Decimal("0.01"))) if weekly is not None else None,
        "sell_count": len(sell_events),
        "by_base_eur": {k: round(v, 2) for k, v in top},
        "error": err,
    }
    _last_refresh_mono = now_mono
    _persist_cache(_cache)
    return dict(_cache)


def get_calendar_pnl_cache() -> dict[str, Any]:
    """Return cached day/week PnL (loads from disk on first read)."""
    global _cache  # noqa: PLW0603
    if _cache.get("daily_eur") is not None or _cache.get("weekly_eur") is not None:
        return dict(_cache)
    loaded = _load_cache()
    if loaded:
        _cache = loaded
    return dict(_cache)


def attach_calendar_pnl(session: dict[str, Any]) -> dict[str, Any]:
    """Merge cached calendar PnL into a session snapshot (non-blocking)."""
    cache = get_calendar_pnl_cache()
    if not cache:
        return session
    return {**session, "calendar_pnl": cache}


def schedule_calendar_pnl_refresh(bridge: Any) -> None:
    """Refresh exchange FIFO PnL in the background when cache is stale."""
    global _refresh_task  # noqa: PLW0603

    now_mono = time.monotonic()
    if (
        now_mono - _last_refresh_mono < _CACHE_TTL_SEC
        and _cache.get("daily_eur") is not None
    ):
        return
    if _refresh_task is not None and not _refresh_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _runner() -> None:
        try:
            await refresh_calendar_pnl_cache(bridge, force=False)
        except Exception:  # noqa: BLE001
            logger.exception("background calendar PnL refresh failed")

    _refresh_task = loop.create_task(_runner(), name="calendar-pnl-refresh")


def calendar_pnl_for_metrics() -> tuple[Decimal | None, Decimal | None, str | None]:
    """Daily, weekly EUR and source label for dashboard KPIs."""
    raw = get_calendar_pnl_cache()
    daily = _to_dec(raw.get("daily_eur"))
    weekly = _to_dec(raw.get("weekly_eur"))
    source = str(raw.get("source") or "") or None
    return daily, weekly, source


def clear_calendar_pnl_cache(*, path: Path | None = None) -> None:
    global _last_refresh_mono, _cache  # noqa: PLW0603
    _cache = {}
    _last_refresh_mono = 0.0
    target = path or _CACHE_PATH
    try:
        if target.exists():
            target.unlink()
    except OSError:
        logger.exception("calendar PnL cache clear failed path=%s", target)


def _to_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _persist_cache(data: dict[str, Any]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        logger.exception("calendar PnL cache persist failed")


def _load_cache() -> dict[str, Any]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}

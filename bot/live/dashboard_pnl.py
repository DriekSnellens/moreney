"""Exchange-FIFO calendar PnL for operator KPIs (day/week, Europe/Amsterdam)."""

from __future__ import annotations

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
_CACHE_TTL_SEC = 90.0
_last_refresh_mono = 0.0
_cache: dict[str, Any] = {}


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


async def compute_realized_since(
    bridge: Any,
    since: datetime,
    *,
    seed_days: int = 14,
) -> Decimal | None:
    """Net realized PnL from exchange fills since ``since`` (FIFO, fees included)."""
    from bot.live.dashboard_reconcile import _fetch_exchange_trades, _replay_fifo

    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    else:
        since = since.astimezone(UTC)
    since_ms = int(since.timestamp() * 1000)
    seed_ms = since_ms - max(1, seed_days) * 24 * 3600 * 1000
    venues = sorted(getattr(bridge, "_execute_venues", None) or {"bitvavo"})
    all_trades: list[dict[str, Any]] = []
    for venue in venues:
        for base in sorted(_collect_bases(bridge)):
            rows = await _fetch_exchange_trades(
                bridge, venue=venue, base=base, since_ms=seed_ms
            )
            all_trades.extend(rows)
    if not all_trades:
        return None
    all_trades.sort(key=lambda r: r["ts_ms"])
    _, realized_since, _, _ = _replay_fifo(
        all_trades, since_ms=since_ms, quote=str(getattr(bridge, "_quote", "EUR") or "EUR")
    )
    return realized_since


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

    day_start = operator_day_start_utc()
    week_start = operator_week_start_utc()
    daily: Decimal | None = None
    weekly: Decimal | None = None
    err: str | None = None
    try:
        if day_start == week_start:
            daily = weekly = await compute_realized_since(bridge, day_start)
        else:
            daily = await compute_realized_since(bridge, day_start)
            weekly = await compute_realized_since(bridge, week_start)
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:200]
        logger.exception("calendar PnL refresh failed")

    updated = datetime.now(UTC).isoformat()
    _cache = {
        "updated_at": updated,
        "source": "exchange_fifo",
        "day_start_utc": day_start.isoformat(),
        "week_start_utc": week_start.isoformat(),
        "daily_eur": str(daily.quantize(Decimal("0.01"))) if daily is not None else None,
        "weekly_eur": str(weekly.quantize(Decimal("0.01"))) if weekly is not None else None,
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

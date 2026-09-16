"""BTC hold dashboard origin — MTM vs a fixed start baseline.

``today`` (Europe/Amsterdam calendar day of first capture) is the origin.
The baseline is written once and not overwritten, so later marks show green
above / red below that starting EUR value.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_NL = ZoneInfo("Europe/Amsterdam")
_DEFAULT_PATH = Path("./data/momentum_hold_baseline.json")


def baseline_path(settings: Any | None = None) -> Path:
    raw = None
    if settings is not None:
        raw = getattr(settings, "momentum_hold_baseline_path", None)
    return Path(str(raw or _DEFAULT_PATH))


def amsterdam_day(now: datetime | None = None) -> str:
    dt = now or datetime.now(_NL)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_NL)
    else:
        dt = dt.astimezone(_NL)
    return dt.date().isoformat()


def load_baseline(path: Path | None = None) -> dict[str, Any] | None:
    p = path or _DEFAULT_PATH
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("hold baseline: read failed %s: %s", p, exc)
        return None
    if not isinstance(raw, dict):
        return None
    try:
        baseline_eur = float(raw.get("baseline_eur") or 0.0)
    except (TypeError, ValueError):
        return None
    if baseline_eur <= 0:
        return None
    return raw


def ensure_baseline(
    *,
    value_eur: float,
    qty_btc: float,
    mark_eur: float | None,
    by_venue: dict[str, float] | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return existing baseline, or capture ``value_eur`` once as today's origin."""
    p = path or _DEFAULT_PATH
    existing = load_baseline(p)
    if existing is not None:
        return existing
    if value_eur <= 0 or qty_btc <= 0:
        return {
            "pending": True,
            "reason": "no_btc_inventory",
            "baseline_eur": None,
            "qty_btc": qty_btc,
            "value_eur": value_eur,
        }
    captured = now or datetime.now(_NL)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=_NL)
    else:
        captured = captured.astimezone(_NL)
    row = {
        "day_key": amsterdam_day(captured),
        "captured_at": captured.isoformat(),
        "baseline_eur": round(float(value_eur), 2),
        "qty_btc": float(qty_btc),
        "mark_eur": round(float(mark_eur), 2) if mark_eur else None,
        "by_venue_btc": {
            str(k): float(v) for k, v in sorted((by_venue or {}).items())
        },
        "note": "Dashboard origin for BTC hold MTM (green above / red below).",
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        logger.warning("hold baseline: write failed %s: %s", p, exc)
    return row


def mtm_snapshot(
    *,
    value_eur: float,
    qty_btc: float,
    mark_eur: float | None,
    by_venue: dict[str, float] | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Current BTC inventory vs persisted origin baseline."""
    baseline = ensure_baseline(
        value_eur=value_eur,
        qty_btc=qty_btc,
        mark_eur=mark_eur,
        by_venue=by_venue,
        path=path,
        now=now,
    )
    base_eur = baseline.get("baseline_eur")
    try:
        base_f = float(base_eur) if base_eur is not None else None
    except (TypeError, ValueError):
        base_f = None
    pnl = None if base_f is None else float(value_eur) - base_f
    pnl_pct = None if (base_f is None or base_f <= 0 or pnl is None) else pnl / base_f
    return {
        "qty_btc": round(float(qty_btc), 8),
        "mark_eur": round(float(mark_eur), 2) if mark_eur else None,
        "value_eur": round(float(value_eur), 2),
        "by_venue_btc": {
            str(k): round(float(v), 8) for k, v in sorted((by_venue or {}).items())
        },
        "baseline_eur": round(base_f, 2) if base_f is not None else None,
        "baseline_day": baseline.get("day_key"),
        "baseline_captured_at": baseline.get("captured_at"),
        "pnl_eur": round(pnl, 2) if pnl is not None else None,
        "pnl_pct": round(pnl_pct, 6) if pnl_pct is not None else None,
        "pending_baseline": bool(baseline.get("pending")),
    }

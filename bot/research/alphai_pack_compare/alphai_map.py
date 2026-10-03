"""Real AlphaI date→allowed-alt maps (no price proxy, no lookahead)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE


def load_merged_sessions(path: Path | str) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    sessions = raw.get("sessions") if isinstance(raw, dict) else raw
    if not isinstance(sessions, list):
        return []
    return [s for s in sessions if isinstance(s, dict)]


def _session_day(sess: Mapping[str, Any]) -> str:
    ga = str(sess.get("generated_at") or sess.get("session_id") or "")
    return ga[:10] if len(ga) >= 10 else ""


def _session_ts(sess: Mapping[str, Any]) -> float:
    ga = str(sess.get("generated_at") or "")
    try:
        return datetime.fromisoformat(ga.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def picks_asof_hour(
    sessions: Sequence[Mapping[str, Any]],
    *,
    hour_utc: int = 7,
    minute_grace: int = 8,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
) -> dict[str, set[str]]:
    """Latest session at or before ``hour_utc:minute_grace`` each UTC day.

    Matches the live desk/clip buy-window cadence (07/13/16, minute < 8).
    """
    univ = {str(b).upper() for b in universe}
    by_day: dict[str, list[Mapping[str, Any]]] = {}
    for sess in sessions:
        day = _session_day(sess)
        if not day:
            continue
        by_day.setdefault(day, []).append(sess)

    out: dict[str, set[str]] = {}
    for day, rows in sorted(by_day.items()):
        cutoff = datetime(
            int(day[:4]),
            int(day[5:7]),
            int(day[8:10]),
            int(hour_utc),
            int(minute_grace),
            tzinfo=UTC,
        ).timestamp()
        eligible = [s for s in rows if _session_ts(s) <= cutoff]
        chosen = max(eligible, key=_session_ts) if eligible else min(rows, key=_session_ts)
        picks = {
            str(p.get("base") or "").upper()
            for p in (chosen.get("picks") or [])
            if isinstance(p, Mapping) and p.get("base")
        }
        out[day] = {b for b in picks if b in univ}
    return out


def build_alt_allow(
    *,
    start: str,
    end: str,
    ohlc_dates: Sequence[str],
    daily_picks: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    """Forward-fill picks across the sim window. Empty before the first real day."""
    dates = [d for d in ohlc_dates if start <= d <= end]
    out: dict[str, set[str]] = {}
    last: set[str] = set()
    seen = False
    for d in dates:
        if d in daily_picks:
            last = set(daily_picks[d])
            seen = True
        out[d] = set(last) if seen else set()
    return out


def coverage_report(
    sessions: Sequence[Mapping[str, Any]],
    daily_picks: Mapping[str, set[str]],
) -> dict[str, Any]:
    days = sorted(daily_picks)
    return {
        "n_sessions": len(sessions),
        "n_days": len(days),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "days": days,
        "picks_by_day": {d: sorted(v) for d, v in sorted(daily_picks.items())},
        "unique_bases": sorted({b for s in daily_picks.values() for b in s}),
    }

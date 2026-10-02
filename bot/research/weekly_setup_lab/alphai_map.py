"""Build date→allowed-alt maps for residual weekly sleeve ±AlphaI.

Real AlphaI history in ``pick_outcomes`` is only ~6 days. Longer windows use a
causal *price* proxy: short-horizon excess + liquidity membership (no headlines,
no lookahead). Both are coin-agnostic set filters — residual rank stays primary.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_btc_rs_clip import closes_of, quote_vol, rs_excess
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_exit_lab.engine import rows_through


def load_pick_outcome_sessions(path: Path | str) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text())
    sessions = raw.get("sessions") if isinstance(raw, dict) else raw
    if not isinstance(sessions, list):
        return []
    return [s for s in sessions if isinstance(s, dict)]


def real_alphai_daily_picks(
    sessions: Sequence[Mapping[str, Any]],
    *,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
) -> dict[str, set[str]]:
    """Last session of each UTC day → pick bases ∩ universe."""
    univ = {str(b).upper() for b in universe}
    by_day: dict[str, Mapping[str, Any]] = {}
    for sess in sessions:
        ga = str(sess.get("generated_at") or sess.get("session_id") or "")
        if len(ga) < 10:
            continue
        day = ga[:10]
        prev = by_day.get(day)
        if prev is None or str(sess.get("generated_at") or "") >= str(
            prev.get("generated_at") or ""
        ):
            by_day[day] = sess
    out: dict[str, set[str]] = {}
    for day, sess in sorted(by_day.items()):
        picks = {
            str(p.get("base") or "").upper()
            for p in (sess.get("picks") or [])
            if isinstance(p, Mapping) and p.get("base")
        }
        out[day] = {b for b in picks if b in univ}
    return out


def proxy_alphai_picks(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    *,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    top_n: int = 6,
    short_lb: int = 3,
    avoid_lb: int = 5,
    skip_days: int = 1,
    min_qvol_eur: float = 80_000.0,
) -> set[str]:
    """Causal AlphaI-style membership from price only.

    Keep names with positive short excess vs BTC, drop the weakest avoid-lb
    quartile, rank the rest by short excess × log-volume, take top_n.
    """
    btc_c = closes_of(rows_through(ohlc.get("BTC") or [], date))
    scored: list[tuple[float, str]] = []
    shorts: dict[str, float] = {}
    avoids: dict[str, float] = {}
    for base in universe:
        rows = rows_through(ohlc.get(base) or [], date)
        cl = closes_of(rows)
        xs = rs_excess(cl, btc_c, lb=short_lb, skip=skip_days)
        xa = rs_excess(cl, btc_c, lb=avoid_lb, skip=skip_days)
        qv = quote_vol(rows)
        if xs is None or qv < min_qvol_eur:
            continue
        shorts[base] = float(xs)
        if xa is not None:
            avoids[base] = float(xa)
        if xs <= 0:
            continue
        scored.append((float(xs) * math.log1p(qv), base))
    if avoids:
        cut = sorted(avoids.values())[max(0, len(avoids) // 4 - 1)]
        weak = {b for b, v in avoids.items() if v <= cut}
    else:
        weak = set()
    ranked = [b for s, b in sorted(scored, reverse=True) if b not in weak]
    return set(ranked[: max(1, int(top_n))])


def build_alt_allow(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    mode: str,
    sessions: Sequence[Mapping[str, Any]] | None = None,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    top_n: int = 6,
) -> dict[str, set[str]] | None:
    """Return date→allowed bases, or None when AlphaI is off.

    Modes:
      off              → None
      real_gate/intersect → real picks, forward-filled (empty when unknown)
      proxy_gate/intersect → proxy computed every day
    """
    m = str(mode or "off").lower()
    if m in {"", "off", "none"}:
        return None
    dates = [
        datetime.fromtimestamp(int(r[0]) / 1000, UTC).strftime("%Y-%m-%d")
        for r in (ohlc.get("BTC") or [])
    ]
    dates = [d for d in dates if start <= d <= end]
    if m.startswith("real"):
        daily = real_alphai_daily_picks(sessions or [], universe=universe)
        out: dict[str, set[str]] = {}
        last: set[str] = set()
        seen_real = False
        for d in dates:
            if d in daily:
                last = set(daily[d])
                seen_real = True
            # Before first real session: empty gate (no AlphaI yet).
            out[d] = set(last) if seen_real else set()
        return out
    if m.startswith("proxy"):
        return {
            d: proxy_alphai_picks(ohlc, d, universe=universe, top_n=top_n) for d in dates
        }
    raise ValueError(f"unknown alphai mode: {mode}")


def allow_mode_name(mode: str) -> str:
    m = str(mode or "off").lower()
    if m.endswith("intersect"):
        return "intersect"
    return "gate"

"""Price confirmation for AlphaI headline picks.

Headline bullish ≠ outperformance. Demote picks that lag BTC intraday so
strong-clip / hold treatment tracks relative strength, not narrative score.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping


def classify_price_lags(
    day_returns_pct: Mapping[str, float],
    *,
    btc_base: str = "BTC",
    lag_vs_btc_pp: float = 1.5,
) -> frozenset[str]:
    """Return bases that underperform BTC by at least ``lag_vs_btc_pp`` points.

    Example (2026-09-04 AMS): XRP −4.25% vs BTC −2.18% → lag 2.07pp → lagging.
    LINK −1.81% vs BTC −2.18% → outperforming → not lagging.
    """
    btc_key = str(btc_base or "BTC").upper()
    if btc_key not in day_returns_pct:
        return frozenset()
    try:
        btc_ret = float(day_returns_pct[btc_key])
    except (TypeError, ValueError):
        return frozenset()

    lagging: set[str] = set()
    threshold = float(lag_vs_btc_pp)
    for raw_base, raw_ret in day_returns_pct.items():
        base = str(raw_base or "").upper()
        if not base or base == btc_key:
            continue
        try:
            ret = float(raw_ret)
        except (TypeError, ValueError):
            continue
        # Negative excess vs BTC = lagging.
        if (ret - btc_ret) <= -threshold:
            lagging.add(base)
    return frozenset(lagging)


def build_price_check(
    pick_bases: list[str] | tuple[str, ...] | set[str],
    day_returns_pct: Mapping[str, float],
    *,
    btc_base: str = "BTC",
    lag_vs_btc_pp: float = 1.5,
    asof: datetime | None = None,
) -> dict[str, Any]:
    """Structured price-check block for daily reports / dashboards."""
    instant = asof or datetime.now(UTC)
    btc_key = str(btc_base or "BTC").upper()
    btc_ret = None
    if btc_key in day_returns_pct:
        try:
            btc_ret = round(float(day_returns_pct[btc_key]), 4)
        except (TypeError, ValueError):
            btc_ret = None

    lagging = classify_price_lags(
        day_returns_pct,
        btc_base=btc_key,
        lag_vs_btc_pp=lag_vs_btc_pp,
    )
    rows: dict[str, Any] = {}
    confirmed: list[str] = []
    lagged_picks: list[str] = []
    for raw in pick_bases:
        base = str(raw or "").upper()
        if not base:
            continue
        if base not in day_returns_pct:
            continue
        try:
            ret = float(day_returns_pct[base])
        except (TypeError, ValueError):
            continue
        vs_btc = None if btc_ret is None else round(ret - float(btc_ret), 4)
        is_lag = base in lagging
        rows[base] = {
            "day_pct": round(ret, 4),
            "vs_btc_pp": vs_btc,
            "lagging": is_lag,
        }
        if is_lag:
            lagged_picks.append(base)
        else:
            confirmed.append(base)

    return {
        "asof": instant.astimezone(UTC).isoformat(),
        "btc_base": btc_key,
        "btc_day_pct": btc_ret,
        "lag_vs_btc_pp": float(lag_vs_btc_pp),
        "picks": rows,
        "confirmed": confirmed,
        "lagging": lagged_picks,
    }


def enrich_daily_with_price_check(
    daily: dict[str, Any] | None,
    day_returns_pct: Mapping[str, float],
    *,
    lag_vs_btc_pp: float = 1.5,
    btc_base: str = "BTC",
    asof: datetime | None = None,
) -> dict[str, Any] | None:
    """Return a copy of *daily* with ``price_check`` attached (no-op if empty)."""
    if not isinstance(daily, dict):
        return daily
    picks = [
        str(p.get("base") or "").upper()
        for p in (daily.get("picks") or [])
        if isinstance(p, dict) and p.get("base")
    ]
    # Always include BTC so lag math works even when BTC is not a pick.
    needed = set(picks) | {str(btc_base or "BTC").upper()}
    filtered = {
        k: float(v)
        for k, v in day_returns_pct.items()
        if str(k).upper() in needed
    }
    check = build_price_check(
        picks,
        filtered,
        btc_base=btc_base,
        lag_vs_btc_pp=lag_vs_btc_pp,
        asof=asof,
    )
    out = dict(daily)
    out["price_check"] = check
    return out

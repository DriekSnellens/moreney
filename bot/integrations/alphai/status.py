"""Merge AlphaI monitor + live bridge snapshots for dashboards."""

from __future__ import annotations

from typing import Any


def merge_alphai_status(session: dict[str, Any], bridge: dict[str, Any]) -> dict[str, Any]:
    """Combine PaperRunner monitor fields with live bridge block state."""
    runner = session.get("alphai") if isinstance(session.get("alphai"), dict) else {}
    bridge_box = bridge.get("alphai") if isinstance(bridge.get("alphai"), dict) else {}
    out: dict[str, Any] = dict(runner)
    for key in (
        "enabled",
        "macro_active",
        "macro_reduce_only",
        "blocked_bases",
        "blocked_detail",
        "skips",
    ):
        if key in bridge_box and bridge_box.get(key) is not None:
            out[key] = bridge_box.get(key)
    if bridge_box.get("macro_active") is not None:
        out["macro_active"] = bridge_box.get("macro_active")
    if not out.get("blocked_bases") and bridge.get("alphai_blocked_bases"):
        out["blocked_bases"] = bridge.get("alphai_blocked_bases")
    if not out.get("blocked_detail") and bridge.get("alphai_blocked_detail"):
        out["blocked_detail"] = bridge.get("alphai_blocked_detail")
    if out.get("macro_active") is None and bridge.get("alphai_macro_active") is not None:
        out["macro_active"] = bridge.get("alphai_macro_active")
    if out.get("enabled") is None:
        out["enabled"] = bool(bridge.get("alphai_enabled"))
    return out


def alphai_metrics(session: dict[str, Any], bridge: dict[str, Any]) -> dict[str, Any]:
    box = merge_alphai_status(session, bridge)
    blocked = box.get("blocked_bases") or []
    headlines = box.get("headlines") or []
    top_headline = None
    if isinstance(headlines, list) and headlines:
        first = headlines[0]
        if isinstance(first, dict):
            top_headline = str(first.get("title") or "")[:120] or None
    blocked_list = blocked if isinstance(blocked, list) else list(blocked)
    detail = box.get("blocked_detail") if isinstance(box.get("blocked_detail"), dict) else {}
    would_block = {
        str(k): str(v)
        for k, v in detail.items()
        if k and str(k) != "_MACRO_" and not str(k).startswith("_")
    }
    headline_rows = [h for h in headlines if isinstance(h, dict)][:8]
    from bot.core.config import get_settings
    from bot.integrations.alphai.daily_recommendations import load_daily_recommendations

    picks_path = getattr(
        get_settings(),
        "alphai_daily_recommendations_path",
        "data/alphai/daily_recommendations.json",
    )
    daily = load_daily_recommendations(picks_path) or {}
    pick_bases = [p.get("base") for p in (daily.get("picks") or []) if isinstance(p, dict)]
    avoid_bases = [p.get("base") for p in (daily.get("avoid") or []) if isinstance(p, dict)]

    return {
        "alphai_enabled": bool(box.get("enabled")),
        "alphai_observation_mode": bool(box.get("observation_mode")),
        "alphai_macro_active": bool(box.get("macro_active") or box.get("macro_reduce_only")),
        "alphai_blocked_bases": blocked_list,
        "alphai_blocked_count": len(blocked_list),
        "alphai_would_block_count": len(would_block),
        "alphai_would_block": would_block,
        "alphai_macro_headline": detail.get("_MACRO_"),
        "alphai_rate_limit_remaining": box.get("rate_limit_remaining"),
        "alphai_polls": box.get("polls"),
        "alphai_skips": box.get("skips"),
        "alphai_last_poll_at": box.get("last_poll_at"),
        "alphai_last_error": box.get("last_error"),
        "alphai_headline_count": box.get("headline_count")
        if box.get("headline_count") is not None
        else (len(headlines) if isinstance(headlines, list) else 0),
        "alphai_top_headline": top_headline,
        "alphai_headlines": headline_rows,
    }

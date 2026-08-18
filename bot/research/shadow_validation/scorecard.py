"""Decision-oriented live validation scorecard. Frozen criteria only."""

from __future__ import annotations

from typing import Any

from bot.research.shadow_validation.protocol import (
    HISTORICAL_FINAL_VALIDATION,
    MIN_CALENDAR_DAYS,
    MIN_COMPLETE_WINDOWS,
    PREFERRED_CALENDAR_DAYS,
    PREFERRED_COMPLETE_WINDOWS,
)
from bot.research.shadow_validation.verdict import decide


def progress_sentence(snapshot: dict[str, Any]) -> str:
    windows = int(snapshot.get("complete_windows") or 0)
    days = float(snapshot.get("calendar_days") or 0.0)
    need_w = max(0, MIN_COMPLETE_WINDOWS - windows)
    need_d = max(0.0, float(MIN_CALENDAR_DAYS) - days)
    if not snapshot.get("sample_complete"):
        parts = []
        if need_w:
            parts.append(f"{need_w} more complete windows")
        if need_d > 0:
            parts.append(f"{need_d:.2f} more calendar days")
        if not parts:
            need_v = max(0, int(snapshot.get("min_valid_observations") or 100) - int(snapshot.get("valid_observations") or 0))
            if need_v:
                parts.append(f"{need_v} more valid observations")
        joined = " and ".join(parts) if parts else "the remaining frozen sample"
        return f"We need {joined} before the frozen minimum sample is reached. Do not stop early."
    pref_w = max(0, PREFERRED_COMPLETE_WINDOWS - windows)
    pref_d = max(0.0, float(PREFERRED_CALENDAR_DAYS) - days)
    if snapshot.get("preferred_complete"):
        return "Preferred collection target reached. Official frozen verdict stands. Production execution remains DISABLED."
    extra = []
    if pref_w:
        extra.append(f"{pref_w} windows")
    if pref_d > 0:
        extra.append(f"{pref_d:.2f} days")
    return (
        "Official frozen minimum sample is complete. Passive collection continues toward "
        + " and ".join(extra)
        + ". No retuning."
    )


def build_scorecard(
    snapshot: dict[str, Any],
    decision: dict[str, Any] | None = None,
    *,
    integrity: str = "UNKNOWN",
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = decision or decide(snapshot)
    rates = snapshot.get("rates") or {}
    funnel = snapshot.get("funnel") or {}
    adv = snapshot.get("adverse") or {}
    stab = snapshot.get("stability") or {}
    pred = snapshot.get("prediction_gap") or {}
    mkt = snapshot.get("market_gap") or {}
    ident = identity or {}
    hist = HISTORICAL_FINAL_VALIDATION
    known = []
    unknown = []
    if snapshot.get("n_completed"):
        known.append("Live candidates have been observed.")
    else:
        unknown.append("No live candidates completed yet.")
    if snapshot.get("sample_complete"):
        known.append("Frozen minimum sample (20 windows and 7 days) is complete.")
    else:
        unknown.append("Frozen minimum sample is not complete.")
    if integrity != "VALID":
        unknown.append(f"VALIDATION_INTEGRITY is {integrity}.")
    else:
        known.append("VALIDATION_INTEGRITY is VALID.")
    return {
        "CURRENT_RESEARCH_STATUS": {
            "phase": "SHADOW_PAPER_VALIDATION",
            "research_verdict": hist.get("FINAL_VALIDATION_VERDICT"),
            "production_execution": "DISABLED",
            "frozen_strategy": "cross_venue_dislocation",
            "route": "okx -> bitvavo",
            "ROUTE_UNIVERSE_LIMITED": True,
        },
        "VALIDATION_INTEGRITY": integrity,
        "FROZEN_STRATEGY": {
            "strategy_fingerprint": ident.get("strategy_fingerprint") or snapshot.get("strategy_fingerprint"),
            "config_hash": ident.get("config_hash"),
            "git_commit": ident.get("git_commit"),
            "validation_run_id": ident.get("validation_run_id"),
            "runtime_id": ident.get("runtime_id"),
            "dislocation_bps": 40.0,
            "horizon_ms": 5000,
            "route": "okx|bitvavo",
        },
        "A_SAMPLE": {
            "windows": snapshot.get("complete_windows"),
            "windows_required": MIN_COMPLETE_WINDOWS,
            "days": snapshot.get("calendar_days"),
            "days_required": MIN_CALENDAR_DAYS,
            "preferred_windows": PREFERRED_COMPLETE_WINDOWS,
            "preferred_days": PREFERRED_CALENDAR_DAYS,
            "signals": snapshot.get("n_candidates"),
            "valid_outcomes": snapshot.get("valid_observations"),
            "sample_complete": snapshot.get("sample_complete"),
            "preferred_complete": snapshot.get("preferred_complete"),
        },
        "B_FILL_REALISM": {
            "full_fill_rate": rates.get("fill_rate"),
            "partial_fill_rate": rates.get("partial_fill_rate"),
            "no_fill_rate": rates.get("no_fill_rate"),
            "quote_disappearance_rate": (funnel.get("quote_disappeared") or {}).get("rate"),
            "funnel": funnel,
            "FULL_FILL": snapshot.get("FULL_FILL"),
            "PARTIAL_FILL": snapshot.get("PARTIAL_FILL"),
            "NO_FILL": snapshot.get("NO_FILL"),
        },
        "C_EXECUTION_GAP": {
            "RESEARCH_EXPECTATION": snapshot.get("RESEARCH_EXPECTED_NET"),
            "LIVE_SHADOW_EXECUTION": snapshot.get("LIVE_SHADOW_EXECUTION_NET"),
            "gap": snapshot.get("execution_gap"),
            "prediction_gap": pred.get("all_candidates"),
            "label": "prediction_gap = shadow_execution_net - expected_net",
            "not_profit": True,
        },
        "D_MARKET_GAP": {
            "LIVE_SHADOW_EXECUTION": snapshot.get("LIVE_SHADOW_EXECUTION_NET"),
            "REALIZED_MARKET": snapshot.get("REALIZED_MARKET_NET"),
            "gap": mkt.get("all_candidates"),
            "label": "market_gap = realized_market_net - shadow_execution_net",
            "not_profit": True,
        },
        "E_ADVERSE_SELECTION": {
            "research_assumption_bps": adv.get("research_adverse_assumption_bps"),
            "observed_1s": adv.get("realized_1s_markout"),
            "observed_5s": adv.get("realized_5s_markout"),
            "observed_30s": adv.get("realized_30s_markout"),
            "observed_60s": adv.get("realized_60s_markout"),
            "adverse_gap": adv.get("adverse_gap"),
            "descriptive_only": True,
        },
        "F_STABILITY": {
            "positive_windows": stab.get("positive_windows"),
            "negative_windows": stab.get("negative_windows"),
            "top_symbol_share": stab.get("top_symbol_share") or snapshot.get("top_symbol_share"),
            "top_window_share": stab.get("top_window_share") or snapshot.get("top_window_share"),
            "top_hour_share": snapshot.get("top_hour_share"),
            "ROUTE_UNIVERSE_LIMITED": True,
        },
        "G_CURRENT_VERDICT": {
            "SHADOW_VALIDATION_VERDICT": decision.get("SHADOW_VALIDATION_VERDICT"),
            "NEXT_ACTION": decision.get("NEXT_ACTION"),
            "WHY": decision.get("WHY"),
            "provisional": decision.get("provisional"),
            "production_execution": "DISABLED",
        },
        "known": known,
        "unknown": unknown,
        "progress_sentence": progress_sentence(snapshot),
        "historical_research_expectation": {
            "label": "RESEARCH EXPECTATION",
            "BASELINE_EXECUTION_NET_EUR": hist.get("BASELINE_EXECUTION_NET_EUR"),
            "n_canonical_fills": hist.get("n_canonical_fills"),
            "n_candidates": hist.get("n_candidates"),
        },
    }

"""Scenario matrix generation with staged elimination."""

from __future__ import annotations

from itertools import product
from typing import Any

from bot.research.execution_realism.config import (
    CANCEL_SCENARIOS,
    FILL_MODELS,
    HEDGE_SCENARIOS,
    LATENCY_SCENARIOS,
    STAGE1_MAX_SCENARIOS,
    STAGE2_MAX_SCENARIOS,
)


def scenario_id(fill_model: str, latency: str, hedge: str, cancel: str) -> str:
    return f"{fill_model}|{latency}|{hedge}|{cancel}"


def full_matrix() -> list[dict[str, str]]:
    rows = []
    for fm, lat, hg, cn in product(FILL_MODELS, LATENCY_SCENARIOS, HEDGE_SCENARIOS, CANCEL_SCENARIOS):
        rows.append({
            "fill_model": fm,
            "latency_scenario": lat,
            "hedge_scenario": hg,
            "cancel_scenario": cn,
            "scenario_id": scenario_id(fm, lat, hg, cn),
        })
    return rows


def stage1_screen() -> list[dict[str, str]]:
    """Reduced matrix for fast screening: one cancel, bounded combos."""
    rows = []
    for fm in FILL_MODELS:
        for lat in LATENCY_SCENARIOS:
            for hg in HEDGE_SCENARIOS:
                rows.append({
                    "fill_model": fm,
                    "latency_scenario": lat,
                    "hedge_scenario": hg,
                    "cancel_scenario": "NORMAL",
                    "scenario_id": scenario_id(fm, lat, hg, "NORMAL"),
                })
    return rows[:STAGE1_MAX_SCENARIOS]


def stage2_survivors(stage1_results: list[dict[str, Any]], *, threshold_eur: float = 0.0) -> list[dict[str, str]]:
    """Keep only scenarios with positive execution NET from stage 1."""
    survivors = []
    for r in stage1_results:
        from decimal import Decimal
        net = Decimal(str(r.get("execution_net_eur") or 0))
        if net > Decimal(str(threshold_eur)):
            survivors.append({
                "fill_model": r["fill_model"],
                "latency_scenario": r["latency_scenario"],
                "hedge_scenario": r["hedge_scenario"],
                "cancel_scenario": r.get("cancel_scenario", "NORMAL"),
                "scenario_id": r["scenario_id"],
            })
    return survivors[:STAGE2_MAX_SCENARIOS]


def reference_scenarios() -> list[dict[str, str]]:
    """Key scenarios for the dashboard comparison table."""
    refs = []
    for lat in LATENCY_SCENARIOS:
        refs.append({
            "fill_model": "EXISTING_TRADE_THROUGH",
            "latency_scenario": lat,
            "hedge_scenario": "NORMAL",
            "cancel_scenario": "NORMAL",
            "scenario_id": scenario_id("EXISTING_TRADE_THROUGH", lat, "NORMAL", "NORMAL"),
        })
    for fm in FILL_MODELS:
        refs.append({
            "fill_model": fm,
            "latency_scenario": "NORMAL",
            "hedge_scenario": "NORMAL",
            "cancel_scenario": "NORMAL",
            "scenario_id": scenario_id(fm, "NORMAL", "NORMAL", "NORMAL"),
        })
    seen = set()
    deduped = []
    for r in refs:
        if r["scenario_id"] not in seen:
            seen.add(r["scenario_id"])
            deduped.append(r)
    return deduped

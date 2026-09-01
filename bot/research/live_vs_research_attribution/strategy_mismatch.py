"""Strategy mismatch detection between research and live paths."""

from __future__ import annotations

from typing import Any


RESEARCH_STRATEGY = "cross_venue_dislocation"
LIVE_STRATEGY = "maker_inventory / alt-beta micro recycle"
LIVE_EXECUTOR = "MicroBudgetLiveExecutor"


def strategy_comparison_table() -> list[dict[str, str]]:
    return [
        {
            "component": "Signal",
            "research": "CrossExchangeArbitrageStrategy / CVD frozen candidate — simultaneous buy-low sell-high dislocation",
            "live": "MakerInventoryStrategy — per-venue maker/taker alt recycle, momentum gates",
            "same": "NO",
        },
        {
            "component": "Universe",
            "research": "okx|bitvavo route, 67k signals, ETHEUR-heavy (~58% share)",
            "live": "40+ EUR alts on Bitvavo+OKX, focus_bases allowlist, ETH long-hold",
            "same": "NO",
        },
        {
            "component": "Entry",
            "research": "Taker-taker round-trip on dislocation above NET threshold",
            "live": "Maker buys with momentum/headroom gates; taker only when BE+ exit",
            "same": "NO",
        },
        {
            "component": "Fees",
            "research": "Frozen venue taker fees in canonical replay",
            "live": "Actual exchange fees; maker/taker mix; fee-aware BE tracking",
            "same": "PARTIAL",
        },
        {
            "component": "Slippage",
            "research": "Modeled slippage + execution buffer in profitability engine",
            "live": "Order-book depth + partial fills; maker queue simulation for paper legs",
            "same": "PARTIAL",
        },
        {
            "component": "Execution",
            "research": "Canonical replay — instant round-trip fills at VWAP",
            "live": "Live taker on Bitvavo/OKX; maker paper; resting order management",
            "same": "NO",
        },
        {
            "component": "Exit",
            "research": "Immediate round-trip close in replay",
            "live": "Trail/soft-arm/exit-engine, time_stop_below_be, momentum exits",
            "same": "NO",
        },
        {
            "component": "Inventory",
            "research": "No persistent inventory (arb round-trip)",
            "live": "FIFO session_lots, velocity sleeve, underwater blocks, cross-venue dedup",
            "same": "NO",
        },
        {
            "component": "GOE",
            "research": "Not on CVD replay path",
            "live": "Opportunity engine available but disabled (observation mode intelligence)",
            "same": "NO",
        },
        {
            "component": "Risk",
            "research": "Research gates (OOS, stability, concentration)",
            "live": "RiskEngine + micro budget cap + daily loss + kill switch",
            "same": "PARTIAL",
        },
    ]


def analyze_strategy_mismatch() -> dict[str, Any]:
    table = strategy_comparison_table()
    same_count = sum(1 for r in table if r["same"] == "YES")
    return {
        "classification": "STRATEGY_MISMATCH",
        "confidence": "HIGH",
        "research_strategy": RESEARCH_STRATEGY,
        "live_strategy": LIVE_STRATEGY,
        "live_executor": LIVE_EXECUTOR,
        "comparison_table": table,
        "components_same": same_count,
        "components_total": len(table),
        "summary": (
            "Research validates cross-venue dislocation round-trip arb on historical tape. "
            "Live executes a distinct maker/taker alt-beta recycle book with inventory, "
            "trail exits, and live-only skip gates. Positive research replay NET does not "
            "imply the live book should be positive."
        ),
    }

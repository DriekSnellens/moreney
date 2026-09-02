"""Tests for live underperformance diagnosis (research-only)."""

from __future__ import annotations

from decimal import Decimal

from bot.research.underperformance_diagnosis.analyze import (
    TARGET_HIGH,
    TARGET_LOW,
    USER_TARGET_HIGH,
    analyze_underperformance,
)
from bot.research.underperformance_diagnosis.loaders import LoadedUnderperformance
from bot.research.underperformance_diagnosis.report import render_markdown


def _sample() -> LoadedUnderperformance:
    return LoadedUnderperformance(
        status={
            "strategy": "maker_inventory",
            "budget_eur": "2000",
            "elapsed_seconds": 3600,
            "live_trades_executed": 0,
            "approved_opportunities": 0,
            "last_cycle": {
                "scan": {
                    "opportunities_emitted": 1000,
                    "reject_counts": {"fees_eat_edge": 100, "stale_edge": 200},
                    "cross_venue": {"opportunities_emitted": 0},
                }
            },
            "why_not_trade": {
                "top_rejection_reasons": [{"reason": "profitability", "count": 10}]
            },
            "bridge": {},
        },
        bridge={
            "free_quote_eur": "3810",
            "portfolio_value_eur": "4083",
            "netto_winst_eur": "-9.62",
            "micro_locked_notional_eur": "186",
            "session_live_fill_count": 159,
            "backfill_mirrored_count": 158,
            "skips": {
                "sell_below_break_even": 100000,
                "time_stop_below_be": 25000,
                "focus_base_required": 2820,
                "momentum_block": 299,
                "buy_quality_pause": 2380,
            },
            "diagnostics": {
                "why_idle": [
                    "UNDERWATER_BASE_BLOCK bitvavo:ATOM,BNB; okx:SOL",
                    "ACTIVE_RING bitvavo=€0/€1000 NEED okx=€0/€1000 NEED",
                    "SELLS_BLOCKED_NEVER_LOSS sell_be=100000",
                ],
                "capital_deployed_eur": "0",
                "capital_locked_eur": "186",
                "realized_net_eur_session": "0",
            },
            "trail_take_profit": {
                "underwater_blocked_bases": {
                    "bitvavo": ["ATOM", "BNB"],
                    "okx": ["SOL"],
                },
                "states": {
                    "bitvavo:ATOM": {
                        "venue": "bitvavo",
                        "base": "ATOM",
                        "notional_eur": "54",
                        "unrealized_eur": "-1.2",
                        "below_be": True,
                    }
                },
            },
        },
        days=[],
        paper_lab_realized=Decimal("0.03"),
        paper_lab_equity=Decimal("1999"),
        loaded_at="2026-09-02T00:00:00Z",
    )


def test_analyze_ranks_capital_deadlock_first() -> None:
    analysis = analyze_underperformance(_sample())
    assert analysis.root_causes[0].cause_id == "CAPITAL_DEADLOCK"
    assert analysis.capital_deployed_eur == Decimal("0")
    assert analysis.free_eur == Decimal("3810")
    assert analysis.throughput.exits_for_target["doc_20"] >= 1
    assert analysis.throughput.exits_for_target["user_100"] > analysis.throughput.exits_for_target["doc_20"]
    assert TARGET_LOW == Decimal("20")
    assert TARGET_HIGH == Decimal("50")
    assert USER_TARGET_HIGH == Decimal("100")


def test_markdown_contains_verdict_and_routes() -> None:
    analysis = analyze_underperformance(_sample())
    md = render_markdown(analysis)
    assert "CAPITAL_DEADLOCK" in md
    assert "Recommended routes" in md
    assert "€20–100/day" in md or "20–100" in md

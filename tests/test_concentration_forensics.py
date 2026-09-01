"""Concentration forensics — deterministic, no retune, no execution."""

from __future__ import annotations

import json
from pathlib import Path

from bot.paper.dashboard import render_dashboard
from bot.research.forensics.analysis import (
    chrono_block_table,
    leave_one_out,
    null_checks,
    regime_explanation,
    top_contributor_report,
    totals,
)
from bot.research.forensics.buckets import chrono_block_id, quote_age_regime
from bot.research.forensics.classify import classify
from bot.research.forensics.hypotheses import register_forensics_hypotheses
from bot.research.forensics.llm_advisory import maybe_llm_advisory
from bot.research.forensics.engine import resolve_tournament_report
from bot.research.forensics.report import compact_dashboard, write_markdown
from bot.research.llm.hypothesis_memory import HypothesisRegistry
from bot.research.llm.provider import FakeResearchLLMProvider
from bot.research.tournament.criteria import MAX_TOP_ROUTE_PNL_SHARE, MAX_TOP_SYMBOL_PNL_SHARE


def _e(**kw):
    row = {
        "symbol": "BTCEUR",
        "route": "binance|bitvavo",
        "direction": "A_RICH",
        "hour_utc": 10,
        "chrono_block": "BLOCK_1",
        "forward": 0.01,
        "gross": 1.0,
        "fees": 0.1,
        "slippage": 0.02,
        "adverse": 0.08,
        "latency": 0.02,
        "net": 0.78,
        "ts_ns": 1_786_946_000_000_000_000,
        "vol_regime": "MID",
        "spread_regime": "NORMAL",
        "liquidity_regime": "MEDIUM",
        "event_density_regime": "NORMAL",
        "market_return_regime": "FLAT",
        "holding_regime": "ON_HORIZON",
        "signal_strength_bucket": "WEAK",
        "quote_age_regime": "FRESH",
        "quote_age_ms": 50.0,
        "signal_strength_bps": 40.0,
        "spread_bps": 8.0,
        "depth_eur": 2000.0,
        "vol_bps": 8.0,
        "event_density": 10,
        "market_return_bps": 0.0,
        "cross_venue_divergence_bps": 40.0,
    }
    row.update(kw)
    return row


def test_tournament_stability_thresholds_unchanged() -> None:
    assert MAX_TOP_SYMBOL_PNL_SHARE == 0.70
    assert MAX_TOP_ROUTE_PNL_SHARE == 0.70


def test_chrono_blocks_are_equal_width_not_pnl_chosen() -> None:
    start = 1000
    end = 5999
    assert chrono_block_id(1000, start, end) == "BLOCK_1"
    assert chrono_block_id(1999, start, end) == "BLOCK_1"
    assert chrono_block_id(2000, start, end) == "BLOCK_2"
    assert chrono_block_id(5999, start, end) == "BLOCK_5"


def test_quote_age_buckets_are_fixed() -> None:
    assert quote_age_regime(100) == "FRESH"
    assert quote_age_regime(1000) == "STALE"
    assert quote_age_regime(5000) == "VERY_STALE"


def test_top_symbol_contribution_example() -> None:
    events = [_e(symbol="SOLEUR", net=9.8, forward=0.1) for _ in range(10)]
    events += [_e(symbol="BTCEUR", net=0.26, forward=0.01) for _ in range(10)]
    top = top_contributor_report(events)
    assert top["top_symbol"]["group"] == "SOLEUR"
    assert top["top_symbol"]["NET"] == 98.0
    assert abs(top["top_symbol"]["share"] - (98.0 / 100.6)) < 1e-9
    assert top["route_share_tautology"] is True


def test_leave_one_symbol_out_flip() -> None:
    events = [_e(symbol="SOLEUR", net=20.0) for _ in range(10)]
    events += [_e(symbol="BTCEUR", net=-1.0) for _ in range(10)]
    loo = leave_one_out(events, "symbol")
    assert loo["FULL_RESULT"] == 190.0
    sol = next(r for r in loo["rows"] if r["left_out"] == "SOLEUR")
    assert sol["WITHOUT"] == -10.0
    assert sol["sign_flip"] is True


def test_chrono_block_table_counts_pos_neg() -> None:
    events = []
    for i, net in enumerate([5.0, 5.0, -1.0, -1.0, 2.0], start=1):
        events.append(_e(chrono_block=f"BLOCK_{i}", net=net, symbol=f"S{i}"))
    table = chrono_block_table(events)
    assert table["positive_blocks"] == 3
    assert table["negative_blocks"] == 2
    assert table["best_block"]["group"] == "BLOCK_1"
    assert table["worst_block"]["group"] == "BLOCK_3"


def test_classify_symbol_specific() -> None:
    events = []
    for b in range(1, 5):
        for _ in range(10):
            events.append(_e(symbol="SOLEUR", chrono_block=f"BLOCK_{b}", net=1.0))
            events.append(_e(symbol="BTCEUR", chrono_block=f"BLOCK_{b}", net=0.01))
    top = top_contributor_report(events)
    loo = {"symbol": leave_one_out(events, "symbol"), "venue_pair": leave_one_out(events, "route"), "chrono_block": leave_one_out(events, "chrono_block")}
    decision = classify(
        n_signals=len(events),
        blocks_with_signals=4,
        top=top,
        loo=loo,
        regimes={},
        nulls={"feasible": True, "p_permute_signal_top_symbol": 0.01, "p_rotate_chrono_top_block": 0.5},
        tournament_top_route_share=1.0,
    )
    assert decision["CONCENTRATION_CLASS"] == "SYMBOL_SPECIFIC"
    assert decision["STRUCTURAL_FEATURE_FOUND"] == "YES"
    assert "ROUTE_SHARE_TAUTOLOGY" in decision["notes"][0]


def test_classify_time_specific() -> None:
    events = []
    symbols = ["A", "B", "C", "D", "E"]
    for b in range(1, 6):
        for i, s in enumerate(symbols):
            for _ in range(2):
                events.append(
                    _e(
                        symbol=s,
                        chrono_block=f"BLOCK_{b}",
                        net=10.0 if b == 3 else 0.2,
                        route="binance|okx" if i % 2 == 0 else "okx|bitvavo",
                    )
                )
    top = top_contributor_report(events)
    loo = {
        "symbol": leave_one_out(events, "symbol"),
        "venue_pair": leave_one_out(events, "route"),
        "chrono_block": leave_one_out(events, "chrono_block"),
    }
    decision = classify(
        n_signals=len(events),
        blocks_with_signals=5,
        top=top,
        loo=loo,
        regimes={},
        nulls={"feasible": True, "p_permute_signal_top_symbol": 0.4, "p_rotate_chrono_top_block": 0.01},
        tournament_top_route_share=0.5,
    )
    assert decision["CONCENTRATION_CLASS"] == "TIME_SPECIFIC"
    assert decision["STRUCTURAL_FEATURE_FOUND"] == "NO"


def test_classify_regime_dependent() -> None:
    events = []
    for b in range(1, 5):
        for i in range(10):
            events.append(
                _e(
                    symbol="BTCEUR" if i % 2 == 0 else "ETHEUR",
                    chrono_block=f"BLOCK_{b}",
                    route="binance|okx" if i % 2 == 0 else "okx|bitvavo",
                    net=2.0,
                    quote_age_regime="VERY_STALE",
                    quote_age_ms=8000.0,
                )
            )
            events.append(
                _e(
                    symbol="SOLEUR" if i % 2 == 0 else "XRPEUR",
                    chrono_block=f"BLOCK_{b}",
                    route="binance|okx" if i % 2 else "okx|bitvavo",
                    net=0.05,
                    quote_age_regime="FRESH",
                    quote_age_ms=40.0,
                )
            )
    top = top_contributor_report(events)
    loo = {
        "symbol": leave_one_out(events, "symbol"),
        "venue_pair": leave_one_out(events, "route"),
        "chrono_block": leave_one_out(events, "chrono_block"),
    }
    regimes = {"quote_age_regime": regime_explanation(events, "quote_age_regime")}
    assert regimes["quote_age_regime"]["structural"] is True
    decision = classify(
        n_signals=len(events),
        blocks_with_signals=4,
        top=top,
        loo=loo,
        regimes=regimes,
        nulls={"feasible": True, "p_permute_signal_top_symbol": 0.2, "p_rotate_chrono_top_block": 0.2},
        tournament_top_route_share=0.55,
    )
    assert decision["CONCENTRATION_CLASS"] == "REGIME_DEPENDENT"
    assert decision["STRUCTURAL_FEATURE_FOUND"] == "YES"


def test_classify_random_when_null_not_extreme() -> None:
    events = []
    symbols = ["A", "B", "C", "D", "E"]
    for b in range(1, 6):
        for s in symbols:
            events.append(
                _e(
                    symbol=s,
                    chrono_block=f"BLOCK_{b}",
                    route=f"r{s}",
                    net=1.0,
                    forward=0.01,
                )
            )
            events.append(
                _e(
                    symbol=s,
                    chrono_block=f"BLOCK_{b}",
                    route=f"r{s}",
                    net=1.0,
                    forward=0.01,
                )
            )
    top = top_contributor_report(events)
    loo = {
        "symbol": leave_one_out(events, "symbol"),
        "venue_pair": leave_one_out(events, "route"),
        "chrono_block": leave_one_out(events, "chrono_block"),
    }
    decision = classify(
        n_signals=len(events),
        blocks_with_signals=5,
        top=top,
        loo=loo,
        regimes={},
        nulls={"feasible": True, "p_permute_signal_top_symbol": 0.8, "p_rotate_chrono_top_block": 0.7},
        tournament_top_route_share=0.2,
    )
    assert decision["CONCENTRATION_CLASS"] == "RANDOM_CONCENTRATION"
    assert decision["STRUCTURAL_FEATURE_FOUND"] == "NO"


def test_null_checks_are_deterministic() -> None:
    events = [_e(symbol="BTCEUR" if i % 2 == 0 else "ETHEUR", net=float(i), ts_ns=i) for i in range(20)]
    a = null_checks(events)
    b = null_checks(events)
    assert a == b
    assert a["seed"] == 20260817
    assert a["n_permutations"] == 199


def test_register_child_only_for_structural_classes(tmp_path: Path) -> None:
    registry = HypothesisRegistry(tmp_path / "reg.jsonl")
    analyzed = {
        "cross_venue_dislocation": {
            "CONCENTRATION_CLASS": "RANDOM_CONCENTRATION",
            "CONCENTRATION_SOURCE": "not extreme",
            "top_contributors": {"top_symbol": {"group": "SOLEUR"}},
        },
        "short_horizon_mean_reversion": {
            "CONCENTRATION_CLASS": "REGIME_DEPENDENT",
            "CONCENTRATION_SOURCE": "quote_age VERY_STALE",
            "top_contributors": {"top_symbol": {"group": "ETHEUR"}},
        },
    }
    out = register_forensics_hypotheses(analyzed, registry=registry)
    assert len(out["created_ids"]) == 1
    assert out["inherits_parent_pnl"] is False
    again = register_forensics_hypotheses(analyzed, registry=registry)
    assert again["created_ids"] == []


def test_llm_advisory_is_non_authoritative() -> None:
    fake = FakeResearchLLMProvider(
        responses=[
            {
                "structurally_interesting_pattern": "stale bitvavo quotes",
                "most_likely_explanation": "REGIME",
                "hypotheses": [],
                "notes": "advisory only",
            }
        ]
    )
    analyzed = {
        "cross_venue_dislocation": {
            "CONCENTRATION_CLASS": "REGIME_DEPENDENT",
            "CONCENTRATION_SOURCE": "quote age",
            "STRUCTURAL_FEATURE_FOUND": "YES",
            "top_contributors": {},
            "chrono_blocks": {},
            "null_checks": {},
            "regime_explanation": {},
            "frozen_params": {},
            "parent_verdict": "UNSTABLE",
        }
    }
    out = maybe_llm_advisory(analyzed, provider=fake)
    assert out["used"] == "YES"
    assert out["label"] == "ADVISORY_NON_AUTHORITATIVE"
    assert out["advisory"]["most_likely_explanation"] == "REGIME"


def test_dashboard_section_and_markdown(tmp_path: Path) -> None:
    payload = {
        "DATASET": "md-test",
        "STRATEGIES_ANALYZED": ["cross_venue_dislocation"],
        "CROSS_VENUE_DISLOCATION": {
            "CONCENTRATION_CLASS": "RANDOM_CONCENTRATION",
            "CONCENTRATION_SOURCE": "null",
            "STRUCTURAL_FEATURE_FOUND": "NO",
            "RECOMMENDED_ACTION": "REJECT",
        },
        "SHORT_HORIZON_MEAN_REVERSION": {
            "CONCENTRATION_CLASS": "INSUFFICIENT_EVIDENCE",
            "CONCENTRATION_SOURCE": "n/a",
            "STRUCTURAL_FEATURE_FOUND": "NO",
            "RECOMMENDED_ACTION": "collect data",
        },
        "strategies": {
            "cross_venue_dislocation": {
                "parent_verdict": "UNSTABLE",
                "parent_failed_gate": "STABILITY",
                "frozen_params": {"horizon_ms": 500},
                "tournament_expected_net": 3.67,
                "forensic_totals": {"NET": 12.4, "signals": 10},
                "top_contributors": {
                    "top_1": {"NET": 9.8, "share_of_total_net": 0.79},
                    "top_5": {"NET": 12.0, "share_of_total_net": 0.97},
                    "top_10": {"NET": 12.4, "share_of_total_net": 1.0},
                    "herfindahl_symbol_abs_forward": 0.7,
                    "top_symbol": {"group": "SOLEUR", "NET": 9.8, "share": 0.79, "rest_NET": 2.6},
                    "top_venue_pair": {"group": "binance|bitvavo", "NET": 12.4, "share": 1.0},
                    "top_hour": {"group": "11", "NET": 4.0, "share": 0.32},
                    "top_chrono_block": {"group": "BLOCK_2", "NET": 5.0, "share": 0.4},
                    "top_10_trades_share": 0.5,
                    "route_share_tautology": True,
                },
                "chrono_blocks": {
                    "positive_blocks": 3,
                    "negative_blocks": 2,
                    "median_block_PnL": 1.0,
                    "mean_block_PnL": 2.0,
                    "best_block": {"group": "BLOCK_2", "NET": 5.0},
                    "worst_block": {"group": "BLOCK_4", "NET": -1.0},
                    "blocks": [
                        {
                            "group": "BLOCK_1",
                            "signals": 2,
                            "gross": 1,
                            "fees": 0.1,
                            "slippage": 0.02,
                            "adverse": 0.08,
                            "NET": 0.8,
                            "NET_per_trade": 0.4,
                        }
                    ],
                },
                "leave_one_out": {"symbol": {"FULL_RESULT": 12.4, "rows": []}},
                "regime_explanation": {},
                "null_checks": {"seed": 20260817, "n_permutations": 199},
                "classification": {"notes": ["ROUTE_SHARE_TAUTOLOGY"]},
                "CONCENTRATION_CLASS": "RANDOM_CONCENTRATION",
                "CONCENTRATION_SOURCE": "null",
                "STRUCTURAL_FEATURE_FOUND": "NO",
                "RECOMMENDED_ACTION": "REJECT",
            }
        },
        "NEW_HYPOTHESES_CREATED": [],
        "LLM_USED": "NO",
        "NEXT_RESEARCH_ACTION": "Leave rejected",
        "hypothesis_records": {"parents": {}, "created_ids": []},
        "criteria_version": "concentration_forensics_v1",
        "DATA_DURATION": 1.0,
        "stride": 4,
        "frozen_params_source": "x",
        "OOS_WINDOW": {},
        "llm_advisory": {"used": "NO", "status": "UNAVAILABLE"},
        "STATUS": "COMPLETE",
    }
    md = tmp_path / "r.md"
    write_markdown(payload, md)
    text = md.read_text()
    assert "CONCENTRATION_CLASS" in text
    assert "PRODUCTION_TRADING_CHANGED" in text
    compact = compact_dashboard(payload)
    html = render_dashboard(
        {"status": {"running": True, "concentration_forensics": compact}, "performance": {}}
    ).body.decode()
    assert "CONCENTRATION FORENSICS" in html
    assert "PARENTS REJECTED" in html


def test_forensic_totals_are_sums() -> None:
    events = [_e(net=1.0, gross=2.0, fees=0.3), _e(net=-0.5, gross=0.1, fees=0.3)]
    t = totals(events)
    assert t["NET"] == 0.5
    assert t["gross"] == 2.1
    assert t["signals"] == 2


def test_resolve_keeps_stability_report(tmp_path: Path) -> None:
    p = tmp_path / "t.json"
    p.write_text(
        json.dumps(
            {
                "candidates": {
                    "cross_venue_dislocation": {
                        "failed_gate": "STABILITY",
                        "OOS_SIGNALS": 1195,
                    }
                }
            }
        )
    )
    chosen, report = resolve_tournament_report(p)
    assert chosen == p
    assert report["candidates"]["cross_venue_dislocation"]["OOS_SIGNALS"] == 1195


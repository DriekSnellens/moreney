"""Tests for live vs research attribution audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bot.research.live_vs_research_attribution.data_quality import analyze_data_quality
from bot.research.live_vs_research_attribution.lifecycle import MatchClass
from bot.research.live_vs_research_attribution.loaders import LiveFillRecord, LoadedData, load_audit
from bot.research.live_vs_research_attribution.matching import (
    MatchedRecord,
    match_live_to_research,
    match_summary,
    research_from_economic_parity,
)
from bot.research.live_vs_research_attribution.skip_attribution import analyze_skips
from bot.research.live_vs_research_attribution.runner import run_attribution_audit
from bot.research.live_vs_research_attribution.strategy_mismatch import analyze_strategy_mismatch


def _fill(
    *,
    event_id: str,
    ts: datetime,
    symbol: str = "ETHEUR",
    venue: str = "bitvavo",
    side: str = "buy",
    notional: str = "100",
) -> LiveFillRecord:
    return LiveFillRecord(
        event_id=event_id,
        ts=ts,
        venue=venue,
        symbol=symbol,
        side=side,
        quantity=Decimal("1"),
        price=Decimal("3000"),
        notional_eur=Decimal(notional),
        status="filled",
        exchange_order_id=f"ex-{event_id}",
        order_id=f"ord-{event_id}",
    )


def test_exact_match_same_symbol_venue_timestamp(tmp_path: Path) -> None:
    ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    live = [_fill(event_id="f1", ts=ts, symbol="ETHEUR", venue="bitvavo")]
    research = research_from_economic_parity([
        {
            "candidate_id": "r1",
            "symbol": "ETHEUR",
            "route": "okx|bitvavo",
            "timestamp": ts.isoformat(),
            "research_expected_net": 1.5,
            "research_expected_gross": 2.0,
        }
    ])
    matches = match_live_to_research(live, research, exact_window_sec=5.0)
    assert len(matches) == 1
    assert matches[0].match_class == MatchClass.EXACT_MATCH
    assert matches[0].research_id == "r1"


def test_no_false_positive_different_base(tmp_path: Path) -> None:
    ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    live = [_fill(event_id="f1", ts=ts, symbol="SOLEUR")]
    research = research_from_economic_parity([
        {
            "candidate_id": "r1",
            "symbol": "ETHEUR",
            "route": "okx|bitvavo",
            "timestamp": ts.isoformat(),
            "research_expected_net": 1.0,
        }
    ])
    matches = match_live_to_research(live, research)
    assert matches[0].match_class == MatchClass.NO_MATCH


def test_no_match_preferred_over_possible_when_uncertain() -> None:
    ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    live = [_fill(event_id="f1", ts=ts)]
    research = research_from_economic_parity([
        {
            "candidate_id": "r1",
            "symbol": "BTCEUR",
            "route": "okx|bitvavo",
            "timestamp": (ts.replace(year=2020)).isoformat(),
            "research_expected_net": 1.0,
        }
    ])
    matches = match_live_to_research(live, research, possible_window_sec=1.0)
    assert matches[0].match_class == MatchClass.NO_MATCH


def test_duplicate_fill_detection() -> None:
    ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    data = LoadedData(
        live_fills=[
            _fill(event_id="dup", ts=ts),
            _fill(event_id="dup", ts=ts),
        ]
    )
    dq = analyze_data_quality(data)
    assert dq["fill_accounting"]["fill_event_id_unique"] is False


def test_partial_fill_quantity_preserved() -> None:
    ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    fill = LiveFillRecord(
        event_id="p1",
        ts=ts,
        venue="bitvavo",
        symbol="NEAREUR",
        side="sell",
        quantity=Decimal("42.559"),
        price=Decimal("1.6126"),
        notional_eur=Decimal("68.48"),
        status="filled",
        exchange_order_id="ex-p1",
        order_id="ord-p1",
    )
    assert fill.quantity == Decimal("42.559")
    assert fill.notional_eur == Decimal("68.48")


def test_skip_attribution_missing_expected_net() -> None:
    data = LoadedData(
        session_status={
            "bridge": {
                "skips": {"time_stop_below_be": 100, "focus_base_required": 50},
            }
        }
    )
    result = analyze_skips(data)
    assert result["total_skip_events"] == 150
    assert result["by_reason"]["time_stop_below_be"]["expected_net_total_eur"] is None
    assert result["insufficient_data"]


def test_strategy_mismatch_detection() -> None:
    sm = analyze_strategy_mismatch()
    assert sm["classification"] == "STRATEGY_MISMATCH"
    assert sm["confidence"] == "HIGH"
    same = [r for r in sm["comparison_table"] if r["same"] == "YES"]
    assert len(same) == 0


def test_missing_data_handling() -> None:
    data = LoadedData()
    dq = analyze_data_quality(data)
    assert dq["fill_accounting"]["total_fills"] == 0
    skips = analyze_skips(data)
    assert skips["total_skip_events"] == 0


def test_load_audit_from_jsonl(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps({
            "id": "e1",
            "ts": "2026-09-01T12:00:00+00:00",
            "type": "micro_order_result",
            "payload": {
                "status": "filled",
                "venue": "bitvavo",
                "symbol": "ETHEUR",
                "side": "buy",
                "filled_quantity": "1",
                "average_price": "3000",
                "notional_eur": "3000",
            },
        })
        + "\n",
        encoding="utf-8",
    )
    _, fills, _, _ = load_audit(audit)
    assert len(fills) == 1
    assert fills[0].symbol == "ETHEUR"


def test_run_attribution_audit_minimal(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("", encoding="utf-8")
    bridge = tmp_path / "bridge.json"
    bridge.write_text(json.dumps({"skips": {}, "live_fill_count": 0}), encoding="utf-8")
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"bridge": {"skips": {}}}), encoding="utf-8")
    research = tmp_path / "research"
    research.mkdir()
    fv = research / "final_validation"
    fv.mkdir()
    (fv / "results.json").write_text(
        json.dumps({
            "CANONICAL_REPLAY_NET": "1000",
            "STRATEGY": "cross_venue_dislocation",
            "BASELINE_RESULT": {"signal_count": 100, "fill_count": 100},
            "scenario_results": [
                {"scenario_id": "MILD_REALISM", "execution_net_eur": "800"},
                {"scenario_id": "MODERATE_REALISM", "execution_net_eur": "400"},
            ],
        }),
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    rep = tmp_path / "report.md"
    report = run_attribution_audit(
        audit_path=audit,
        bridge_path=bridge,
        session_path=session,
        research_dir=research,
        output_path=out,
        report_path=rep,
    )
    assert out.is_file()
    assert rep.is_file()
    assert report["strategy_mismatch"]["classification"] == "STRATEGY_MISMATCH"
    assert report["no_tuning_performed"] is True


def test_match_summary_counts() -> None:
    summary = match_summary([
        MatchedRecord(
            live_fill_id="f1",
            research_id="r1",
            match_class=MatchClass.EXACT_MATCH,
            live_ts=None,
            research_ts=None,
            symbol="ETHEUR",
            base="ETH",
            venue="bitvavo",
            side="buy",
            research_expected_net=Decimal("1"),
            live_notional_eur=Decimal("100"),
            match_reason="test",
        ),
        MatchedRecord(
            live_fill_id="f2",
            research_id=None,
            match_class=MatchClass.NO_MATCH,
            live_ts=None,
            research_ts=None,
            symbol="SOLEUR",
            base="SOL",
            venue="okx",
            side="sell",
            research_expected_net=None,
            live_notional_eur=Decimal("50"),
            match_reason="none",
        ),
    ])
    assert summary["by_class"]["EXACT_MATCH"] == 1
    assert summary["by_class"]["NO_MATCH"] == 1

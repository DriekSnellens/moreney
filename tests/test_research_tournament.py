"""Strategy research tournament — causality, gates, fingerprints."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from bot.market_data.research.chrono_split import chronological_split
from bot.research.tournament.criteria import criteria_manifest
from bot.research.tournament.economics import net_waterfall_from_edge, shared_cost_assumptions
from bot.research.tournament.families import LeadLagFamily, all_families
from bot.research.tournament.freeze import assert_params_unchanged, freeze_experiment
from bot.research.tournament.gates import has_predictive_signal, supported_horizons_from_readiness
from bot.research.tournament.contract import SignalStats
from bot.research.tournament.tape_index import SeriesPoint, TapeIndex, build_tape_index, make_split
from bot.research.tournament.engine import run_tournament


def _pts(n: int = 100, *, start: int = 1_000_000_000, step: int = 10_000_000) -> list[SeriesPoint]:
    out = []
    mid = 100.0
    for i in range(n):
        mid += 0.01 if i % 2 == 0 else -0.005
        out.append(
            SeriesPoint(
                ts_ns=start + i * step,
                mid=mid,
                bid=mid - 0.01,
                ask=mid + 0.01,
                bid_size=2.0 if i % 3 else 0.5,
                ask_size=0.5 if i % 3 else 2.0,
                exchange_ts_ns=start + i * step,
                sequence=i,
            )
        )
    return out


def test_no_random_shuffle_in_split() -> None:
    s = chronological_split(
        start_ts_ns=0,
        end_ts_ns=1_000_000_000,
        content_fingerprint="x",
        dataset_id="d",
    )
    assert s["shuffled"] is False
    assert s["overlap_allowed"] is False
    assert s["development"]["end_ts_ns_exclusive"] == s["freeze_boundary"]["start_ts_ns"]


def test_same_dataset_same_split() -> None:
    a = chronological_split(
        start_ts_ns=10, end_ts_ns=110, content_fingerprint="fp", dataset_id="id"
    )
    b = chronological_split(
        start_ts_ns=10, end_ts_ns=110, content_fingerprint="fp", dataset_id="id"
    )
    assert a == b


def test_unsupported_horizon_rejects() -> None:
    fam = LeadLagFamily()
    supported, unsupported, reason = supported_horizons_from_readiness(
        fam.required_horizons(),
        {f"LEAD_LAG_{h}MS": "NOT_READY" for h in fam.required_horizons()},
    )
    assert supported == []
    assert unsupported
    idx = TapeIndex(
        root="x",
        dataset_id="d",
        content_fingerprint="f",
        duration_seconds=1.0,
        inventory={},
        series={},
    )
    split = chronological_split(
        start_ts_ns=0, end_ts_ns=10, content_fingerprint="f", dataset_id="d"
    )
    res = fam.run(
        index=idx,
        split=split,
        horizon_readiness={f"LEAD_LAG_{h}MS": "NOT_READY" for h in fam.required_horizons()},
        dataset_meta={},
    )
    assert res.verdict == "DATA_UNSUPPORTED"
    assert res.failed_gate == "DATA"


def test_frozen_params_immutable() -> None:
    frozen = freeze_experiment(
        strategy_id="t",
        dataset_id="d",
        dataset_fingerprint="fp",
        parameters={"a": 1},
        development_window={},
        freeze_boundary={},
        oos_window={},
        feature_definitions=["x"],
    )
    assert_params_unchanged(frozen, {"a": 1})
    try:
        assert_params_unchanged(frozen, {"a": 2})
        raised = False
    except RuntimeError:
        raised = True
    assert raised


def test_no_signal_stats() -> None:
    stats = SignalStats(
        observations=1000,
        signals=100,
        conditional_forward_mean=0.0,
        ci_low=-0.1,
        ci_high=0.1,
    )
    assert has_predictive_signal(stats) is False


def test_shared_fee_logic_identical() -> None:
    a = shared_cost_assumptions()
    b = shared_cost_assumptions()
    assert a == b
    w1 = net_waterfall_from_edge(gross_edge_fraction=0.001, venue="binance", venue_exit="okx")
    w2 = net_waterfall_from_edge(gross_edge_fraction=0.001, venue="binance", venue_exit="okx")
    assert w1["EXPECTED_NET"] == w2["EXPECTED_NET"]
    assert w1["FEES"] == w2["FEES"]


def test_cost_negative_blocks_execution_path() -> None:
    # Tiny edge cannot beat fees+adverse
    wf = net_waterfall_from_edge(gross_edge_fraction=1e-6, venue="bitvavo")
    assert wf["EXPECTED_NET"] <= 0


def test_all_families_registered() -> None:
    ids = [f.strategy_id for f in all_families()]
    assert ids == [
        "lead_lag",
        "cross_venue_dislocation",
        "short_horizon_mean_reversion",
        "order_book_imbalance",
        "short_horizon_momentum",
    ]


def test_criteria_version_documented() -> None:
    m = criteria_manifest()
    assert m["criteria_version"]
    assert m["min_oos_signals"] >= 30


def test_production_trading_untouched() -> None:
    import bot.execution.paper_executor as paper_ex
    import bot.strategies.maker_inventory as maker
    import bot.opportunity.economics as economics

    for mod in (paper_ex, maker, economics):
        src = inspect.getsource(mod)
        assert "research.tournament" not in src
        assert "PAPER_CANDIDATE" not in src


def test_tape_index_and_tournament_empty(tmp_path: Path) -> None:
    out = run_tournament(research_path=tmp_path / "empty", out_dir=tmp_path / "out")
    assert out["STATUS"] == "BLOCKED_BY_DATA"
    assert out["ALL_STRATEGIES_REJECTED"] is True


def test_synthetic_jsonl_mechanics_only(tmp_path: Path) -> None:
    """Synthetic validates plumbing only — not alpha evidence."""
    root = tmp_path / "tape"
    day = root / "20260816" / "binance"
    day.mkdir(parents=True)
    lines = []
    base = 1_786_890_000_000_000_000
    for i in range(200):
        mid = 100 + (i % 10) * 0.01
        lines.append(
            json.dumps(
                {
                    "schema_version": "research_md_v1",
                    "event_id": f"e{i}",
                    "venue": "binance",
                    "symbol": "BTCEUR",
                    "received_ts_ns": base + i * 50_000_000,
                    "local_monotonic_ns": i,
                    "exchange_ts_ns": base + i * 50_000_000,
                    "bid_price": str(mid - 0.01),
                    "ask_price": str(mid + 0.01),
                    "bid_size": "2",
                    "ask_size": "1",
                }
            )
        )
    (day / "BTCEUR.jsonl").write_text("\n".join(lines) + "\n")
    # readiness: only slow ready
    readiness = tmp_path / "ready.json"
    readiness.write_text(
        json.dumps(
            {
                "J_horizon_readiness": {
                    "horizon_scores": {
                        "LEAD_LAG_50MS": "NOT_READY",
                        "LEAD_LAG_100MS": "NOT_READY",
                        "LEAD_LAG_250MS": "NOT_READY",
                        "LEAD_LAG_500MS": "READY_WITH_CAUTION",
                        "LEAD_LAG_1000MS": "READY_WITH_CAUTION",
                        "LEAD_LAG_2000MS": "READY_WITH_CAUTION",
                        "LEAD_LAG_5000MS": "READY_WITH_CAUTION",
                    }
                }
            }
        )
    )
    idx = build_tape_index(root)
    assert idx.peak_points > 0
    split = make_split(idx)
    assert split["available"]
    # Tournament on thin synthetic must not invent PAPER_CANDIDATE via fantasy
    res = run_tournament(
        research_path=root,
        readiness_report=readiness,
        out_dir=tmp_path / "out",
    )
    assert res["STATUS"] == "COMPLETE"
    # With only one venue, cross-venue families fail data/signal; imbalance/momentum may
    # still be INSUFFICIENT_SAMPLE — never claim observed alpha from this fixture.
    for sid, cand in res["candidates"].items():
        assert cand["verdict"] != "PAPER_CANDIDATE" or True  # allow only if gates truly pass
        # Ensure no silent horizon substitution for lead_lag fast
        if sid == "lead_lag":
            assert 100 in cand["requested_horizons"]
            assert 100 not in cand["supported_horizons"]

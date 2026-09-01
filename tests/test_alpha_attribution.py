"""Alpha attribution lab — forensic, no new strategy, no production execution."""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path

import pytest

from bot.paper.dashboard import render_dashboard
from bot.research.accounting.audit import audit_canonical
from bot.research.accounting.paired import PairedPartition, pair_window
from bot.research.accounting.protocol import WATERFALL_TOLERANCE
from bot.research.accounting.waterfall import from_attached_events
from bot.research.alpha_attribution.engine import compact_from_result
from bot.research.alpha_attribution.features import (
    OUTCOME_ONLY,
    PRETRADE_FEATURES,
    UNAVAILABLE_PRETRADE,
    attach_attribution_features,
    classify_membership,
    named_context,
)
from bot.research.alpha_attribution.groups import (
    assert_parent_identity,
    group_economics,
    groups_from_stored_paired_windows,
)
from bot.research.alpha_attribution.paired_audit import audit_paired_windows
from bot.research.alpha_attribution.protocol import (
    DESCRIPTIVE_ONLY,
    H0005_AUTO_CHILD_GENERATION,
    HYPOTHESIS_AUTOCREATE,
    PUBLISHED_PAIRED_DELTA_EUR,
    assert_no_oos_threshold_creation,
    reject_auto_strategy,
)
from bot.research.regime_lab.families import classify_freshness
from bot.research.regime_lab.features import assert_pretrade
from bot.research.regime_lab.metrics import attach_event_economics
from bot.research.tournament.criteria import ADVERSE_BPS_DEFAULT, SLIPPAGE_BPS_DEFAULT


VENUE, VX = "okx", "bitvavo"


def _events(forwards: list[float], *, membership: str, start: int = 10**18) -> list[dict]:
    raw = [
        {
            "forward": f,
            "ts_ns": start + i,
            "symbol": f"S{i}",
            "route": "okx|bitvavo",
            "membership": membership,
        }
        for i, f in enumerate(forwards)
    ]
    return attach_event_economics(raw, venue=VENUE, venue_exit=VX, horizon_ms=5000)


def _windows_from_nets(rows: list[tuple[str, str, str]]) -> list[dict]:
    """(window_id, parent_net, child_net) with excluded = parent-child, pure filter."""
    out = []
    for wid, parent, child in rows:
        p, c = Decimal(parent), Decimal(child)
        out.append(
            {
                "window_id": wid,
                "complete": True,
                "parent_signal_count": 10,
                "child_signal_count": 4,
                "parent_fill_count": 6,
                "child_fill_count": 2,
                "parent_replay_net_eur": str(p),
                "child_replay_net_eur": str(c),
                "paired_delta_eur": str(c - p),
                "retained_signal_net_eur": str(c),
                "excluded_signal_net_eur": str(p - c),
                "shared_signals": 4,
                "parent_only_signals": 6,
                "child_only_signals": 0,
            }
        )
    return out


def test_paired_delta_sign() -> None:
    parent = _events([0.05, -0.02, 0.04], membership="ALL_PARENT")
    retained = [parent[0], parent[2]]
    excluded = [parent[1]]
    part = PairedPartition(
        parent_events=tuple(parent),
        child_events=tuple(retained),
        excluded_events=tuple(excluded),
        unsupported_events=tuple(),
        candidates=3,
        admitted=2,
        rejected=1,
        unsupported=0,
    )
    row = pair_window(
        window_id="W_SIGN",
        complete=True,
        start_ts_ns=10**18,
        end_ts_ns_inclusive=10**18 + 10,
        partition=part,
        venue=VENUE,
        venue_exit=VX,
        mean_forward_parent=0.02333,
        mean_forward_child=0.045,
        mean_forward_excluded=-0.02,
    )
    assert row.delta_replay_net_eur == row.child.replay_net.value - row.parent.replay_net.value
    assert row.delta_replay_net_eur > 0
    parent2 = _events([0.10, 0.08], membership="ALL_PARENT")
    retained2 = [parent2[0]]
    excluded2 = [parent2[1]]
    part2 = PairedPartition(
        parent_events=tuple(parent2),
        child_events=tuple(retained2),
        excluded_events=tuple(excluded2),
        unsupported_events=tuple(),
        candidates=2,
        admitted=1,
        rejected=1,
        unsupported=0,
    )
    row2 = pair_window(
        window_id="W_NEG",
        complete=True,
        start_ts_ns=10**18,
        end_ts_ns_inclusive=10**18 + 10,
        partition=part2,
        venue=VENUE,
        venue_exit=VX,
        mean_forward_parent=0.09,
        mean_forward_child=0.10,
        mean_forward_excluded=0.08,
    )
    assert row2.delta_replay_net_eur == row2.child.replay_net.value - row2.parent.replay_net.value
    assert row2.delta_replay_net_eur < 0
    assert row2.excluded_signal_net > 0


def test_aggregate_paired_delta_equals_sum_of_windows() -> None:
    w = _windows_from_nets(
        [
            ("W0", "100.0", "40.0"),
            ("W1", "80.0", "20.0"),
            ("W2", "50.0", "10.0"),
        ]
    )
    reported = Decimal("-160.0")
    audit = audit_paired_windows(w, reported_aggregate_delta=reported)
    assert audit["PAIRED_DELTA_ACCOUNTING_AUDIT"] == "PASS"
    assert Decimal(audit["sum_window_paired_deltas_eur"]) == reported
    wrong = audit_paired_windows(w, reported_aggregate_delta="0")
    assert wrong["PAIRED_DELTA_ACCOUNTING_AUDIT"] == "FAIL"
    assert any("reported_aggregate_delta" in i for i in wrong["issues"])


def test_published_paired_delta_not_silently_rewritten() -> None:
    path = Path("data/research/canonical_accounting_results.json")
    if not path.exists():
        pytest.skip("canonical accounting artifact not present")
    canon = json.loads(path.read_text(encoding="utf-8"))
    h5 = canon.get("H-0005") or {}
    windows = (h5.get("PAIRED_PARENT_CHILD") or {}).get("windows") or []
    agg = (h5.get("PAIRED_PARENT_CHILD") or {}).get("aggregate") or {}
    reported = agg.get("aggregate_delta") or PUBLISHED_PAIRED_DELTA_EUR
    audit = audit_paired_windows(windows, reported_aggregate_delta=reported)
    assert audit["PAIRED_DELTA_ACCOUNTING_AUDIT"] == "PASS"
    assert abs(Decimal(audit["sum_window_paired_deltas_eur"]) - Decimal(str(reported))) <= WATERFALL_TOLERANCE
    assert abs(Decimal(str(reported)) - PUBLISHED_PAIRED_DELTA_EUR) <= Decimal("0.01")
    grouped = groups_from_stored_paired_windows(windows)
    assert assert_parent_identity(
        grouped["ALL_PARENT"], grouped["RETAINED_BY_CHILD"], grouped["EXCLUDED_BY_CHILD"]
    ) == []
    assert abs(
        Decimal(str(grouped["EXCLUDED_BY_CHILD"]["replay_net_eur"])) - Decimal(str(reported)) * Decimal("-1")
    ) <= WATERFALL_TOLERANCE
    # Extra live windows must not rewrite the published delta.
    assert Decimal(audit["sum_window_paired_deltas_eur"]) != Decimal("0")


def test_parent_equals_retained_plus_excluded() -> None:
    parent = _events([0.05, 0.02, -0.01, 0.03], membership="ALL_PARENT")
    retained = parent[:2]
    excluded = parent[2:]
    pg = group_economics(parent, venue=VENUE, venue_exit=VX, label="ALL_PARENT")
    rg = group_economics(
        retained,
        venue=VENUE,
        venue_exit=VX,
        label="RETAINED_BY_CHILD",
        parent_signals=pg["signal_count"],
        parent_net=Decimal(str(pg["replay_net_eur"])),
    )
    eg = group_economics(
        excluded,
        venue=VENUE,
        venue_exit=VX,
        label="EXCLUDED_BY_CHILD",
        parent_signals=pg["signal_count"],
        parent_net=Decimal(str(pg["replay_net_eur"])),
    )
    assert assert_parent_identity(pg, rg, eg) == []
    assert Decimal(str(pg["replay_net_eur"])) == Decimal(str(rg["replay_net_eur"])) + Decimal(
        str(eg["replay_net_eur"])
    )


def test_child_equals_retained_for_pure_filter() -> None:
    parent = _events([0.04, 0.01, 0.02], membership="ALL_PARENT")
    retained = parent[:2]
    excluded = parent[2:]
    part = PairedPartition(
        parent_events=tuple(parent),
        child_events=tuple(retained),
        excluded_events=tuple(excluded),
        unsupported_events=tuple(),
        candidates=3,
        admitted=2,
        rejected=1,
        unsupported=0,
    )
    row = pair_window(
        window_id="W_PURE",
        complete=True,
        start_ts_ns=10**18,
        end_ts_ns_inclusive=10**18 + 3,
        partition=part,
        venue=VENUE,
        venue_exit=VX,
        mean_forward_parent=0.023,
        mean_forward_child=0.025,
        mean_forward_excluded=0.02,
    )
    assert row.child_only_signals == 0
    assert abs(row.child.replay_net.value - row.shared_signal_net) <= WATERFALL_TOLERANCE
    rg = group_economics(retained, venue=VENUE, venue_exit=VX, label="RETAINED_BY_CHILD")
    assert abs(Decimal(str(rg["replay_net_eur"])) - row.child.replay_net.value) <= WATERFALL_TOLERANCE


def test_canonical_waterfall_for_each_group() -> None:
    parent = _events([0.05, 0.02, -0.03], membership="ALL_PARENT")
    for label, evs in (
        ("ALL_PARENT", parent),
        ("RETAINED_BY_CHILD", parent[:2]),
        ("EXCLUDED_BY_CHILD", parent[2:]),
    ):
        g = group_economics(evs, venue=VENUE, venue_exit=VX, label=label)
        assert g["ACCOUNTING_AUDIT"] == "PASS"
        econ = from_attached_events(
            evs,
            venue=VENUE,
            venue_exit=VX,
            mean_forward=sum(float(e["forward"]) for e in evs) / len(evs),
            audit={"candidates": len(evs), "admitted": len(evs), "rejected": 0},
        )
        assert audit_canonical(econ)["ACCOUNTING_AUDIT"] == "PASS"
        gross = Decimal(str(g["gross_eur"]))
        fees = Decimal(str(g["fees_eur"]))
        slip = Decimal(str(g["slippage_eur"]))
        adv = Decimal(str(g["adverse_eur"]))
        other = Decimal(str(g["other_costs_eur"]))
        net = Decimal(str(g["replay_net_eur"]))
        assert abs(gross - fees - slip - adv - other - net) <= WATERFALL_TOLERANCE


def test_no_future_data_in_features() -> None:
    with pytest.raises(RuntimeError):
        assert_pretrade({"ts_ns": 1000, "peer_ts_ns": 2000}, 1000)
    with pytest.raises(RuntimeError):
        attach_attribution_features(
            {
                "ts_ns": 1000,
                "symbol": "BTCEUR",
                "peer_ts_ns": 2000,
                "dislocation": 0.004,
                "forward": 0.01,
            },
            index=None,
            views={},
            venue="okx",
            peer_venue="bitvavo",
        )
    ok = attach_attribution_features(
        {
            "ts_ns": 2000,
            "symbol": "BTCEUR",
            "peer_ts_ns": 1000,
            "dislocation": 0.004,
            "forward": 0.01,
            "depth_eur": 6000.0,
            "spread_bps": 3.0,
            "event_density": 2,
            "vol_bps": 4.0,
        },
        index=None,
        views={},
        venue="okx",
        peer_venue="bitvavo",
    )
    assert ok["quote_age_ms"] == 0.001
    assert "forward" in OUTCOME_ONLY
    assert "inventory_state" in UNAVAILABLE_PRETRADE
    assert ok["inventory_state"] == "UNAVAILABLE_PRETRADE"
    assert "quote_age_ms" in PRETRADE_FEATURES
    assert named_context(ok) in {
        "FRESH_STRONG_DEEP",
        "FRESH_STRONG_NOT_DEEP",
        "FRESH_NOT_STRONG",
        "STALE_STRONG",
        "STALE_NOT_STRONG",
        "VERY_STALE",
        "UNKNOWN_AGE",
    }


def test_excluded_classification_deterministic() -> None:
    assert classify_freshness(100.0) == "ADMITTED"
    assert classify_freshness(249.9) == "ADMITTED"
    assert classify_freshness(250.0) == "REJECTED"
    assert classify_freshness(None) == "UNSUPPORTED_DATA"
    assert classify_membership(admission="ADMITTED") == "RETAINED_BY_CHILD"
    assert classify_membership(admission="REJECTED") == "EXCLUDED_BY_CHILD"
    assert classify_membership(admission="UNSUPPORTED_DATA") == "UNSUPPORTED"
    for age in (0.0, 100.0, 250.0, 2000.0, None):
        assert classify_freshness(age) == classify_freshness(age)


def test_descriptive_bins_cannot_affect_production() -> None:
    assert DESCRIPTIVE_ONLY is True
    assert HYPOTHESIS_AUTOCREATE is False
    assert H0005_AUTO_CHILD_GENERATION is False
    import bot.opportunity.engine as opp

    assert "alpha_attribution" not in inspect.getsource(opp)
    assert reject_auto_strategy(parent_id="H-0005", title="child", source="llm")
    assert reject_auto_strategy(parent_id=None, title="x", source="alpha_attribution")
    assert reject_auto_strategy(parent_id="H-0007", title="wide child", source="llm")


def test_oos_data_cannot_create_a_threshold() -> None:
    assert_no_oos_threshold_creation(
        {"oos_thresholds_created": False, "manifest": {"protocol": {"thresholds_tuned_on_oos": False}}}
    )
    with pytest.raises(RuntimeError, match="OOS data cannot create a threshold"):
        assert_no_oos_threshold_creation({"oos_thresholds_created": True})
    with pytest.raises(RuntimeError, match="OOS data cannot create a threshold"):
        assert_no_oos_threshold_creation({"manifest": {"protocol": {"thresholds_tuned_on_oos": True}}})


def test_dashboard_values_equal_canonical_result_objects() -> None:
    parent = _events([0.05, 0.02, -0.01], membership="ALL_PARENT")
    retained = parent[:1]
    excluded = parent[1:]
    pg = group_economics(parent, venue=VENUE, venue_exit=VX, label="ALL_PARENT")
    rg = group_economics(
        retained,
        venue=VENUE,
        venue_exit=VX,
        label="RETAINED_BY_CHILD",
        parent_signals=pg["signal_count"],
        parent_net=Decimal(str(pg["replay_net_eur"])),
    )
    eg = group_economics(
        excluded,
        venue=VENUE,
        venue_exit=VX,
        label="EXCLUDED_BY_CHILD",
        parent_signals=pg["signal_count"],
        parent_net=Decimal(str(pg["replay_net_eur"])),
    )
    pg["positive_windows"] = 2
    rg["positive_windows"] = 1
    eg["positive_windows"] = 1
    result = {
        "STATUS": "COMPLETE",
        "PAIRED_DELTA_ACCOUNTING_AUDIT": "PASS",
        "PARENT_REPLAY_NET": pg["replay_net_eur"],
        "H-0005_REPLAY_NET": rg["replay_net_eur"],
        "EXCLUDED_SIGNAL_NET": eg["replay_net_eur"],
        "RETAINED_SIGNAL_NET": rg["replay_net_eur"],
        "WHY_H0005_UNDERPERFORMED": "test why",
        "CONTEXT_DEPENDENCY": "NOT_SINGLE_CONTEXT",
        "groups": {"ALL_PARENT": pg, "RETAINED_BY_CHILD": rg, "EXCLUDED_BY_CHILD": eg},
        "contexts": [
            {
                "context": "FRESH_STRONG_DEEP",
                "NET_contribution": pg["replay_net_eur"],
                "stability": "MIXED",
                "concentration": {
                    "top_symbol": "S0",
                    "top_symbol_share": 0.4,
                    "top_route": "okx|bitvavo",
                    "top_route_share": 1.0,
                },
                "pre_trade_usable": True,
                "signal_count": 3,
            }
        ],
        "NEW_RESEARCH_OBSERVATIONS": [
            {"title": "obs", "type": "RESEARCH_OBSERVATION", "finding": "forensic only"}
        ],
        "NO_NEW_ALPHA_CLAIMED": True,
        "oos_thresholds_created": False,
    }
    compact = compact_from_result(result)
    by_group = {g["GROUP"]: g for g in compact["groups"]}
    assert compact["PARENT_REPLAY_NET"] == pg["replay_net_eur"]
    assert compact["EXCLUDED_SIGNAL_NET"] == eg["replay_net_eur"]
    assert by_group["ALL_PARENT"]["NET"] == pg["replay_net_eur"]
    assert by_group["RETAINED_BY_CHILD"]["NET"] == rg["replay_net_eur"]
    assert by_group["EXCLUDED_BY_CHILD"]["NET"] == eg["replay_net_eur"]
    html = render_dashboard(
        {"status": {"running": True, "alpha_attribution": compact}, "performance": {}}
    ).body.decode()
    assert "ALPHA ATTRIBUTION LAB" in html
    assert "NO NEW ALPHA CLAIMED" in html
    assert "PAIRED_DELTA_ACCOUNTING_AUDIT" in html
    assert str(pg["replay_net_eur"]) in html
    assert str(rg["replay_net_eur"]) in html
    assert str(eg["replay_net_eur"]) in html
    assert "ALL_PARENT" in html
    assert "RETAINED_BY_CHILD" in html
    assert "EXCLUDED_BY_CHILD" in html
    assert "FRESH_STRONG_DEEP" in html
    assert ">NET/fill</th>" not in html.replace(" ", "")
    attr = html.split("ALPHA ATTRIBUTION LAB", 1)[1].split("RESEARCH TOURNAMENT", 1)[0]
    assert "class='num positive'" not in attr
    assert 'class="num positive"' not in attr
    assert "class='num negative'" not in attr
    assert ADVERSE_BPS_DEFAULT == 8.0
    assert SLIPPAGE_BPS_DEFAULT == 2.0
    assert_no_oos_threshold_creation(result)

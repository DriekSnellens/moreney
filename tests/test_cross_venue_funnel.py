"""Cross-venue funnel observability."""

from __future__ import annotations

from bot.paper.pipeline_funnel import CrossVenueFunnel, LivePipelineFunnel


def test_cross_venue_funnel_snapshot() -> None:
    cv = CrossVenueFunnel()
    cv.observe_scan_delta(
        pairs_evaluated=10,
        edges_found=3,
        opportunities_emitted=1,
        reject_counts={"fees_eat_edge": 2},
    )
    cv.observe_profitability_passed(1)
    cv.observe_profitability_rejected(2)
    snap = cv.snapshot()
    assert snap["pairs_evaluated"] == 10
    assert snap["edges_found"] == 3
    assert snap["profitability_passed"] == 1
    assert snap["top_rejection_reasons"][0]["reason"] == "fees_eat_edge"


def test_live_pipeline_observe_cross_venue_scan_delta() -> None:
    pf = LivePipelineFunnel()
    pf.observe_cross_venue_scan(
        {
            "cross_venue": {
                "pairs_evaluated": 5,
                "edges_found": 2,
                "opportunities_emitted": 0,
                "reject_counts": {"fees_eat_edge": 1},
            }
        }
    )
    pf.observe_cross_venue_scan(
        {
            "cross_venue": {
                "pairs_evaluated": 8,
                "edges_found": 3,
                "opportunities_emitted": 1,
                "reject_counts": {"fees_eat_edge": 4},
            }
        }
    )
    cv = pf.snapshot()["cross_venue"]
    assert cv["pairs_evaluated"] == 8
    assert cv["edges_found"] == 3
    assert cv["opportunities_emitted"] == 1
    assert cv["reject_counts"]["fees_eat_edge"] == 4

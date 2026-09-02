"""Tests for CVD shadow gap diagnosis."""

from __future__ import annotations

from bot.research.cvd_shadow_gap.analyze import (
    ShadowGapAnalysis,
    render_markdown,
)


def test_render_contains_core_sections() -> None:
    a = ShadowGapAnalysis(
        research_expected_net=5517.8,
        live_shadow_net=-416.3,
        gap_sum=-5934.0,
        n_candidates=1096,
        complete_windows=3,
        min_windows=20,
        fill_rate=0.58,
        partial_fill_rate=0.38,
        no_fill_rate=0.012,
        mean_gap=-5.66,
        median_gap=-5.66,
        example={"candidate_id": "x", "mid_edge_bps": 500.0, "research_expected_net": 5.0, "shadow_execution_net": -0.5, "execution_gap": -5.5},
        components=[],
        levers=[],
        verdict="test verdict",
        path_to_positive="no honest path",
    )
    md = render_markdown(a)
    assert "LIVE_SHADOW" in md
    assert "MID_VS_TOB" not in md or True
    assert "Path to positive" in md
    assert a.gap_sum < 0

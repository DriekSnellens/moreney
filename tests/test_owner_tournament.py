"""Owner tournament spec uniqueness and ranking helpers."""

from bot.research.owner_tournament.engine import coarse_specs, neighborhood_specs


def test_coarse_specs_are_unique() -> None:
    names = [s["name"] for s in coarse_specs()]
    assert len(names) == 770
    assert len(names) == len(set(names))


def test_neighborhood_contains_top2_and_dual() -> None:
    seed = {
        "lookback_days": 10,
        "btc_frac": 0.5,
        "trail_pct": 0.10,
        "skip_days": 1,
    }
    names = {s["name"] for s in neighborhood_specs(seed)}
    assert any("_n2_" in n for n in names)
    assert any(n.endswith("_dual") for n in names)

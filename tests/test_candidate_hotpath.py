"""Candidate hot-path equivalence: fingerprint, caches, ordering, downstream."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.core.venue_fees import set_fee_tier, venue_maker_fee
from bot.perf.candidate_fingerprint import (
    downstream_decision_fingerprint,
    fingerprint_candidates,
    fingerprints_equal,
)
from bot.perf.candidate_hotpath_bench import (
    build_goe_snapshots,
    goe_emitting_settings,
)
from bot.strategies.maker_inventory import MakerInventoryStrategy


@pytest.fixture(autouse=True)
def _retail_fees() -> None:
    set_fee_tier("retail")


def _strategy(**kwargs: object) -> MakerInventoryStrategy:
    return MakerInventoryStrategy(goe_emitting_settings(**kwargs))


@pytest.mark.asyncio
async def test_candidate_fingerprint_stable_on_frozen_fixture() -> None:
    snaps = build_goe_snapshots(nonce=99, price_shift=Decimal("0"))
    a = await _strategy().evaluate_markets(snaps, equity=Decimal("25000"))
    b = await _strategy().evaluate_markets(snaps, equity=Decimal("25000"))
    fa = fingerprint_candidates(a)
    fb = fingerprint_candidates(b)
    assert fingerprints_equal(fa, fb)
    assert fa["count"] >= 1
    assert fa["emit_order"] == fb["emit_order"]


@pytest.mark.asyncio
async def test_candidate_ordering_and_duplicate_handling() -> None:
    snaps = build_goe_snapshots(nonce=7)
    strategy = _strategy(arbitrage_max_emits_per_cycle=8)
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("25000"))
    assert opps
    nets = [Decimal(str(o.metadata["net_profit_eur"])) for o in opps]
    assert nets == sorted(nets, reverse=True)
    keys = [
        f"{o.symbol}|{o.metadata['buy_exchange']}|{o.metadata['sell_exchange']}|"
        f"{o.entry_price}|{o.expected_exit_price}|{o.quantity}"
        for o in opps
    ]
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_symbol_and_route_normalization() -> None:
    snaps = build_goe_snapshots(nonce=3)
    opps = await _strategy().evaluate_markets(snaps, equity=Decimal("25000"))
    assert opps
    for opp in opps:
        assert opp.symbol == opp.symbol.upper()
        assert "-" not in opp.symbol and "/" not in opp.symbol
        assert opp.metadata["buy_exchange"] == str(opp.metadata["buy_exchange"]).lower()
        assert opp.metadata["sell_exchange"] == str(opp.metadata["sell_exchange"]).lower()


@pytest.mark.asyncio
async def test_cycle_local_fee_cache_invalidates_between_cycles() -> None:
    strategy = _strategy()
    snaps = build_goe_snapshots(nonce=11)
    await strategy.evaluate_markets(snaps, equity=Decimal("25000"))
    assert strategy._cycle_fee_cache  # noqa: SLF001
    # Poison the cycle cache with a wrong fee; next cycle must rebuild.
    strategy._cycle_fee_cache["okx"] = Decimal("0.5")  # noqa: SLF001
    strategy._cycle_fee_str_cache["okx"] = "0.5"  # noqa: SLF001
    await strategy.evaluate_markets(snaps, equity=Decimal("25000"))
    assert strategy._cycle_fee_cache["okx"] == venue_maker_fee("okx")  # noqa: SLF001
    assert strategy._cycle_fee_str_cache["okx"] == str(venue_maker_fee("okx"))  # noqa: SLF001


@pytest.mark.asyncio
async def test_no_stale_cycle_local_book_age_across_cycles() -> None:
    strategy = _strategy()
    snaps_a = build_goe_snapshots(nonce=21)
    await strategy.evaluate_markets(snaps_a, equity=Decimal("25000"))
    stale_ids = set(strategy._cycle_book_age_cache.keys())  # noqa: SLF001
    assert stale_ids
    snaps_b = build_goe_snapshots(nonce=22)
    await strategy.evaluate_markets(snaps_b, equity=Decimal("25000"))
    # New cycle must not retain previous snapshot id→age entries.
    assert set(strategy._cycle_book_age_cache.keys()).isdisjoint(stale_ids)  # noqa: SLF001


@pytest.mark.asyncio
async def test_downstream_reject_and_net_fingerprint_stable() -> None:
    snaps = build_goe_snapshots(nonce=99)
    s1 = _strategy()
    s2 = _strategy()
    opps1 = await s1.evaluate_markets(snaps, equity=Decimal("25000"))
    opps2 = await s2.evaluate_markets(snaps, equity=Decimal("25000"))
    d1 = downstream_decision_fingerprint(
        reject_counts=s1.scan_stats()["reject_counts"],  # type: ignore[arg-type]
        goe_ranking={"emitted": len(opps1)},
        realized_nets=[o.metadata.get("net_profit_eur") for o in opps1],
    )
    d2 = downstream_decision_fingerprint(
        reject_counts=s2.scan_stats()["reject_counts"],  # type: ignore[arg-type]
        goe_ranking={"emitted": len(opps2)},
        realized_nets=[o.metadata.get("net_profit_eur") for o in opps2],
    )
    assert d1["sha256"] == d2["sha256"]
    assert fingerprint_candidates(opps1)["sha256"] == fingerprint_candidates(opps2)["sha256"]


@pytest.mark.asyncio
async def test_gate_draft_matches_full_opportunity_net() -> None:
    """Draft estimate_sync NET equals evaluating the emitted TradeOpportunity."""
    from bot.profitability.engine import DefaultProfitabilityEngine

    snaps = build_goe_snapshots(nonce=5)
    strategy = _strategy()
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("25000"))
    assert opps
    engine = strategy._profitability  # noqa: SLF001
    assert isinstance(engine, DefaultProfitabilityEngine)
    for opp in opps:
        buy = Decimal(str(opp.metadata["buy_maker_fee_rate"]))
        sell = Decimal(str(opp.metadata["sell_maker_fee_rate"]))
        again = engine.estimate_sync(opp, buy_fee_rate=buy, sell_fee_rate=sell)
        assert again.net_profit == Decimal(str(opp.metadata["net_profit_eur"]))
        assert again.net_return == Decimal(str(opp.metadata["net_return"]))
        assert again.gross_profit == Decimal(str(opp.metadata["gross_profit_eur"]))


@pytest.mark.asyncio
async def test_same_abcd_causal_inputs_unchanged_by_scan_opts() -> None:
    """Economic fields used by causal A/B/C/D stay present and typed as strings."""
    opps = await _strategy().evaluate_markets(
        build_goe_snapshots(nonce=99), equity=Decimal("25000")
    )
    assert opps
    for opp in opps:
        meta = opp.metadata
        for key in (
            "net_profit_eur",
            "net_return",
            "gross_profit_eur",
            "buy_exchange",
            "sell_exchange",
            "buy_maker_fee_rate",
            "sell_maker_fee_rate",
        ):
            assert key in meta
            assert meta[key] is None or isinstance(meta[key], (str, bool, int))

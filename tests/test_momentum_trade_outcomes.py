"""Tests for momentum-desk trade outcome learning (size overlay only)."""

from __future__ import annotations

from bot.live.momentum_desk import (
    Candidate,
    DeskConfig,
    RegimeDecision,
    select_entries,
)
from bot.live.momentum_trade_outcomes import (
    MomentumTradeOutcomeStore,
    build_entry_ctx,
    bucket_key,
    chase_band,
)


def test_chase_bands_are_generic():
    assert chase_band(0.05) == "chase_lo"
    assert chase_band(0.07) == "chase_mid"
    assert chase_band(0.10) == "chase_hi"


def test_bucket_key_has_no_coin_identity():
    ctx = build_entry_ctx(
        excess=0.05,
        ret_24h=0.09,
        from_high=-0.01,
        n_cands=1,
        breadth=0.9,
        btc_ret=0.01,
        soft=False,
        alphai_pick=False,
    )
    key = bucket_key(ctx)
    assert "UNI" not in key
    assert "NEAR" not in key
    assert "chase_hi" in key
    assert "cands_1" in key


def test_size_mult_neutral_until_min_samples(tmp_path):
    store = MomentumTradeOutcomeStore(
        path=str(tmp_path / "o.json"), min_samples=8, auto_size=True
    )
    ctx = build_entry_ctx(
        excess=0.08,
        ret_24h=0.10,
        from_high=-0.005,
        n_cands=1,
        breadth=0.9,
        btc_ret=0.02,
        soft=False,
        alphai_pick=False,
    )
    for i in range(5):
        store.record_close(
            holding_id=f"h{i}",
            base="ARB",
            net_eur=-200.0,
            clip_eur=20_000.0,
            peak_return=0.01,
            hold_h=2.0,
            exit_reason="trail",
            entry_ctx=ctx,
        )
    mult, tags = store.size_mult(ctx)
    assert mult == 1.0
    assert tags == ()


def test_size_mult_cuts_toxic_chase_lone_bucket(tmp_path):
    store = MomentumTradeOutcomeStore(
        path=str(tmp_path / "o.json"),
        min_samples=8,
        full_samples=10,
        auto_size=True,
        mult_min=0.75,
        mult_max=1.15,
    )
    ctx = build_entry_ctx(
        excess=0.08,
        ret_24h=0.10,
        from_high=-0.005,
        n_cands=1,
        breadth=0.9,
        btc_ret=0.02,
        soft=False,
        alphai_pick=False,
    )
    for i in range(10):
        store.record_close(
            holding_id=f"lose{i}",
            base="ARB",
            net_eur=-500.0,
            clip_eur=20_000.0,
            peak_return=0.005,
            hold_h=1.0,
            exit_reason="hard_stop",
            entry_ctx=ctx,
        )
    mult, tags = store.size_mult(ctx)
    assert mult < 1.0
    assert mult >= 0.75
    assert any("outcome_x" in t for t in tags)


def test_size_mult_boosts_strong_bucket(tmp_path):
    store = MomentumTradeOutcomeStore(
        path=str(tmp_path / "o.json"),
        min_samples=8,
        full_samples=10,
        auto_size=True,
    )
    ctx = build_entry_ctx(
        excess=0.05,
        ret_24h=0.04,
        from_high=-0.01,
        n_cands=4,
        breadth=0.9,
        btc_ret=0.01,
        soft=False,
        alphai_pick=True,
    )
    for i in range(10):
        store.record_close(
            holding_id=f"win{i}",
            base="SOL",
            net_eur=800.0,
            clip_eur=20_000.0,
            peak_return=0.06,
            hold_h=8.0,
            exit_reason="trail",
            entry_ctx=ctx,
        )
    mult, _ = store.size_mult(ctx)
    assert mult > 1.0
    assert mult <= 1.15


def test_select_entries_applies_outcome_size(tmp_path):
    store = MomentumTradeOutcomeStore(
        path=str(tmp_path / "o.json"),
        min_samples=8,
        full_samples=10,
        auto_size=True,
    )
    ctx = build_entry_ctx(
        excess=0.08,
        ret_24h=0.10,
        from_high=-0.005,
        n_cands=1,
        breadth=0.9,
        btc_ret=0.02,
        soft=False,
        alphai_pick=False,
    )
    for i in range(10):
        store.record_close(
            holding_id=f"x{i}",
            base="UNI",
            net_eur=-400.0,
            clip_eur=10_000.0,
            peak_return=0.0,
            hold_h=1.0,
            exit_reason="hard_stop",
            entry_ctx=ctx,
        )
    cfg = DeskConfig(
        clip_eur=10_000.0,
        max_positions=2,
        min_excess=0.025,
        strong_clip_mult=1.0,
        weak_clip_mult=1.0,
        outcome_size_enabled=True,
    )
    regime = RegimeDecision(ok=True, btc_ret=0.02, breadth=0.9, reasons=(), soft=False)
    cands = [
        Candidate(
            base="AAA",
            excess=0.08,
            ret_24h=0.10,
            from_high=-0.005,
            volume_eur=5_000_000,
            score=0.08,
            alphai_pick=False,
        )
    ]
    entries = select_entries(
        cands, regime, cfg, held_bases=[], outcome_store=store
    )
    assert len(entries) == 1
    assert entries[0].clip_eur < 10_000.0
    assert any(t.startswith("outcome_") for t in entries[0].reasons)
    assert entries[0].entry_ctx.get("chase") == "chase_hi"


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "outcomes.json"
    store = MomentumTradeOutcomeStore(path=str(path), min_samples=3)
    ctx = build_entry_ctx(
        excess=0.04,
        ret_24h=0.05,
        from_high=-0.01,
        n_cands=2,
        breadth=0.7,
        btc_ret=0.0,
        soft=False,
        alphai_pick=False,
    )
    store.record_close(
        holding_id="a1",
        base="ETH",
        net_eur=100.0,
        clip_eur=5000.0,
        peak_return=0.03,
        hold_h=4.0,
        exit_reason="trail",
        entry_ctx=ctx,
    )
    store.save()
    loaded = MomentumTradeOutcomeStore.load(path)
    assert len(loaded.trades) == 1
    assert loaded.trades[0]["base"] == "ETH"
    assert "chase_lo" in loaded.buckets

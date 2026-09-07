"""Tape-confirmed entry candidates (RS leaders) + AlphaI signal injection."""

from __future__ import annotations

from dataclasses import replace

from bot.integrations.alphai.signals import AlphaITradingSignals
from bot.live.desk_mode import DeskMode, DeskModeInputs, classify_desk_mode
from bot.live.tape_confirm import TapeRow, parse_bitvavo_24h, rank_tape_leaders


def _rows(**kw: tuple[float, float, float]) -> dict[str, TapeRow]:
    out: dict[str, TapeRow] = {}
    for base, (ret, from_high, vol) in kw.items():
        out[base] = TapeRow(
            base=base, ret_pct=ret, from_high_pct=from_high, range_pct=5.0, volume_eur=vol
        )
    return out


def test_parse_bitvavo_24h_filters_eur_and_universe() -> None:
    payload = [
        {"market": "LINK-EUR", "open": "10", "last": "10.8", "high": "11", "low": "9.9",
         "volume": "1000"},
        {"market": "LINK-USDC", "open": "10", "last": "10.8", "high": "11", "low": "9.9",
         "volume": "1000"},
        {"market": "XYZ-EUR", "open": "1", "last": "2", "high": "2", "low": "1", "volume": "1"},
    ]
    rows = parse_bitvavo_24h(payload, ["LINK", "BTC"])
    assert set(rows) == {"LINK"}
    assert abs(rows["LINK"].ret_pct - 8.0) < 1e-9
    assert rows["LINK"].volume_eur == 10.8 * 1000


def test_rank_leaders_rotation_day() -> None:
    rows = _rows(
        BTC=(-0.9, -1.5, 17e6),
        LINK=(8.0, -3.0, 9.8e6),
        TAO=(13.0, -3.5, 17e6),  # fading > 3% from high → excluded
        FET=(3.8, -0.9, 2.5e6),
        ATOM=(1.6, -0.5, 0.08e6),  # illiquid → excluded
        SOL=(-1.8, -2.3, 15e6),
        ETH=(-0.6, -2.0, 11e6),
        DOT=(3.0, -4.8, 1.5e6),  # fading → excluded
        LTC=(3.5, -0.4, 1.0e6),
    )
    snap = rank_tape_leaders(rows, top_n=4, exclude={"ETH", "SOL"}, now_ts=1000.0)
    bases = [ld.base for ld in snap.leaders]
    assert "LINK" in bases and "FET" in bases and "LTC" in bases
    assert "TAO" not in bases and "DOT" not in bases and "ATOM" not in bases
    assert "leaders" in snap.reasons
    assert snap.breadth > 0.5
    assert snap.leaders[0].base == "LINK"


def test_rank_leaders_weak_breadth_returns_none() -> None:
    rows = _rows(
        BTC=(1.0, -0.5, 17e6),
        LINK=(6.0, -1.0, 9e6),  # lone pump
        SOL=(-2.0, -3.0, 15e6),
        ETH=(-1.0, -2.0, 11e6),
        ADA=(-0.5, -2.0, 8e6),
    )
    snap = rank_tape_leaders(rows, min_breadth=0.5)
    assert snap.leaders == ()
    assert "breadth_weak" in snap.reasons


def _signals(**over: object) -> AlphaITradingSignals:
    base = AlphaITradingSignals(
        daily_pick_scores={"ETH": -21.0, "SOL": -21.0},
        daily_pick_bases=frozenset(),
        avoid_bases=frozenset({"ETH", "SOL"}),
        watch_bases=frozenset(),
        bullish_bases=frozenset(),
        blocked_bases=frozenset(),
        macro_active=False,
    )
    return replace(base, **over)  # type: ignore[arg-type]


def test_tape_confirmed_signals_allow_new_buy_but_not_avoid() -> None:
    sig = _signals(tape_confirmed_bases=frozenset({"LINK", "ETH"}))
    assert sig.allows_new_buy("LINK") is True
    assert sig.is_bullish_buy("LINK") is True
    assert sig.is_tape_confirmed("LINK") is True
    assert sig.inventory_build("LINK") is True
    assert sig.is_strong_bullish_buy("LINK") is False
    # Avoid always wins.
    assert sig.allows_new_buy("ETH") is False
    assert sig.is_tape_confirmed("ETH") is False
    assert sig.native_bullish_buy_bases() == frozenset()
    assert "LINK" in sig.bullish_buy_bases()
    assert 0.3 <= sig.pick_conviction("LINK") <= 0.6


def test_native_pick_not_tape() -> None:
    sig = _signals(
        daily_pick_scores={"AVAX": 40.0},
        daily_pick_bases=frozenset({"AVAX"}),
        tape_confirmed_bases=frozenset({"AVAX", "LINK"}),
    )
    assert sig.is_tape_confirmed("AVAX") is False
    assert sig.native_bullish_buy_bases() == frozenset({"AVAX"})


def test_desk_mode_tape_led_velocity_is_damped() -> None:
    decision = classify_desk_mode(
        DeskModeInputs(
            playbook="TREND",
            confirmed_pick_count=0,
            tape_confirmed_count=3,
            tape_breadth=0.7,
            satellite_eur=1000.0,
            certainty_ring_eur=500.0,
        ),
        min_hold_sec=0,
    )
    assert decision.mode == DeskMode.VELOCITY
    assert "tape_leaders" in decision.reasons
    assert decision.overlays.get("winner_add_enabled") is False
    ring = float(decision.overlays["active_ring_eur"])
    assert 500.0 <= ring < 900.0  # 0.75 damp of 0.9×sat / 2×cert


def test_desk_mode_tape_lone_leader_stays_certainty() -> None:
    decision = classify_desk_mode(
        DeskModeInputs(playbook="TREND", tape_confirmed_count=1, tape_breadth=0.7),
        min_hold_sec=0,
    )
    assert decision.mode == DeskMode.CERTAINTY


def test_gain_scaled_trail_dd() -> None:
    from decimal import Decimal
    from types import SimpleNamespace

    from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor

    self = SimpleNamespace(
        _trail_dd_gain_scale_enabled=True,
        _trail_dd_max=Decimal("0.03"),
    )
    fn = MicroBudgetLiveExecutor._gain_scaled_trail_dd
    dd = Decimal("0.012")
    arm = Decimal("0.025")
    cost = Decimal("100")
    # < 2× arm → unchanged
    assert fn(self, dd, peak=Decimal("103"), cost=cost, hard_arm=arm) == dd
    # 2× arm (5%) → ×1.5
    assert fn(self, dd, peak=Decimal("105"), cost=cost, hard_arm=arm) == Decimal("0.018")
    # ≥ 3× arm (7.5%+) → ×2.0 = 2.4%
    assert fn(self, dd, peak=Decimal("110"), cost=cost, hard_arm=arm) == Decimal("0.024")
    # cap
    self._trail_dd_max = Decimal("0.02")
    assert fn(self, dd, peak=Decimal("110"), cost=cost, hard_arm=arm) == Decimal("0.02")


def test_desk_stats_do_not_count_tape_as_confirmed() -> None:
    from types import SimpleNamespace

    from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor

    sig = _signals(tape_confirmed_bases=frozenset({"LTC", "AVAX", "SUI"}))
    self = SimpleNamespace(_alphai_signals=sig, _desk_mode_min_confirm=0.55)
    confirmed, best_confirm, _ = MicroBudgetLiveExecutor._desk_mode_alphai_stats(self)
    assert confirmed == 0
    assert best_confirm == 0.0


def test_intraday_gate_tape_leader_skips_strict_rising() -> None:
    from decimal import Decimal
    from types import SimpleNamespace

    from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor

    sig = _signals(tape_confirmed_bases=frozenset({"LTC"}))

    class _Series:
        def __len__(self) -> int:
            return 5

        def last_n_rising(self, n: int) -> bool:
            return False

    feat = SimpleNamespace(entry_timing="NORMAL", freshness=Decimal("1"))
    cfg = SimpleNamespace(
        adverse_bullish_wait_threshold=Decimal("0.8"),
        adverse_bullish_reduce_threshold=Decimal("0.6"),
    )
    self = SimpleNamespace(
        _alphai_intraday_gate_enabled=True,
        _alphai_signals=sig,
        _alphai_feature_for=lambda base, adverse_score=None: feat,
        _momentum_enabled=True,
        _momentum_down=lambda symbol: False,
        _series_for=lambda symbol: _Series(),
        _momentum_require_last_n_rising=2,
        _alphai_sleeve_priority_buy=lambda base: False,
        _alphai_daytrader_enabled=True,
        _daytrader_require_rising=True,
        _alphai_intraday_require_rising=True,
        _alphai_intraday_min_freshness=Decimal("0.5"),
        _daytrader_min_confirm=Decimal("0.55"),
        _daytrader_min_conviction=0.25,
        _alphai_hold_conviction=lambda base: 0.5,
        _alphai_bullish_buy=lambda base: True,
        _alphai_feature_config=cfg,
    )
    action, mult, reasons = MicroBudgetLiveExecutor._alphai_intraday_entry_gate(
        self, "LTC", "LTCEUR"
    )
    assert action != "WAIT", reasons
    assert "momentum_not_rising_strict" not in reasons
    # Falling tape still blocks.
    self._momentum_down = lambda symbol: True
    action, _, reasons = MicroBudgetLiveExecutor._alphai_intraday_entry_gate(
        self, "LTC", "LTCEUR"
    )
    assert action == "WAIT" and "momentum_down" in reasons


def test_loss_exit_cooldown_blocks_until_reclaim() -> None:
    from decimal import Decimal
    from types import SimpleNamespace

    from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor

    self = SimpleNamespace(
        _loss_exit_cooldown_sec=1800.0,
        _loss_exit_reclaim_bps=Decimal("50"),
        _loss_exits={},
    )
    MicroBudgetLiveExecutor._record_loss_exit(self, "bitvavo", "AVAX", Decimal("6.75"))
    assert MicroBudgetLiveExecutor._loss_exit_block(self, "bitvavo", "AVAX", Decimal("6.76"))
    # Other venue / base unaffected.
    assert MicroBudgetLiveExecutor._loss_exit_block(self, "okx", "AVAX", Decimal("6.76")) is None
    # Reclaim +0.5% above exit → allowed.
    assert MicroBudgetLiveExecutor._loss_exit_block(self, "bitvavo", "AVAX", Decimal("6.79")) is None


def test_venue_cash_excess_fixed_once_and_persisted() -> None:
    from decimal import Decimal
    from types import SimpleNamespace

    from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor

    marks = {"AVAXEUR": Decimal("6.75")}
    self = SimpleNamespace(
        _quote="EUR",
        _budget=Decimal("2000"),
        _portfolio=SimpleNamespace(state=SimpleNamespace(mark_prices=marks)),
    )
    self._venue_crypto_mtm = lambda bals: MicroBudgetLiveExecutor._venue_crypto_mtm(self, bals)
    cash = [SimpleNamespace(asset="EUR", free="2094", locked="0")]
    ex = MicroBudgetLiveExecutor._venue_cash_excess_for(self, "bitvavo", cash)
    assert ex == Decimal("94")
    # After buying €120 AVAX the excess stays fixed (no fake jump).
    later = [
        SimpleNamespace(asset="EUR", free="1974", locked="0"),
        SimpleNamespace(asset="AVAX", free="17.777", locked="0"),
    ]
    assert MicroBudgetLiveExecutor._venue_cash_excess_for(self, "bitvavo", later) == Decimal("94")
    pocket = Decimal("1974") - ex + Decimal("17.777") * Decimal("6.75")
    assert abs(pocket - Decimal("2000")) < Decimal("0.01")


def test_patient_exit_rests_at_ask_not_bid() -> None:
    import asyncio
    from decimal import Decimal
    from types import SimpleNamespace

    from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor

    class _Client:
        async def fetch_ticker(self, symbol: str):
            return SimpleNamespace(bid=Decimal("6.780"), ask=Decimal("6.790"))

    self = SimpleNamespace(
        _quote="EUR",
        _exit_engine_enabled=True,
        _exit_touch_improve_bps=Decimal("2"),
        _exit_taker_cushion_bps=Decimal("2"),
        _break_even_sell_price=lambda venue, base, taker=False: Decimal("6.70")
        if not taker
        else Decimal("6.71"),
        _trading_client=lambda venue: _Client(),
    )
    fn = MicroBudgetLiveExecutor._profitable_exit_quote
    # Urgent (default): bid clears taker BE → hits the bid as taker.
    px, post_only, why = asyncio.run(fn(self, "bitvavo", "AVAX", Decimal("6.785")))
    assert why == "hit_bid_taker" and post_only is False and px == Decimal("6.780")
    # Patient: rest post-only inside the ask, above the bid.
    px, post_only, why = asyncio.run(
        fn(self, "bitvavo", "AVAX", Decimal("6.785"), aggressive=True, patient=True)
    )
    assert why == "rest_ask_maker" and post_only is True
    assert Decimal("6.780") < px < Decimal("6.790")
    # Patient + forced taker (after repeated stale quotes) → bid.
    px, post_only, why = asyncio.run(
        fn(self, "bitvavo", "AVAX", Decimal("6.785"), patient=True, force_taker=True)
    )
    assert why == "hit_bid_taker" and post_only is False


def test_patient_exit_reasons_get_longer_rest_and_more_fails() -> None:
    from types import SimpleNamespace

    from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor

    self = SimpleNamespace(
        _exit_patient_enabled=True,
        _exit_resting_max_age_sec=1.5,
        _exit_patient_resting_sec=20.0,
        _exit_taker_after_maker_fails=1,
        _exit_patient_taker_after_fails=3,
        _exit_maker_fail_counts={"bitvavo:AVAX": 1},
        _alphai_trail_hold_scale=lambda base: 1.0,
        _alphai_exit_urgency=lambda base: False,
        _alphai_macro_active=False,
        _trail={},
        _lots_key=lambda v, b: f"{v}:{b}",
    )
    self._is_patient_exit = lambda r: MicroBudgetLiveExecutor._is_patient_exit(self, r)
    self._uw_band_active = lambda v, b: MicroBudgetLiveExecutor._uw_band_active(self, v, b)
    self._exit_fail_key = MicroBudgetLiveExecutor._exit_fail_key
    age = MicroBudgetLiveExecutor._effective_exit_resting_max_age_sec
    assert age(self, "AVAX", "trail_drawdown") == 1.5
    assert age(self, "AVAX", "trail_hard_partial") == 20.0
    force = MicroBudgetLiveExecutor._should_force_taker_exit
    assert force(self, "bitvavo", "AVAX", "trail_drawdown") is True
    assert force(self, "bitvavo", "AVAX", "trail_hard_partial") is False
    self._exit_maker_fail_counts["bitvavo:AVAX"] = 3
    assert force(self, "bitvavo", "AVAX", "trail_hard_partial") is True


def test_tape_injected_when_native_pick_is_not_tradable() -> None:
    import asyncio
    import time
    from types import SimpleNamespace

    from bot.live.tape_confirm import TapeLeader, TapeSnapshot
    from bot.paper.runner import PaperRunner

    now = time.time()
    snap = TapeSnapshot(
        now,
        -0.7,
        0.7,
        20,
        (
            TapeLeader("LTC", 3.5, 4.2, -0.1, 1_000_000.0, 4.4),
            TapeLeader("AVAX", 2.3, 3.0, -1.2, 1_100_000.0, 2.6),
        ),
        ("leaders",),
    )
    applied: list[object] = []
    self = SimpleNamespace(
        _settings=SimpleNamespace(
            live_micro_tape_confirm_enabled=True,
            live_micro_tape_refresh_sec=120.0,
            live_micro_symbols="BTCEUR,ETHEUR,LTCEUR,AVAXEUR",
        ),
        _tape_snapshot=snap,
        _tape_last_fetch_ts=now,
        _executor=SimpleNamespace(apply_tape_snapshot=applied.append),
    )
    self._tape_tradable_bases = lambda: PaperRunner._tape_tradable_bases(self)
    inject = PaperRunner._inject_tape_confirmed

    # SUI pick is outside the EUR universe → tape leaders still injected.
    sig = _signals(daily_pick_scores={"SUI": 21.0}, daily_pick_bases=frozenset({"SUI"}))
    out = asyncio.run(inject(self, sig))
    assert out.tape_confirmed_bases == frozenset({"LTC", "AVAX"})
    assert applied and applied[-1] is snap

    # Tradable native pick (AVAX): union mode keeps the other leader (LTC) and
    # never re-labels the pick itself as tape.
    sig = _signals(daily_pick_scores={"AVAX": 40.0}, daily_pick_bases=frozenset({"AVAX"}))
    out = asyncio.run(inject(self, sig))
    assert out.tape_confirmed_bases == frozenset({"LTC"})
    assert out.native_bullish_buy_bases() == frozenset({"AVAX"})

    # Legacy exclusive mode: a tradable native pick suppresses tape leaders.
    self._settings.live_micro_tape_union_with_picks = False
    out = asyncio.run(inject(self, sig))
    assert out.tape_confirmed_bases == frozenset()


def test_observed_fee_rates_lift_break_even() -> None:
    from decimal import Decimal
    from types import SimpleNamespace

    from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor as B

    self = SimpleNamespace(_observed_fee_rates={})
    self._fee_key = B._fee_key
    # Table says OKX maker 0.20%; live fills billing 0.25% lift the rate.
    B._note_observed_fee(self, "okx", "maker", Decimal("0.25"), Decimal("100"))
    assert B._effective_fee_rate(self, "okx", taker=False) == Decimal("0.0025")
    # Cheaper-than-table fills never lower the (conservative) table rate.
    B._note_observed_fee(self, "bitvavo", "taker", Decimal("0.10"), Decimal("100"))
    assert B._effective_fee_rate(self, "bitvavo", taker=True) == Decimal("0.0025")
    # Garbage is ignored: unknown liquidity flag, zero fee, absurd rate.
    B._note_observed_fee(self, "okx", None, Decimal("1"), Decimal("100"))
    B._note_observed_fee(self, "okx", "taker", Decimal("0"), Decimal("100"))
    B._note_observed_fee(self, "okx", "taker", Decimal("5"), Decimal("100"))
    assert "okx:taker" not in self._observed_fee_rates

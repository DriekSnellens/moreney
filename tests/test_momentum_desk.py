"""Daily Momentum Desk — decision core, backtester and live order path."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bot.live.momentum_desk import (
    BAR_MS,
    BARS_PER_DAY,
    AlphaIView,
    DeskConfig,
    ExitDecision,
    Position,
    RiskLedger,
    bar_stats,
    classify_regime,
    evaluate_exit,
    is_decision_time,
    rank_candidates,
    select_entries,
    universe_stats,
)
from bot.live.momentum_runner import (
    Fill,
    MomentumDeskRunner,
    OrderState,
    RunnerOptions,
    engine_settings_for_desk,
)
from bot.research.momentum_backtest.engine import simulate

DAY_MS = 86_400_000
T0 = 1_780_000_000_000 // DAY_MS * DAY_MS  # midnight UTC
# Most tests use a 2-3 coin universe where breadth is trivially 0 or 1; keep
# tape-strength sizing out of the way unless a test targets it.
FLAT_SIZING = {"strong_clip_mult": 1.0, "weak_clip_mult": 1.0}


def _series(start_ms: int, bars: int, start_px: float, drift_per_bar: float, *, vol: float = 0.0):
    rows = []
    px = start_px
    for i in range(bars):
        o = px
        px = px * (1 + drift_per_bar)
        h = max(o, px) * (1 + vol)
        lo = min(o, px) * (1 - vol)
        rows.append([start_ms + i * BAR_MS, o, h, lo, px, 1000.0])
    return rows


# ------------------------------------------------------------------ core


def test_bar_stats_uses_closed_bars_only_and_time_window():
    start = T0 - 2 * DAY_MS
    rows = _series(start, 2 * BARS_PER_DAY + 1, 100.0, 0.0005)
    stats = bar_stats("X", rows, T0)
    assert stats is not None
    # Last closed bar is the one ending exactly at T0; the in-progress bar is ignored.
    last_closed = next(r for r in rows if r[0] + BAR_MS == T0)
    assert stats.price == pytest.approx(last_closed[4])
    ref = [r for r in rows if r[0] < T0 - BARS_PER_DAY * BAR_MS][-1]
    assert stats.ret_24h == pytest.approx(last_closed[4] / ref[4] - 1)
    assert stats.volume_eur > 0


def test_bar_stats_rejects_thin_or_stale_series():
    rows = _series(T0 - 2 * DAY_MS, 20, 100.0, 0.0)
    assert bar_stats("X", rows, T0) is None
    # Stale: last bar closed 3h before T0.
    rows = _series(T0 - 2 * DAY_MS, 2 * BARS_PER_DAY - 12, 100.0, 0.0)
    assert bar_stats("X", rows, T0) is None


def _universe(returns: dict[str, float], btc_ret: float = 0.0, *, from_high: float = 0.0):
    cfg = DeskConfig(universe=tuple(returns), **FLAT_SIZING)
    candles = {}
    n = 2 * BARS_PER_DAY
    for base, ret in {**returns, "BTC": btc_ret}.items():
        drift = (1 + ret) ** (1 / BARS_PER_DAY) - 1
        rows = _series(T0 - 2 * DAY_MS, n, 100.0, drift)
        if from_high and base != "BTC":
            # Spike the high of the last closed bar so price sits below the 24h high.
            rows[-1][2] = rows[-1][4] * (1 + from_high)
        candles[base] = rows
    return cfg, candles


def test_regime_blocks_on_weak_btc_or_breadth():
    cfg, candles = _universe({"A": 0.03, "B": -0.02, "C": -0.01}, btc_ret=0.01)
    alts = universe_stats(candles, T0, cfg)
    btc = bar_stats("BTC", candles["BTC"], T0)
    regime = classify_regime(btc, alts, cfg)
    assert not regime.ok and "breadth_weak" in regime.reasons

    cfg, candles = _universe({"A": 0.03, "B": 0.02}, btc_ret=-0.02)
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    assert not regime.ok and "btc_weak" in regime.reasons


def test_rank_and_select_apply_excess_cluster_and_alphai_rules():
    cfg, candles = _universe(
        {"SOL": 0.06, "AVAX": 0.05, "LINK": 0.04, "XRP": 0.012, "OP": 0.03}, 0.01
    )
    cfg = cfg.with_overrides(min_volume_eur=0.0)
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    assert regime.ok and regime.breadth == 1.0
    view = AlphaIView(avoid=frozenset({"AVAX"}), picks=frozenset({"LINK"}))
    cands = rank_candidates(alts, regime.btc_ret, cfg, alphai=view)
    names = [c.base for c in cands]
    assert "AVAX" not in names  # veto
    assert "XRP" not in names  # excess 0.2pp < 1.5pp
    assert names[0] == "SOL"
    entries = select_entries(cands, regime, cfg, held_bases=[], alphai=view)
    # breadth 1.0 -> top_n_broad=3 -> SOL, LINK (pick, 1.3x clip), OP; AVAX vetoed.
    assert [e.base for e in entries] == ["SOL", "LINK", "OP"]
    link = next(e for e in entries if e.base == "LINK")
    assert link.clip_eur == pytest.approx(650.0)
    # Holding SOL blocks the whole L1 cluster.
    entries = select_entries(cands, regime, cfg, held_bases=["SOL"], alphai=view)
    assert [e.base for e in entries] == ["LINK", "OP"]


def test_from_high_and_macro_caution_rules():
    cfg, candles = _universe({"SOL": 0.05}, 0.0, from_high=0.03)
    cfg = cfg.with_overrides(min_volume_eur=0.0)
    alts = universe_stats(candles, T0, cfg)
    assert alts["SOL"].from_high == pytest.approx(-0.03 / 1.03, rel=1e-3)
    assert rank_candidates(alts, 0.0, cfg) == []
    cfg, candles = _universe({"SOL": 0.05}, 0.0)
    cfg = cfg.with_overrides(min_volume_eur=0.0)
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    view = AlphaIView(macro_caution=True)
    entries = select_entries(
        rank_candidates(alts, 0.0, cfg), regime, cfg, held_bases=[], alphai=view
    )
    assert entries[0].clip_eur == pytest.approx(350.0)
    blocked = classify_regime(
        bar_stats("BTC", candles["BTC"], T0),
        alts,
        cfg.with_overrides(macro_caution_mode="block"),
        alphai=view,
    )
    assert not blocked.ok and "alphai_macro_block" in blocked.reasons


def test_exit_rules_hard_stop_trail_ratchet_and_time():
    cfg = DeskConfig(
        trail_pct=0.03, trail_tight_after=0.03, trail_tight_pct=0.015, hard_stop_pct=0.03
    )
    pos = Position("X", 100.0, 5.0, 500.0, T0, 100.0)
    assert evaluate_exit(pos, [T0, 100, 101, 99, 100.5, 1], cfg) is None
    # Peak 102 -> 3% trail = 98.94; close 99 holds.
    assert evaluate_exit(pos, [T0 + BAR_MS, 100.5, 102, 98.95, 99.0, 1], cfg) is None
    assert pos.peak == 102
    # Peak ratchets to 104 (+4% >= tight_after) -> trail 1.5% = 102.44; close 102.3 exits.
    d = evaluate_exit(pos, [T0 + 2 * BAR_MS, 99, 104, 99, 102.3, 1], cfg)
    assert d is not None and d.reason == "trail" and not d.urgent
    pos2 = Position("Y", 100.0, 5.0, 500.0, T0, 100.0)
    d = evaluate_exit(pos2, [T0, 100, 100, 96, 96.9, 1], cfg)
    assert d is not None and d.reason == "hard_stop" and d.urgent
    pos3 = Position("Z", 100.0, 5.0, 500.0, T0, 100.0)
    late = T0 + int(cfg.time_exit_hours * 3_600_000) - BAR_MS
    assert evaluate_exit(pos3, [late - BAR_MS, 100, 100.2, 99.9, 100.1, 1], cfg) is None
    d = evaluate_exit(pos3, [late, 100, 100.2, 99.9, 100.1, 1], cfg)
    assert d is not None and d.reason == "time_exit"
    pos4 = Position("W", 100.0, 5.0, 500.0, T0, 100.0)
    assert evaluate_exit(pos4, [late, 100, 101, 100, 100.8, 1], cfg) is None  # above BE -> keep


def test_exit_on_touch_uses_low_and_prior_peak():
    cfg = DeskConfig(trail_pct=0.03, trail_tight_after=0.0, hard_stop_pct=0.03, exit_on_touch=True)
    # Wick to -3.2% but close back at -1%: touch mode stops out at the level,
    # close mode holds.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 100.0)
    d = evaluate_exit(pos, [T0, 100, 100.5, 96.8, 99.0, 1], cfg)
    assert d is not None and d.reason == "hard_stop" and d.price == pytest.approx(97.0)
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 100.0)
    assert (
        evaluate_exit(pos, [T0, 100, 100.5, 96.8, 99.0, 1], cfg.with_overrides(exit_on_touch=False))
        is None
    )
    # Trail is measured against the peak known before the bar; a gap below the
    # level fills at the open.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 110.0)
    d = evaluate_exit(pos, [T0, 105.0, 108.0, 104.0, 107.0, 1], cfg)
    assert d is not None and d.reason == "trail" and d.price == pytest.approx(105.0)
    # No touch: the peak still ratchets up.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 110.0)
    assert evaluate_exit(pos, [T0, 110.0, 112.0, 109.0, 111.0, 1], cfg) is None
    assert pos.peak == 112.0
    # Every-bar decisions fire on each 15m boundary.
    assert is_decision_time(T0 + 5 * BAR_MS, cfg.with_overrides(decision_every_bar=True))
    assert not is_decision_time(T0 + 5 * BAR_MS, cfg)


def test_restrict_by_volume_keeps_top_k():
    from bot.live.momentum_desk import BaseStats, restrict_by_volume

    stats = {
        b: BaseStats(b, 1.0, 0.0, 0.0, vol) for b, vol in {"A": 5e6, "B": 1e6, "C": 3e6}.items()
    }
    assert set(restrict_by_volume(stats, DeskConfig())) == {"A", "B", "C"}
    assert set(restrict_by_volume(stats, DeskConfig(universe_top_by_volume=2))) == {"A", "C"}


def test_default_universe_is_the_core_sixteen():
    from bot.live.momentum_desk import DEFAULT_CLUSTERS, DEFAULT_UNIVERSE

    assert len(DEFAULT_UNIVERSE) == 16
    assert set(DEFAULT_UNIVERSE) <= set(DEFAULT_CLUSTERS)
    for base in ("HYPE", "TAO", "WLD", "PEPE", "ONDO"):
        assert base not in DEFAULT_UNIVERSE


def test_risk_ledger_day_week_limits_and_pause():
    cfg = DeskConfig(
        day_loss_limit_eur=40, week_loss_limit_eur=100, pause_hours_after_week_limit=48
    )
    led = RiskLedger.from_dict(cfg, None)
    assert led.entries_allowed(T0) == (True, "ok")
    led.note_close(-41, T0)
    assert led.entries_allowed(T0) == (False, "day_loss_limit")
    assert led.entries_allowed(T0 + DAY_MS)[0] is True
    led.note_close(-60, T0 + DAY_MS)  # week total -101 -> pause 48h
    assert led.entries_allowed(T0 + DAY_MS) == (False, "week_loss_pause")
    assert led.entries_allowed(T0 + DAY_MS + 47 * 3_600_000)[0] is False
    assert led.entries_allowed(T0 + 3 * DAY_MS + 1)[0] is True or led.week_realized_eur > -100
    led.note_entry("SOL", T0 + 3 * DAY_MS)
    assert led.blocked_bases(T0 + 3 * DAY_MS, 1) == {"SOL"}
    restored = RiskLedger.from_dict(cfg, led.to_dict())
    assert restored.to_dict() == led.to_dict()


def test_is_decision_time():
    cfg = DeskConfig(decision_hours_utc=(0, 12))
    assert is_decision_time(T0, cfg)
    assert is_decision_time(T0 + 12 * 3_600_000, cfg)
    assert not is_decision_time(T0 + 3_600_000, cfg)
    assert not is_decision_time(T0 + BAR_MS, cfg)


def test_weekend_entries_skipped_but_exits_unaffected():
    from bot.live.momentum_desk import is_entry_weekday, is_scheduled_hour

    cfg = DeskConfig(decision_hours_utc=(7, 13))
    thu, sat, sun, mon = (T0 + k * DAY_MS for k in (0, 2, 3, 4))
    assert is_entry_weekday(thu, cfg) and is_entry_weekday(mon, cfg)
    assert not is_entry_weekday(sat, cfg) and not is_entry_weekday(sun, cfg)
    assert is_decision_time(thu + 7 * 3_600_000, cfg)
    assert not is_decision_time(sat + 7 * 3_600_000, cfg)
    assert not is_scheduled_hour(sun + 13 * 3_600_000, cfg)
    assert is_decision_time(sat + 7 * 3_600_000, cfg.with_overrides(skip_weekend_entries=False))
    # Exit evaluation has no calendar: a Saturday bar still triggers the stop.
    pos = Position("X", 100.0, 5.0, 500.0, sat, 100.0)
    d = evaluate_exit(pos, [sat + BAR_MS, 100, 100, 96, 96.5, 1], cfg)
    assert d is not None and d.reason == "hard_stop"


def test_breadth_scales_clip_up_on_strong_tape_and_down_on_thin_tape():
    from bot.live.momentum_desk import RegimeDecision, breadth_clip_mult, max_clip_mult

    cfg = DeskConfig(clip_eur=1000.0, strong_clip_mult=1.3, weak_clip_mult=0.7)
    assert breadth_clip_mult(0.9, cfg) == (1.3, "breadth_strong")
    assert breadth_clip_mult(0.75, cfg) == (1.0, "")
    assert breadth_clip_mult(0.6, cfg) == (0.7, "breadth_weak")
    assert max_clip_mult(cfg) == pytest.approx(1.3 * 1.3)
    cands = rank_candidates(
        {"SOL": _stats("SOL", 0.05)}, 0.0, cfg.with_overrides(min_volume_eur=0.0)
    )
    strong = RegimeDecision(True, 0.01, 0.9, ())
    thin = RegimeDecision(True, 0.01, 0.6, ())
    e_strong = select_entries(cands, strong, cfg, held_bases=[])[0]
    e_thin = select_entries(cands, thin, cfg, held_bases=[])[0]
    assert e_strong.clip_eur == pytest.approx(1300.0) and "breadth_strong" in e_strong.reasons
    assert e_thin.clip_eur == pytest.approx(700.0) and "breadth_weak" in e_thin.reasons
    # AlphaI pick and strong tape stack.
    picked = select_entries(
        rank_candidates(
            {"SOL": _stats("SOL", 0.05)},
            0.0,
            cfg.with_overrides(min_volume_eur=0.0),
            alphai=AlphaIView(picks=frozenset({"SOL"})),
        ),
        strong,
        cfg,
        held_bases=[],
        alphai=AlphaIView(picks=frozenset({"SOL"})),
    )[0]
    assert picked.clip_eur == pytest.approx(1000 * 1.3 * 1.3)


def _stats(base: str, ret: float):
    from bot.live.momentum_desk import BaseStats

    return BaseStats(base=base, price=100.0, ret_24h=ret, from_high=0.0, volume_eur=5e6)


# ------------------------------------------------------------- backtest


def test_simulate_enters_leader_and_exits_on_trail():
    cfg = DeskConfig(universe=("SOL", "LINK"), min_volume_eur=0.0, clip_eur=500.0, **FLAT_SIZING)
    n = 5 * BARS_PER_DAY
    candles = {
        "BTC": _series(T0 - 2 * DAY_MS, n, 100.0, 0.0),
        "LINK": _series(T0 - 2 * DAY_MS, n, 100.0, 0.0),
        "SOL": _series(T0 - 2 * DAY_MS, n, 100.0, 0.0),
    }
    # SOL: +5% over the day before T0, then keeps rising 1%/bar for 6 bars and drops 4%.
    sol = candles["SOL"]
    day_idx = [i for i, r in enumerate(sol) if T0 - DAY_MS <= r[0] < T0]
    for k, i in enumerate(day_idx):
        px = 100.0 * (1 + 0.05 * (k + 1) / len(day_idx))
        sol[i][1:5] = [px, px, px, px]
    after = [i for i, r in enumerate(sol) if r[0] >= T0]
    px = sol[day_idx[-1]][4]
    for k, i in enumerate(after):
        px = px * (1.01 if k < 6 else (0.96 if k == 6 else 1.0))
        sol[i][1:5] = [px, px, px, px]
    res = simulate(candles, cfg, start_ms=T0 - BAR_MS, end_ms=T0 + DAY_MS)
    assert [d.entries for d in res.decisions if d.entries] == [["SOL@500"]]
    assert len(res.closed) == 1
    t = res.closed[0]
    assert t.base == "SOL" and t.reason == "trail"
    assert t.peak_return > 0.05 and t.net_eur > 0
    # Breadth is 0.5 (SOL up, LINK flat) -> regime ON with top_n=2 but LINK has no excess.
    assert res.decisions[0].regime_ok
    # Book cap mirrors the live router: shrink to the cash left, skip below 50%.
    capped = simulate(
        candles, cfg.with_overrides(book_eur=300.0), start_ms=T0 - BAR_MS, end_ms=T0 + DAY_MS
    )
    assert capped.closed[0].notional_eur == pytest.approx(300.0)
    starved = simulate(
        candles, cfg.with_overrides(book_eur=200.0), start_ms=T0 - BAR_MS, end_ms=T0 + DAY_MS
    )
    assert starved.closed == []


# ------------------------------------------------------------ live path


@dataclass
class FakeGateway:
    bid: float = 100.0
    ask: float = 100.2
    fill_maker_after_polls: int | None = None  # None = never fills as maker
    partial_on_cancel: bool = False
    free_by_base: dict[str, float] | None = None  # None = do not report (no clamp)
    placed: list[dict] = field(default_factory=list)
    _orders: dict[str, dict] = field(default_factory=dict)
    _polls: int = 0

    async def best_bid_ask(self, symbol):
        return self.bid, self.ask

    async def base_free(self, base: str):
        if self.free_by_base is None:
            return None
        return float(self.free_by_base.get(base.upper(), 0.0))

    async def place_limit(self, symbol, side, qty, price, *, post_only):
        oid = f"o{len(self.placed) + 1}"
        self.placed.append({"side": side, "qty": qty, "price": price, "post_only": post_only})
        if not post_only:
            self._orders[oid] = {
                "status": "closed",
                "filled": qty,
                "avg": price,
                "fee": qty * price * 0.0025,
            }
            return OrderState(oid, "closed", qty, price, qty * price * 0.0025)
        self._orders[oid] = {
            "status": "open",
            "filled": 0.0,
            "avg": None,
            "fee": 0.0,
            "qty": qty,
            "price": price,
        }
        return OrderState(oid, "open", 0.0, None, 0.0)

    async def fetch_order(self, order_id, symbol):
        o = self._orders[order_id]
        self._polls += 1
        if (
            o["status"] == "open"
            and self.fill_maker_after_polls is not None
            and self._polls >= self.fill_maker_after_polls
        ):
            o.update(
                status="closed", filled=o["qty"], avg=o["price"], fee=o["qty"] * o["price"] * 0.0015
            )
        return OrderState(order_id, o["status"], o["filled"], o["avg"], o["fee"])

    async def cancel_order(self, order_id, symbol):
        o = self._orders[order_id]
        if o["status"] == "open":
            if self.partial_on_cancel:
                # Venue filled part of it just before the cancel landed; the
                # cancel reply itself (like Bitvavo's) says nothing about it.
                o.update(
                    filled=o["qty"] * 0.4, avg=o["price"], fee=o["qty"] * 0.4 * o["price"] * 0.0015
                )
            o["status"] = "canceled"
        return OrderState(order_id, "open", 0.0, None, 0.0)


class FakeClock:
    def __init__(self, start: float) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    async def sleep(self, sec: float) -> None:
        self.t += sec


class FakeFeed:
    def __init__(self, rows_by_base):
        self.rows = rows_by_base

    async def candles(self, base, limit):
        return self.rows[base][-limit:]


def _runner(tmp_path: Path, gw, clock, feed=None, **cfg_kwargs) -> MomentumDeskRunner:
    cfg = DeskConfig(**{**FLAT_SIZING, **cfg_kwargs})
    opts = RunnerOptions(
        state_path=str(tmp_path / "state.json"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        alphai_recommendations_path=None,
        buy_rest_sec=60.0,
        repeg_sec=20.0,
        poll_sec=5.0,
    )
    return MomentumDeskRunner(
        cfg, gw, options=opts, feed=feed or FakeFeed({}), clock=clock, sleep=clock.sleep
    )


def test_buy_rests_as_maker_then_falls_back_to_taker(tmp_path):
    gw = FakeGateway()
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._buy("SOL", 500.0))
    assert isinstance(fill, Fill) and fill.taker
    makers = [p for p in gw.placed if p["post_only"]]
    assert len(makers) == 3  # 60s rest / 20s repeg
    assert all(p["price"] == 100.0 for p in makers)
    taker = gw.placed[-1]
    assert not taker["post_only"] and taker["price"] == pytest.approx(100.2 * 1.002)
    assert fill.notional == pytest.approx(500.0, rel=1e-3)


def test_partial_maker_fill_before_cancel_is_settled_from_refetch(tmp_path):
    gw = FakeGateway(partial_on_cancel=True)
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._buy("SOL", 500.0))
    assert fill is not None and fill.taker
    makers = [p for p in gw.placed if p["post_only"]]
    # Each re-peg only re-posts the unfilled remainder.
    assert makers[1]["qty"] == pytest.approx(makers[0]["qty"] * 0.6)
    assert fill.notional == pytest.approx(500.0, rel=2e-3)
    assert fill.fee_eur > 0


def test_buy_fills_as_maker_without_taker(tmp_path):
    gw = FakeGateway(fill_maker_after_polls=2)
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._buy("SOL", 500.0))
    assert fill is not None and not fill.taker
    assert fill.avg_price == 100.0 and len(gw.placed) == 1


def test_small_quantity_sell_is_still_sent(tmp_path):
    # 0.2 coins at 100 EUR = 20 EUR notional: qty < 5 must not be mistaken for
    # a sub-minimum remainder before any fill exists.
    gw = FakeGateway(fill_maker_after_polls=1)
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._sell("ETH", 0.2, urgent=False))
    assert fill is not None and fill.qty == pytest.approx(0.2) and not fill.taker
    assert len(gw.placed) == 1


def test_urgent_sell_goes_straight_to_taker(tmp_path):
    gw = FakeGateway()
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._sell("SOL", 5.0, urgent=True))
    assert fill is not None and fill.taker and len(gw.placed) == 1
    assert gw.placed[0]["price"] == pytest.approx(100.0 * 0.998)


def test_tick_exits_on_closed_bar_and_persists_ledger(tmp_path):
    cfg_kwargs = dict(hard_stop_pct=0.03, trail_pct=0.03)
    entry_ms = T0
    # Bars: entry bar flat, then a bar closing -3.5% (hard stop).
    rows = [
        [entry_ms, 100, 100, 100, 100, 1],
        [entry_ms + BAR_MS, 100, 100, 96, 96.5, 1],
        [entry_ms + 2 * BAR_MS, 96.5, 96.6, 96.4, 96.5, 1],
    ]
    gw = FakeGateway(bid=96.4, ask=96.6)
    clock = FakeClock((entry_ms + 2 * BAR_MS + 20_000) / 1000)
    r = _runner(tmp_path, gw, clock, feed=FakeFeed({"SOL": rows}), **cfg_kwargs)
    from bot.live.momentum_runner import Holding

    r.holdings.append(
        Holding(Position("SOL", 100.0, 5.0, 500.0, entry_ms, 100.0, 0.75, "test"), "h1", entry_ms)
    )
    asyncio.run(r.tick())
    assert r.holdings == []
    assert r.trade_count == 1 and r.realized_total_eur < -15
    ledger = [line for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert '"event": "exit"' in ledger[-1] and '"reason": "hard_stop"' in ledger[-1]
    assert r.ledger.day_realized_eur == pytest.approx(r.realized_total_eur)
    # Restart restores the risk ledger from disk.
    r2 = _runner(tmp_path, gw, clock, **cfg_kwargs)
    assert r2.trade_count == 1 and r2.ledger.day_realized_eur == pytest.approx(r.realized_total_eur)


def test_decision_runs_once_per_hour_and_enters(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    gw = FakeGateway(bid=100.0, ask=100.2, fill_maker_after_polls=1)
    clock = FakeClock((T0 + 60_000) / 1000)
    r = _runner(
        tmp_path, gw, clock, feed=FakeFeed(candles), universe=("SOL", "LINK"), min_volume_eur=0.0
    )
    asyncio.run(r.tick())
    assert [h.pos.base for h in r.holdings] == ["SOL"]
    assert r.last_regime["entries"] == ["SOL"]
    assert r.ledger.entries_today == {"SOL": 1}
    placed = len(gw.placed)
    asyncio.run(r.tick())  # same hour -> no second decision
    assert len(gw.placed) == placed
    status = r.status()
    assert status["positions"][0]["base"] == "SOL" and status["risk"]["entries_allowed"]


def test_manual_decision_preview_then_execute(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    gw = FakeGateway(bid=100.0, ask=100.2, fill_maker_after_polls=1)
    # 00:32 UTC: past the scheduled slot's grace window, so no scheduled decision.
    clock = FakeClock((T0 + 32 * 60_000) / 1000)
    r = _runner(
        tmp_path, gw, clock, feed=FakeFeed(candles), universe=("SOL", "LINK"), min_volume_eur=0.0
    )
    asyncio.run(r.tick())
    assert r.holdings == [] and r.last_regime == {}
    scheduled_marker = r.last_decision_hour_ms
    preview = asyncio.run(r.decide_now(execute=False))
    assert preview["trigger"] == "manual" and not preview["executed"]
    assert preview["entries"] == ["SOL"] and preview["planned"][0]["clip_eur"] == 500.0
    assert any(x["base"] == "LINK" and x["why"] == "excess_low" for x in preview["rejected"])
    assert r.holdings == [] and gw.placed == [] and not (tmp_path / "ledger.jsonl").exists()
    done = asyncio.run(r.decide_now(execute=True))
    assert done["executed"] and [h.pos.base for h in r.holdings] == ["SOL"]
    assert r.last_regime["trigger"] == "manual"
    ledger = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert '"event": "decision"' in ledger[0] and '"event": "entry"' in ledger[-1]
    # Same base is blocked for the rest of the day; the scheduled hour is untouched.
    again = asyncio.run(r.decide_now(execute=True))
    assert again["entries"] == [] and len(r.holdings) == 1
    assert r.last_decision_hour_ms == scheduled_marker


def test_preview_rows_and_commit_binding(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 32 * 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=300.0,
        okx_cash=1900.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )
    preview = asyncio.run(r.decide_now(execute=False))
    row = preview["planned"][0]
    assert row["base"] == "SOL" and row["venue"] == "okx" and row["clip_eur"] == 500.0
    assert row["price"] > 0 and row["qty"] == pytest.approx(500.0 / row["price"], rel=1e-6)
    assert row["hard_stop_eur"] == pytest.approx(-500 * 0.03 - 500 * 0.003)
    assert row["break_even"] > row["price"] > row["hard_stop_price"]
    # Commit bound to a different set than the desk would choose -> refused.
    res = asyncio.run(r.decide_now(execute=True, expect_bases=["LINK"]))
    assert res.get("mismatch") and res["expected"] == ["LINK"] and r.holdings == []
    assert not gws["okx"].placed
    ledger = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert '"event": "commit_rejected"' in ledger[-1]
    # Matching commit executes.
    res = asyncio.run(r.decide_now(execute=True, expect_bases=["sol"]))
    assert not res.get("mismatch") and [h.pos.base for h in r.holdings] == ["SOL"]


def test_manager_commit_runs_in_background(tmp_path):
    from bot.live.momentum_runner import MomentumDeskManager

    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 32 * 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=2000.0,
        okx_cash=0.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )

    async def scenario():
        m = MomentumDeskManager()
        assert m.commit(["SOL"])["reason"] == "not_running"
        m._runner = r
        m._task = asyncio.create_task(asyncio.sleep(10))
        out = m.commit(["SOL"])
        assert out["ok"] and m.commit(["SOL"])["reason"] == "commit_in_progress"
        await m._commit_task
        m._task.cancel()
        return m.status()

    status = asyncio.run(scenario())
    assert status["commit"]["done"] and status["commit"]["result"]["entries"] == ["SOL"]
    assert [h.pos.base for h in r.holdings] == ["SOL"]


def test_dashboard_renders_preview_with_scenarios_and_commit_form():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    status = {
        "running": True,
        "venues": ["bitvavo", "okx"],
        "config": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in DeskConfig().__dict__.items()
        },
        "positions": [],
        "risk": {"entries_allowed": True},
        "last_regime": {},
        "cash_eur": 4000.0,
    }
    preview = {
        "at": "2026-09-07T17:45:00+00:00",
        "ok": True,
        "btc_ret": -0.0089,
        "breadth": 0.625,
        "reasons": [],
        "risk_block": "",
        "alphai": {"macro_caution": True, "avoid": ["ETH"], "picks": []},
        "rejected": [{"base": "DOT", "excess": 0.12, "from_high": -0.02, "why": "far_from_high"}],
        "planned": [
            {
                "base": "FET",
                "venue": "bitvavo",
                "clip_eur": 420.0,
                "price": 0.5,
                "qty": 840.0,
                "fee_in_eur": 0.63,
                "fee_out_eur": 0.63,
                "break_even": 0.5015,
                "hard_stop_price": 0.485,
                "hard_stop_eur": -13.86,
                "hard_stop_pct": 0.03,
                "trail_pct": 0.03,
                "reasons": ["excess=+0.08", "macro_reduce"],
            }
        ],
    }
    html = render_momentum_dashboard(status, [], preview=preview).body.decode()
    assert "Simulatie" in html and "FET" in html and "exit +5%" in html
    # +5% on 420 minus 1.26 fees = +19.74; hard stop -3% = -13.86
    assert "+19.74 €" in html and "-13.86 €" in html
    assert 'action="/live/momentum/commit?bases=FET&amp;at=2026-09-07T17:45:00+00:00"' in html
    assert "echt geld" in html and 'http-equiv="refresh"' not in html
    assert "<script" not in html
    # Commit in progress hides the button and shows the notice.
    status["commit"] = {"started_at": "2026-09-07T17:50:00+00:00", "bases": ["FET"], "done": False}
    html = render_momentum_dashboard(status, [], preview=preview).body.decode()
    assert "Uitvoering bezig" in html and "/live/momentum/commit?" not in html
    # Empty preview: nothing to commit, but the simulate button stays.
    html = render_momentum_dashboard(
        status, [], preview={**preview, "planned": [], "ok": False, "reasons": ["btc_weak"]}
    ).body.decode()
    assert "niets kopen" in html and "btc_weak" in html and "Simuleer beslissing nu" in html


def test_dashboard_renders_positions_decision_and_ledger():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    status = {
        "running": True,
        "dry_run": False,
        "venue": "bitvavo",
        "config": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in DeskConfig().__dict__.items()
        },
        "positions": [
            {
                "holding_id": "h-dot",
                "base": "DOT",
                "entry_price": 3.2,
                "quantity": 156.25,
                "notional_eur": 500,
                "age_h": 2.5,
                "peak_return": 0.045,
                "mark": 3.3,
                "gross_return": 0.03125,
                "unrealized_net_eur": 14.1,
                "entry_reason": "excess=+0.09",
            }
        ],
        "risk": {"day_realized_eur": -3.0, "week_realized_eur": 12.0, "entries_allowed": True},
        "last_regime": {
            "at": "2026-09-08T00:00:00+00:00",
            "ok": True,
            "btc_ret": 0.006,
            "breadth": 0.9,
            "reasons": [],
            "candidates": [{"base": "DOT", "excess": 0.09, "from_high": -0.002}],
            "entries": ["DOT"],
            "alphai": {"macro_caution": True, "avoid": ["XRP"], "picks": []},
        },
        "cash_eur": 1500.0,
        "exposure_eur": 515.6,
        "equity_eur": 2015.6,
        "realized_total_eur": 9.0,
        "trade_count": 2,
        "unrealized_net_eur": 14.1,
        "next_decision": "2026-09-09T00:00:00+00:00",
    }
    rows = [
        {
            "ts": "2026-09-08T00:00:40+00:00",
            "event": "entry",
            "base": "DOT",
            "price": 3.2,
            "notional_eur": 500,
            "fee_eur": 0.75,
            "reason": "excess=+0.09",
        },
        {
            "ts": "2026-09-08T01:30:00+00:00",
            "event": "exit",
            "base": "SOL",
            "price": 90.1,
            "notional_eur": 507,
            "fee_eur": 0.76,
            "reason": "trail",
            "gross_return": 0.0156,
            "peak_return": 0.0495,
            "net_eur": 6.3,
        },
    ]
    html = render_momentum_dashboard(status, rows).body.decode()
    assert "LIVE" in html and "REGIME ON" in html
    assert "DOT" in html and "trail" in html and "2,015.60" in html
    # Ratchet active (peak 4.5% >= 4%) -> tight trail shown at 2%.
    assert "(2.0%)" in html
    assert "<script" not in html  # server-rendered, no JS surface
    # Sell button is a GET to the confirmation step, never a direct POST.
    assert 'name="sell" value="h-dot"' in html and "/live/momentum/sell" not in html
    confirm = render_momentum_dashboard(status, rows, sell="h-dot").body.decode()
    assert "Verkoop bevestigen" in confirm
    assert 'action="/live/momentum/sell?holding_id=h-dot"' in confirm
    assert "holding_id=h-dot&amp;urgent=1" in confirm
    assert 'http-equiv="refresh"' not in confirm  # page holds still while confirming
    gone = render_momentum_dashboard(status, rows, sell="nope").body.decode()
    assert "niet (meer) gevonden" in gone
    busy = dict(
        status,
        manual_exit={"base": "DOT", "done": False, "started_at": "2026-09-08T01:00:00+00:00"},
    )
    html_busy = render_momentum_dashboard(busy, rows).body.decode()
    assert "Verkoop DOT bezig" in html_busy and "disabled" in html_busy
    done = dict(
        status,
        manual_exit={
            "base": "DOT",
            "done": True,
            "finished_at": "2026-09-08T01:01:00+00:00",
            "result": {"ok": True, "partial": False},
        },
    )
    assert "DOT verkocht om" in render_momentum_dashboard(done, rows).body.decode()


def test_manual_sell_books_exit_and_runs_in_background(tmp_path):
    from bot.live.momentum_runner import MomentumDeskManager

    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 32 * 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=2000.0,
        okx_cash=0.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )

    async def scenario():
        await r.decide_now(execute=True)
        assert [h.pos.base for h in r.holdings] == ["SOL"]
        hid = r.holdings[0].holding_id
        assert r.status()["positions"][0]["holding_id"] == hid
        assert (await r.sell_now("nope"))["reason"] == "unknown_holding"
        m = MomentumDeskManager()
        assert m.sell(hid)["reason"] == "not_running"
        m._runner = r
        m._task = asyncio.create_task(asyncio.sleep(10))
        assert m.sell("nope")["reason"] == "unknown_holding"
        out = m.sell(hid)
        assert out["ok"] and m.sell(hid)["reason"] == "sell_in_progress"
        await m._sell_task
        m._task.cancel()
        return m.status()

    status = asyncio.run(scenario())
    me = status["manual_exit"]
    assert me["done"] and me["result"]["ok"] and me["result"]["base"] == "SOL"
    assert r.holdings == [] and r.trade_count == 1
    ledger = [json.loads(line) for line in Path(r.opt.ledger_path).read_text().splitlines()]
    exits = [row for row in ledger if row.get("event") == "exit"]
    assert len(exits) == 1 and exits[0]["reason"] == "manual" and exits[0]["base"] == "SOL"
    assert [o["side"] for o in gws["bitvavo"].placed] == ["buy", "sell"]


def test_engine_settings_cap_notional_to_clip():
    from bot.core.config import Settings

    s = Settings(exchange_name="stub", execution_mode="paper")
    cfg = DeskConfig(clip_eur=500.0, alphai_clip_mult=1.3, max_positions=3, strong_clip_mult=1.0)
    out = engine_settings_for_desk(s, cfg, "bitvavo")
    assert out.live_micro_max_notional_eur == pytest.approx(651.0)
    # Strong-tape multiplier stacks on the AlphaI multiplier in the cap.
    strong = engine_settings_for_desk(s, cfg.with_overrides(strong_clip_mult=1.3), "bitvavo")
    assert strong.live_micro_max_notional_eur == pytest.approx(500 * 1.3 * 1.3 + 1)
    assert out.live_micro_symbols == "*" and out.live_micro_venues == "bitvavo"
    assert out.live_micro_max_open_orders_per_venue == 4
    multi = engine_settings_for_desk(s, cfg, "Bitvavo, okx,bitvavo")
    assert multi.live_micro_venues == "bitvavo,okx"


# ------------------------------------------------------- multi-venue routing


class CashGateway(FakeGateway):
    def __init__(self, cash: float, **kwargs):
        super().__init__(**kwargs)
        self.cash = cash

    async def quote_balance_eur(self):
        return self.cash


def _multi_runner(tmp_path, clock, bitvavo_cash, okx_cash, feed=None, **cfg_kwargs):
    cfg_kwargs = {**FLAT_SIZING, **cfg_kwargs}
    gws = {
        "bitvavo": CashGateway(bitvavo_cash, fill_maker_after_polls=1),
        "okx": CashGateway(okx_cash, bid=100.05, ask=100.25, fill_maker_after_polls=1),
    }
    cfg = DeskConfig(**cfg_kwargs)
    opts = RunnerOptions(
        venues=("bitvavo", "okx"),
        state_path=str(tmp_path / "state.json"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        alphai_recommendations_path=None,
        buy_rest_sec=60.0,
        repeg_sec=20.0,
        poll_sec=5.0,
    )
    r = MomentumDeskRunner(
        cfg,
        None,
        options=opts,
        feed=feed or FakeFeed({}),
        clock=clock,
        sleep=clock.sleep,
        gateways=gws,
    )
    return r, gws


def test_route_prefers_primary_and_overflows_to_second_venue(tmp_path):
    clock = FakeClock(T0 / 1000)
    r, _ = _multi_runner(tmp_path, clock, bitvavo_cash=700.0, okx_cash=1900.0)
    asyncio.run(r._refresh_cash())
    assert r.cash_eur == pytest.approx(2600.0)
    # Primary has the clip -> primary, even though OKX is richer.
    assert r._route_entry(600.0) == ("bitvavo", 600.0)
    r.cash_by_venue["bitvavo"] = 300.0
    # Primary short -> second venue takes the full clip.
    assert r._route_entry(600.0) == ("okx", 600.0)
    # Nobody can fund the clip -> richest venue with a reduced clip (>= 50%).
    r.cash_by_venue["okx"] = 400.0
    venue, clip = r._route_entry(600.0)
    assert venue == "okx" and 395.0 < clip < 400.0
    # Too little everywhere -> skip.
    r.cash_by_venue["okx"] = 250.0
    assert r._route_entry(600.0) is None


def test_multi_venue_entry_and_exit_use_position_venue(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=200.0,
        okx_cash=1900.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )
    asyncio.run(r.tick())
    assert [h.pos.venue for h in r.holdings] == ["okx"]
    assert gws["bitvavo"].placed == [] and gws["okx"].placed
    assert r.cash_by_venue["okx"] < 1900.0 - 499.0
    ledger = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert '"event": "entry"' in ledger[-1] and '"venue": "okx"' in ledger[-1]
    status = r.status()
    assert status["positions"][0]["venue"] == "okx" and status["venues"] == ["bitvavo", "okx"]
    # Restart keeps the venue on the position; the exit goes to that venue.
    r2, gws2 = _multi_runner(tmp_path, clock, bitvavo_cash=200.0, okx_cash=1400.0, clip_eur=500.0)
    assert r2.holdings[0].pos.venue == "okx"
    fill = asyncio.run(r2._exit(r2.holdings[0], ExitDecision("trail", 0.01, False)))
    assert fill is not None and fill.qty > 0
    assert gws2["bitvavo"].placed == [] and [p["side"] for p in gws2["okx"].placed] == ["sell"]


def test_entry_skipped_when_no_venue_can_fund(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=100.0,
        okx_cash=120.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )
    asyncio.run(r.tick())
    assert r.holdings == [] and not gws["bitvavo"].placed and not gws["okx"].placed
    ledger = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert '"event": "entry_skipped"' in ledger[-1] and "insufficient_cash" in ledger[-1]


def test_resume_flag_round_trips_venues(tmp_path):
    from bot.live.momentum_runner import _read_flag, _write_flag

    state = str(tmp_path / "state.json")
    _write_flag(state, running=True, dry_run=False, venue="bitvavo", venues=["bitvavo", "okx"])
    flag = _read_flag(state)
    assert flag["venues"] == ["bitvavo", "okx"] and flag["venue"] == "bitvavo"
    from bot.live.momentum_runner import parse_venues

    assert parse_venues(flag["venues"]) == ("bitvavo", "okx")
    assert parse_venues("") == ("bitvavo",)


# ------------------------------------------------------- AlphaI on the exit side


def test_alphai_avoid_tightens_trail_but_does_not_dump():
    cfg = DeskConfig(trail_pct=0.03, trail_tight_after=0.0, trail_tight_pct=0.015)
    bearish = AlphaIView(avoid=frozenset({"SOL"}))
    # Peak 104, close 102.5: -1.44% from peak -> inside both trails, hold.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 104.0)
    assert evaluate_exit(pos, [T0, 103, 104, 102.4, 102.5, 1], cfg, alphai=bearish) is None
    # -2.0% from peak: normal trail (3%) holds, AlphaI-tightened trail (1.5%) exits.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 104.0)
    assert evaluate_exit(pos, [T0, 103, 104, 101.8, 101.92, 1], cfg) is None
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 104.0)
    d = evaluate_exit(pos, [T0, 103, 104, 101.8, 101.92, 1], cfg, alphai=bearish)
    assert d is not None and d.reason == "trail_alphai" and not d.urgent
    # A bearish headline on another base changes nothing.
    pos = Position("LINK", 100.0, 5.0, 500.0, T0, 104.0)
    assert evaluate_exit(pos, [T0, 103, 104, 101.8, 101.92, 1], cfg, alphai=bearish) is None
    # Switch off -> plain trail semantics.
    off = cfg.with_overrides(alphai_avoid_tightens_trail=False)
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 104.0)
    assert evaluate_exit(pos, [T0, 103, 104, 101.8, 101.92, 1], off, alphai=bearish) is None


def test_from_exchange_order_nets_fee_charged_in_base():
    from types import SimpleNamespace

    from bot.live.momentum_runner import _from_exchange_order

    order = SimpleNamespace(
        id="1",
        status="filled",
        filled_quantity=956.5779,
        average_price=1.2367,
        fee_cost=1.9131558,
        fee_currency="XRP",
    )
    st = _from_exchange_order(order, "XRPEUR")
    assert st.fee_base_qty == pytest.approx(1.9131558)
    assert st.fee_eur == pytest.approx(1.9131558 * 1.2367)
    assert st.filled_qty == pytest.approx(956.5779)


def test_sell_clamps_to_free_balance_when_fee_was_taken_in_base(tmp_path):
    """OKX credits fill_qty - fee_in_base; selling the book qty used to fail."""
    from bot.live.momentum_desk import Position
    from bot.live.momentum_runner import Holding

    clock = FakeClock((T0 + 60_000) / 1000)
    # Venue only has 4.9 of the 5.0 the book thinks it holds.
    gw = FakeGateway(fill_maker_after_polls=1, free_by_base={"SOL": 4.9})
    r = _runner(tmp_path, gw, clock, universe=("SOL",), clip_eur=500.0, min_volume_eur=0.0)
    r.holdings = [
        Holding(
            pos=Position("SOL", 100.0, 5.0, 500.0, T0, 100.0, venue="bitvavo"),
            holding_id="h1",
        )
    ]
    r.marks["SOL"] = 100.0
    r._gws = {"bitvavo": gw}

    async def go():
        return await r.sell_now("h1", urgent=True)

    out = asyncio.run(go())
    assert out["ok"] and out["sold_qty"] == pytest.approx(4.9)
    assert r.holdings == []
    assert gw.placed[-1]["side"] == "sell" and gw.placed[-1]["qty"] == pytest.approx(4.9)


def test_sell_all_sells_every_holding(tmp_path):
    clock = FakeClock((T0 + 60_000) / 1000)
    gw = FakeGateway(fill_maker_after_polls=1, free_by_base={"SOL": 5.0, "LINK": 4.0})
    r = _runner(tmp_path, gw, clock, universe=("SOL", "LINK"), clip_eur=500.0, min_volume_eur=0.0)
    from bot.live.momentum_desk import Position
    from bot.live.momentum_runner import Holding, MomentumDeskManager

    r.holdings = [
        Holding(pos=Position("SOL", 100.0, 5.0, 500.0, T0, 100.0), holding_id="a"),
        Holding(pos=Position("LINK", 100.0, 4.0, 400.0, T0, 100.0), holding_id="b"),
    ]
    r.marks = {"SOL": 100.0, "LINK": 100.0}
    r._gws = {"bitvavo": gw}

    async def go():
        m = MomentumDeskManager()
        assert m.sell_all()["reason"] == "not_running"
        m._runner = r
        m._task = asyncio.create_task(asyncio.sleep(10))
        out = m.sell_all(urgent=True)
        assert out["ok"] and m.sell_all()["reason"] == "sell_in_progress"
        await m._sell_task
        m._task.cancel()
        return m.status()

    status = asyncio.run(go())
    me = status["manual_exit"]
    assert me["done"] and me["all"] and me["result"]["sold"] == 2
    assert r.holdings == []


def test_daily_report_flags_missed_hour_and_early_manual():
    from bot.live.momentum_daily_report import build_daily_report, report_as_dict
    from bot.live.momentum_desk import BARS_PER_DAY

    cfg = DeskConfig(
        decision_hours_utc=(7, 13),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
        skip_weekend_entries=False,
        strong_clip_mult=1.0,
        weak_clip_mult=1.0,
        max_from_high=0.02,
    )
    # Build candles: BTC flat, SOL strong on hour 10 only path.
    n = 3 * BARS_PER_DAY
    start = T0 - 2 * DAY_MS
    candles = {
        "BTC": _series(start, n, 100.0, 0.0),
        "LINK": _series(start, n, 100.0, 0.0),
        "SOL": _series(start, n, 100.0, 0.0),
    }
    # Pump SOL so hour-10 stats show excess and near high.
    sol = candles["SOL"]
    for _i, row in enumerate(sol):
        if T0 <= row[0] < T0 + DAY_MS:
            hours = (row[0] - T0) / 3_600_000
            # Rise through morning; afternoon dump triggers trail after manual exit.
            mult = 1.0 + 0.04 + 0.002 * max(0, hours) if hours < 10 else 1.02
            row[1] = row[2] = row[3] = row[4] = 100.0 * mult
            if hours >= 10:
                row[3] = 100.0 * 1.02  # low
    day = datetime.fromtimestamp(T0 / 1000, UTC).date()
    ledger = [
        {
            "ts": f"{day.isoformat()}T07:00:10+00:00",
            "event": "decision",
            "ok": False,
            "btc_ret": -0.02,
            "breadth": 0.2,
            "reasons": ["btc_weak"],
            "entries": [],
            "candidates": [],
        },
        {
            "ts": f"{day.isoformat()}T07:30:00+00:00",
            "event": "entry",
            "base": "SOL",
            "qty": 5.0,
            "price": 104.0,
            "notional_eur": 520.0,
        },
        {
            "ts": f"{day.isoformat()}T09:00:00+00:00",
            "event": "exit",
            "base": "SOL",
            "qty": 5.0,
            "price": 104.5,
            "net_eur": 1.0,
            "reason": "manual",
            "peak_return": 0.02,
        },
    ]
    report = build_daily_report(
        day=day,
        cfg=cfg,
        candles_by_base=candles,
        ledger_rows=ledger,
        now_ms=T0 + DAY_MS - BAR_MS,
    )
    d = report_as_dict(report)
    assert d["day"] == day.isoformat()
    assert isinstance(d["missed_entries"], list)
    assert d["exits"] and d["entries"]
    sol_ops = [o for o in d["exit_opportunities"] if o["base"] == "SOL"]
    assert sol_ops and sol_ops[0]["kind"] == "early_manual"
    assert sol_ops[0]["auto_net_eur"] is not None
    assert sol_ops[0]["delta_eur"] is not None


def test_dashboard_sell_all_and_report_render():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    status = {
        "running": True,
        "venues": ["bitvavo"],
        "config": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in DeskConfig().__dict__.items()
        },
        "positions": [
            {
                "holding_id": "h1",
                "base": "SOL",
                "venue": "bitvavo",
                "entry_price": 100.0,
                "quantity": 5,
                "peak_return": 0.02,
                "mark": 101.0,
                "gross_return": 0.01,
                "unrealized_net_eur": 4.0,
                "age_h": 1.0,
                "entry_reason": "x",
            }
        ],
        "risk": {"day_realized_eur": 0, "week_realized_eur": 0, "entries_allowed": True},
        "cash_eur": 1000,
        "exposure_eur": 500,
        "equity_eur": 1500,
        "realized_total_eur": 0,
        "trade_count": 0,
        "unrealized_net_eur": 4.0,
        "next_decision": "2026-09-09T13:00:00+00:00",
    }
    html = render_momentum_dashboard(status, []).body.decode()
    assert "sticky-actions" in html and "Daily report" in html and "Verkoop alles" in html
    assert "pos-cards" in html and 'name="sell" value="h1"' in html
    confirm = render_momentum_dashboard(status, [], sell_all=True).body.decode()
    assert "Alles verkopen?" in confirm and "/live/momentum/sell-all" in confirm
    report = {
        "day": "2026-09-09",
        "summary": "test",
        "realized_net_eur": 12.0,
        "decisions": [],
        "entries": [],
        "exits": [],
        "missed_entries": [
            {
                "hour_utc": 16,
                "bases": ["DOT"],
                "btc_ret": 0.01,
                "breadth": 0.8,
                "scheduled": False,
                "note": "buiten schema",
                "hypothetical": [
                    {"base": "DOT", "net_eur": 20.0, "reason": "trail", "status": "closed"}
                ],
            }
        ],
        "exit_opportunities": [],
    }
    rep = render_momentum_dashboard(status, [], report=report).body.decode()
    assert "Gemiste instappen" in rep and "DOT" in rep and 'http-equiv="refresh"' not in rep

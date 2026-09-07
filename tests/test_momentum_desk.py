"""Daily Momentum Desk — decision core, backtester and live order path."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bot.live.momentum_desk import (
    BAR_MS,
    BARS_PER_DAY,
    AlphaIView,
    DeskConfig,
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
    cfg = DeskConfig(universe=tuple(returns))
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


# ------------------------------------------------------------- backtest


def test_simulate_enters_leader_and_exits_on_trail():
    cfg = DeskConfig(universe=("SOL", "LINK"), min_volume_eur=0.0, clip_eur=500.0)
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


# ------------------------------------------------------------ live path


@dataclass
class FakeGateway:
    bid: float = 100.0
    ask: float = 100.2
    fill_maker_after_polls: int | None = None  # None = never fills as maker
    placed: list[dict] = field(default_factory=list)
    _orders: dict[str, dict] = field(default_factory=dict)
    _polls: int = 0

    async def best_bid_ask(self, symbol):
        return self.bid, self.ask

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
            o["status"] = "canceled"
        return OrderState(order_id, o["status"], o["filled"], o["avg"], o["fee"])


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
    cfg = DeskConfig(**cfg_kwargs)
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


def test_buy_fills_as_maker_without_taker(tmp_path):
    gw = FakeGateway(fill_maker_after_polls=2)
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._buy("SOL", 500.0))
    assert fill is not None and not fill.taker
    assert fill.avg_price == 100.0 and len(gw.placed) == 1


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


def test_engine_settings_cap_notional_to_clip():
    from bot.core.config import Settings

    s = Settings(exchange_name="stub", execution_mode="paper")
    cfg = DeskConfig(clip_eur=500.0, alphai_clip_mult=1.3, max_positions=3)
    out = engine_settings_for_desk(s, cfg, "bitvavo")
    assert out.live_micro_max_notional_eur == pytest.approx(651.0)
    assert out.live_micro_symbols == "*" and out.live_micro_venues == "bitvavo"
    assert out.live_micro_max_open_orders_per_venue == 4

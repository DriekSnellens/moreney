"""Paper runner/manager for the short-weakest sleeve (never live-orders)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.live.momentum_runner import CandleFeed
from bot.live.momentum_short_weakest import (
    ShortPosition,
    ShortWeakestConfig,
    alphai_is_stale,
    btc_bear_ok,
    default_config,
    evaluate_short_exit,
    fetch_daily_closes,
    fetch_daily_ohlc,
    load_alphai_view,
    rank_weakest,
    select_shorts,
    _atr14,
)

logger = logging.getLogger("bot.live.momentum_short_weakest_runner")


def _flag_path(state_path: str) -> Path:
    return Path(state_path).with_name("momentum_short_weakest_running.json")


def _write_flag(state_path: str, **payload: Any) -> None:
    path = _flag_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"updated_at": datetime.now(UTC).isoformat(), **payload}),
        encoding="utf-8",
    )


def _read_flag(state_path: str) -> dict[str, Any] | None:
    path = _flag_path(state_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def config_from_settings(settings: Settings | None = None) -> ShortWeakestConfig:
    settings = settings or get_settings()
    base = default_config()

    def _f(name: str, default: float) -> float:
        raw = getattr(settings, name, default)
        return float(default if raw is None else raw)

    def _i(name: str, default: int) -> int:
        raw = getattr(settings, name, default)
        return int(default if raw is None else raw)

    def _b(name: str, default: bool) -> bool:
        raw = getattr(settings, name, default)
        return bool(default if raw is None else raw)

    return replace(
        base,
        book_eur=_f("momentum_short_weakest_book_eur", base.book_eur),
        day_loss_limit_eur=_f(
            "momentum_short_weakest_day_loss_limit_eur", base.day_loss_limit_eur
        ),
        week_loss_limit_eur=_f(
            "momentum_short_weakest_week_loss_limit_eur", base.week_loss_limit_eur
        ),
        top_n=_i("momentum_short_weakest_top_n", base.top_n),
        lookback_days=_i("momentum_short_weakest_lookback_days", base.lookback_days),
        rebalance_days=_i("momentum_short_weakest_rebalance_days", base.rebalance_days),
        mom_floor=_f("momentum_short_weakest_mom_floor", base.mom_floor),
        skip_days=_i("momentum_short_weakest_skip_days", base.skip_days),
        bounce_block_pct=_f(
            "momentum_short_weakest_bounce_block_pct", base.bounce_block_pct
        ),
        trail_pct=_f("momentum_short_weakest_trail_pct", base.trail_pct),
        hard_stop_pct=_f("momentum_short_weakest_hard_stop_pct", base.hard_stop_pct),
        max_weight=_f("momentum_short_weakest_max_weight", base.max_weight),
        deploy_frac=_f("momentum_short_weakest_deploy_frac", base.deploy_frac),
        vol_spike_exit=_b("momentum_short_weakest_vol_spike_exit", base.vol_spike_exit),
        idle_fill_enabled=_b(
            "momentum_short_weakest_idle_fill_enabled", base.idle_fill_enabled
        ),
        only_when_core_idle=_b(
            "momentum_short_weakest_only_when_core_idle", base.only_when_core_idle
        ),
        cover_when_core_active=_b(
            "momentum_short_weakest_cover_when_core_active",
            base.cover_when_core_active,
        ),
    )


def probe_core_desk() -> dict[str, Any]:
    """Read core momentum desk: idle when no open positions."""
    try:
        from bot.live.momentum_runner import get_momentum_desk_manager

        st = get_momentum_desk_manager().status()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "idle": True,
            "n_positions": 0,
            "reason": f"probe_failed:{exc}",
        }
    positions = st.get("positions") or []
    n = sum(1 for p in positions if float(p.get("quantity") or 0.0) > 1e-12)
    regime = st.get("last_regime") or {}
    return {
        "ok": True,
        "idle": n == 0,
        "n_positions": n,
        "running": bool(st.get("running")),
        "regime_label": regime.get("regime_label") or regime.get("label"),
        "soft": bool(regime.get("soft")),
        "reasons": list(regime.get("reasons") or [])[:6],
        "risk_block": regime.get("risk_block") or "",
    }


class ShortWeakestPaperRunner:
    """Synthetic short book marked with Bitvavo public prices. Paper only."""

    def __init__(
        self,
        cfg: ShortWeakestConfig,
        *,
        state_path: str,
        ledger_path: str,
        alphai_path: str,
        feed: CandleFeed | None = None,
    ) -> None:
        self.cfg = cfg
        self.state_path = state_path
        self.ledger_path = ledger_path
        self.alphai_path = alphai_path
        self._feed = feed or CandleFeed()
        self.positions: list[ShortPosition] = []
        self.cash_eur = float(cfg.book_eur)
        self.realized_total_eur = 0.0
        self.day_realized_eur = 0.0
        self.week_realized_eur = 0.0
        self._day_key = ""
        self._week_key = ""
        self.marks: dict[str, float] = {}
        self.mark_ts: dict[str, float] = {}
        self.last_regime: dict[str, Any] = {}
        self.last_rebalance_ms = 0
        self._last_idle_decide_ms = 0
        self._core_snapshot: dict[str, Any] = {}
        self._decide_lock = asyncio.Lock()
        self._load_state()

    def _load_state(self) -> None:
        p = Path(self.state_path)
        if not p.exists():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        self.cash_eur = float(raw.get("cash_eur", self.cfg.book_eur))
        self.realized_total_eur = float(raw.get("realized_total_eur") or 0.0)
        self.day_realized_eur = float(raw.get("day_realized_eur") or 0.0)
        self.week_realized_eur = float(raw.get("week_realized_eur") or 0.0)
        self.last_rebalance_ms = int(raw.get("last_rebalance_ms") or 0)
        self.positions = [
            ShortPosition.from_dict(row) for row in (raw.get("positions") or [])
        ]
        self.last_regime = dict(raw.get("last_regime") or {})

    def _save_state(self) -> None:
        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cash_eur": self.cash_eur,
            "realized_total_eur": self.realized_total_eur,
            "day_realized_eur": self.day_realized_eur,
            "week_realized_eur": self.week_realized_eur,
            "last_rebalance_ms": self.last_rebalance_ms,
            "positions": [p.to_dict() for p in self.positions],
            "last_regime": self.last_regime,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        Path(self.state_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _ledger_append(self, row: Mapping[str, Any]) -> None:
        Path(self.ledger_path).parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": datetime.now(UTC).isoformat(),
            **dict(row),
        }
        with Path(self.ledger_path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")

    def _roll_risk_windows(self, now: datetime) -> None:
        day = now.strftime("%Y-%m-%d")
        iso = now.isocalendar()
        week = f"{iso.year}-W{iso.week:02d}"
        if day != self._day_key:
            self._day_key = day
            self.day_realized_eur = 0.0
        if week != self._week_key:
            self._week_key = week
            self.week_realized_eur = 0.0

    def _deployed(self) -> float:
        return sum(max(0.0, p.notional_eur) for p in self.positions)

    def _unrealized(self) -> float:
        total = 0.0
        for p in self.positions:
            mark = self.marks.get(p.base) or p.entry_price
            total += p.unrealized_net(mark, self.cfg.fee_rt)
        return total

    async def _refresh_marks(self) -> None:
        bases = {p.base for p in self.positions} | {"BTC"}
        for base in bases:
            px = await self._feed.last_price(base)
            if px and px > 0:
                self.marks[base] = float(px)
                self.mark_ts[base] = time.time()

    def status(self) -> dict[str, Any]:
        now = time.time()
        positions = []
        for p in self.positions:
            mark = self.marks.get(p.base)
            age = None
            if p.base in self.mark_ts:
                age = round(now - self.mark_ts[p.base], 1)
            gross = p.short_return(mark) if mark else None
            peak = p.peak_return
            if mark:
                peak = max(peak, p.short_return(mark))
            positions.append(
                {
                    "holding_id": p.holding_id,
                    "base": p.base,
                    "side": "short",
                    "venue": "paper",
                    "entry_price": p.entry_price,
                    "quantity": p.notional_eur / p.entry_price if p.entry_price else 0.0,
                    "notional_eur": round(p.notional_eur, 2),
                    "opened": datetime.fromtimestamp(p.opened_ms / 1000, UTC).isoformat(),
                    "age_h": round((now * 1000 - p.opened_ms) / 3_600_000, 2),
                    "peak_return": round(peak, 5),
                    "mark": mark,
                    "mark_source": "ticker" if mark else None,
                    "mark_age_sec": age,
                    "gross_return": round(gross, 5) if gross is not None else None,
                    "unrealized_net_eur": (
                        round(p.unrealized_net(mark, self.cfg.fee_rt), 2) if mark else None
                    ),
                    "entry_reason": p.entry_reason,
                    "exiting": False,
                    "trail_stop_px": (
                        round(p.entry_price * (1.0 - peak + self.cfg.trail_pct), 6)
                        if mark
                        else None
                    ),
                    "hard_stop_px": round(p.entry_price * (1.0 + self.cfg.hard_stop_pct), 6),
                }
            )
        unreal = self._unrealized()
        equity = self.cash_eur + unreal
        core = self._core_snapshot or probe_core_desk()
        return {
            "desk": "momentum_short_weakest",
            "mode": "short_weakest_paper",
            "dry_run": True,
            "paper_only": True,
            "role": (
                "bear_harvest"
                if (self.last_regime or {}).get("bear_ok")
                else "standby_btc_above_sma200"
            ),
            "core_idle": bool(core.get("idle")),
            "core": core,
            "cash_eur": round(self.cash_eur, 2),
            "equity_eur": round(equity, 2),
            "book_eur": float(self.cfg.book_eur),
            "deployed_eur": round(self._deployed(), 2),
            "book_left_eur": round(max(0.0, self.cfg.book_eur - self._deployed()), 2),
            "exposure_eur": round(self._deployed(), 2),
            "unrealized_net_eur": round(unreal, 2),
            "realized_total_eur": round(self.realized_total_eur, 2),
            "positions": positions,
            "last_regime": self.last_regime,
            "risk": {
                "day_realized_eur": round(self.day_realized_eur, 2),
                "week_realized_eur": round(self.week_realized_eur, 2),
                "day_loss_limit_eur": self.cfg.day_loss_limit_eur,
                "week_loss_limit_eur": self.cfg.week_loss_limit_eur,
            },
            "config": {
                "book_eur": self.cfg.book_eur,
                "trail_pct": self.cfg.trail_pct,
                "trail_tight_after": 0.0,
                "trail_tight_pct": self.cfg.trail_pct,
                "hard_stop_pct": self.cfg.hard_stop_pct,
                "lookback_days": self.cfg.lookback_days,
                "top_n": self.cfg.top_n,
                "rebalance_days": self.cfg.rebalance_days,
                "mom_floor": self.cfg.mom_floor,
                "max_weight": self.cfg.max_weight,
                "deploy_frac": self.cfg.deploy_frac,
                "skip_days": self.cfg.skip_days,
                "bounce_block_pct": self.cfg.bounce_block_pct,
                "vol_spike_exit": self.cfg.vol_spike_exit,
                "only_when_core_idle": self.cfg.only_when_core_idle,
                "cover_when_core_active": self.cfg.cover_when_core_active,
                "idle_fill_enabled": self.cfg.idle_fill_enabled,
                "idle_excess_floor": self.cfg.idle_excess_floor,
                "decision_hours_utc": list(self.cfg.decision_hours_utc),
            },
            "next_decision": self._next_decision_iso(),
        }

    def _next_decision_iso(self) -> str | None:
        now = datetime.now(UTC)
        hours = sorted(self.cfg.decision_hours_utc)
        for h in hours:
            cand = now.replace(hour=h, minute=0, second=0, microsecond=0)
            if cand > now:
                return cand.isoformat()
        # tomorrow first hour
        from datetime import timedelta

        tomorrow = (now + timedelta(days=1)).replace(
            hour=hours[0], minute=0, second=0, microsecond=0
        )
        return tomorrow.isoformat()

    def _entries_allowed(self) -> tuple[bool, str]:
        if self.day_realized_eur <= -abs(self.cfg.day_loss_limit_eur):
            return False, "day_loss_limit"
        if self.week_realized_eur <= -abs(self.cfg.week_loss_limit_eur):
            return False, "week_loss_limit"
        return True, ""

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        async with self._decide_lock:
            return await self._decide_unlocked(execute=execute)

    async def _decide_unlocked(self, *, execute: bool = True) -> dict[str, Any]:
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)
        self._roll_risk_windows(now)
        await self._refresh_marks()

        core = probe_core_desk()
        self._core_snapshot = core
        core_idle = bool(core.get("idle"))

        # When core is active, stand down: cover shorts and skip new entries.
        if (
            execute
            and self.cfg.cover_when_core_active
            and not core_idle
            and self.positions
        ):
            for pos in list(self.positions):
                await self._close(pos, reason="core_active_cover", now_ms=now_ms)
            self._save_state()

        alphai, alphai_meta = load_alphai_view(self.alphai_path)
        stale = alphai_is_stale(alphai, self.cfg, now_ms=now_ms)

        closes_map: dict[str, list[float]] = {}
        need = ("BTC", *self.cfg.universe)
        for base in need:
            try:
                rows = await asyncio.to_thread(fetch_daily_closes, base, days=260)
                closes_map[base] = [c for _, c in rows]
                await asyncio.sleep(0.05)
            except Exception as exc:  # noqa: BLE001
                logger.warning("short-weakest daily fetch failed %s: %s", base, exc)

        btc_closes = closes_map.get("BTC") or []
        bear_ok, bear_meta = btc_bear_ok(btc_closes, self.cfg)

        # Mode selection: hard bear → absolute weakness; else idle-fill excess.
        use_idle_fill = (
            bool(self.cfg.idle_fill_enabled)
            and core_idle
            and not bear_ok
        )
        rank_mode = "excess" if use_idle_fill else "absolute"
        cands, rejected = rank_weakest(
            closes_map,
            self.cfg,
            alphai=alphai,
            mode=rank_mode,
            btc_closes=btc_closes,
        )

        allowed, why = self._entries_allowed()
        if self.cfg.only_when_core_idle and not core_idle:
            allowed, why = False, "core_active"
        elif bear_ok:
            pass  # hard bear always ok for absolute shorts
        elif use_idle_fill:
            pass  # idle-fill substitutes for SMA200 bear gate
        else:
            allowed, why = False, "btc_not_bear"
        if self.cfg.alphai_require_macro_or_bear and not alphai.macro_caution and bear_ok:
            pass
        if stale and self.cfg.alphai_enabled:
            alphai = replace_alphai_neutral(alphai)

        due = (now_ms - self.last_rebalance_ms) >= self.cfg.rebalance_days * 86_400_000
        if self.last_rebalance_ms <= 0:
            due = True
        # Idle-fill: if flat and core idle, allow a fresh select even mid-cycle.
        if use_idle_fill and not self.positions:
            due = True

        planned: list[dict[str, Any]] = []
        if allowed and due:
            if execute and self.positions:
                for pos in list(self.positions):
                    await self._close(
                        pos,
                        reason=("idle_fill_rebalance" if use_idle_fill else "rebalance"),
                        now_ms=now_ms,
                    )
            cash_for_entries = self.cash_eur
            planned = select_shorts(
                cands, self.cfg, cash_eur=cash_for_entries, held=set()
            )

        summary: dict[str, Any] = {
            "at": now.isoformat(),
            "trigger": "decide",
            "executed": bool(execute),
            "ok": bool(allowed),
            "desk": "momentum_short_weakest",
            "rank_mode": rank_mode,
            "idle_fill": use_idle_fill,
            "core": core,
            "bear": bear_meta,
            "rebalance_due": due,
            "risk_block": "" if allowed else why,
            "candidates": [
                {
                    "base": c["base"],
                    "mom": round(float(c["mom"]), 4),
                    "score": round(float(c.get("score", c["mom"])), 4),
                }
                for c in cands[:8]
            ],
            "rejected": rejected[:10],
            "entries": [p["base"] for p in planned],
            "planned": planned,
            "alphai": {
                "macro_caution": alphai.macro_caution,
                "avoid": sorted(alphai.avoid),
                "picks": sorted(alphai.picks),
                "stale": stale,
                **{k: alphai_meta.get(k) for k in ("ok", "path")},
            },
        }
        self.last_regime = summary
        if not execute:
            self._save_state()
            return summary

        self._ledger_append({"event": "decision", **summary})
        if allowed and due and planned:
            for row in planned:
                await self._open(row, now_ms=now_ms)
            self.last_rebalance_ms = now_ms
            self._last_idle_decide_ms = now_ms
        self._save_state()
        return summary

    async def _open(self, row: Mapping[str, Any], *, now_ms: int) -> None:
        base = str(row["base"])
        if any(p.base == base for p in self.positions):
            return
        notional = float(row["notional_eur"])
        mark = self.marks.get(base)
        if not mark:
            px = await self._feed.last_price(base)
            mark = float(px) if px else 0.0
        if mark <= 0 or notional > self.cash_eur:
            return
        # ATR from recent dailies
        atr = 0.02
        try:
            ohlc = await asyncio.to_thread(fetch_daily_ohlc, base, days=30)
            atr = _atr14(ohlc)
        except Exception:  # noqa: BLE001
            pass
        fee = notional * (self.cfg.fee_rt / 2)
        self.cash_eur -= fee  # open fee; notional is synthetic collateral
        # Reserve notional from cash so book stays coherent
        self.cash_eur -= notional
        pos = ShortPosition(
            base=base,
            entry_price=mark,
            notional_eur=notional,
            opened_ms=now_ms,
            entry_reason=",".join(str(x) for x in (row.get("reasons") or [])),
            atr14=atr,
        )
        self.positions.append(pos)
        self.marks[base] = mark
        self.mark_ts[base] = time.time()
        self._ledger_append(
            {
                "event": "entry",
                "side": "short",
                "base": base,
                "notional_eur": notional,
                "entry_price": mark,
                "holding_id": pos.holding_id,
                "reason": pos.entry_reason,
            }
        )

    async def _close(self, pos: ShortPosition, *, reason: str, now_ms: int) -> None:
        mark = self.marks.get(pos.base)
        if not mark:
            px = await self._feed.last_price(pos.base)
            mark = float(px) if px else pos.entry_price
        ret = pos.short_return(mark)
        gross = pos.notional_eur * ret
        fee = pos.notional_eur * (self.cfg.fee_rt / 2)
        net = gross - fee
        # Return reserved notional + PnL
        self.cash_eur += pos.notional_eur + net
        self.realized_total_eur += net
        self.day_realized_eur += net
        self.week_realized_eur += net
        self.positions = [p for p in self.positions if p.holding_id != pos.holding_id]
        self._ledger_append(
            {
                "event": "exit",
                "side": "short",
                "base": pos.base,
                "holding_id": pos.holding_id,
                "entry_price": pos.entry_price,
                "exit_price": mark,
                "notional_eur": pos.notional_eur,
                "gross_return": round(ret, 6),
                "net_eur": round(net, 2),
                "reason": reason,
                "closed_at": datetime.fromtimestamp(now_ms / 1000, UTC).isoformat(),
            }
        )

    async def manage_exits(self) -> None:
        await self._refresh_marks()
        now_ms = int(time.time() * 1000)
        for pos in list(self.positions):
            mark = self.marks.get(pos.base)
            if not mark:
                continue
            ret = pos.short_return(mark)
            pos.peak_return = max(pos.peak_return, ret)
            day_ret = None
            try:
                rows = await self._feed.candles(pos.base, 100)
                if len(rows) >= 2:
                    # approx day ret from ~96 bars ago
                    i0 = max(0, len(rows) - 97)
                    prev = float(rows[i0][4])
                    if prev > 0:
                        day_ret = mark / prev - 1.0
            except Exception:  # noqa: BLE001
                day_ret = None
            decision = evaluate_short_exit(
                pos, mark=mark, day_ret=day_ret, cfg=self.cfg
            )
            if decision:
                await self._close(pos, reason=str(decision["reason"]), now_ms=now_ms)
        self._save_state()

    async def sell(self, holding_id: str) -> dict[str, Any]:
        pos = next((p for p in self.positions if p.holding_id == holding_id), None)
        if pos is None:
            return {"ok": False, "reason": "not_found"}
        await self._close(pos, reason="manual_sell", now_ms=int(time.time() * 1000))
        self._save_state()
        return {"ok": True, "holding_id": holding_id}

    async def sell_all(self) -> dict[str, Any]:
        n = 0
        for pos in list(self.positions):
            await self._close(pos, reason="manual_sell_all", now_ms=int(time.time() * 1000))
            n += 1
        self._save_state()
        return {"ok": True, "closed": n}

    async def run(self, should_stop) -> None:  # noqa: ANN001
        logger.info("short-weakest paper runner started (idle-fill complementary)")
        last_hour_fire: set[str] = set()
        while not should_stop():
            try:
                await self.manage_exits()
                now = datetime.now(UTC)
                now_ms = int(now.timestamp() * 1000)
                core = probe_core_desk()
                self._core_snapshot = core

                # Cover immediately when core takes risk.
                if (
                    self.cfg.cover_when_core_active
                    and not core.get("idle")
                    and self.positions
                ):
                    for pos in list(self.positions):
                        await self._close(
                            pos, reason="core_active_cover", now_ms=now_ms
                        )
                    self._save_state()

                key = f"{now.date()}T{now.hour}"
                scheduled = (
                    now.hour in self.cfg.decision_hours_utc
                    and key not in last_hour_fire
                    and now.minute < 5
                )
                idle_due = (
                    bool(core.get("idle"))
                    and not self.positions
                    and (now_ms - self._last_idle_decide_ms)
                    >= float(self.cfg.idle_decide_every_sec) * 1000.0
                )
                if scheduled or idle_due:
                    await self.decide(execute=True)
                    self._last_idle_decide_ms = now_ms
                    if scheduled:
                        last_hour_fire.add(key)
                if len(last_hour_fire) > 48:
                    last_hour_fire = {key}
            except Exception:  # noqa: BLE001
                logger.exception("short-weakest tick failed")
            await asyncio.sleep(float(self.cfg.tick_sec))
        self._save_state()
        logger.info("short-weakest paper runner stopped")


def replace_alphai_neutral(view):  # noqa: ANN001
    from bot.live.momentum_desk import AlphaIView

    return AlphaIView(
        avoid=view.avoid,
        picks=view.picks,
        macro_caution=view.macro_caution,
        generated_at_ms=view.generated_at_ms,
    )


class ShortWeakestDeskManager:
    """Start/stop singleton — paper only."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._runner: ShortWeakestPaperRunner | None = None
        self._stop = False

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        base: dict[str, Any] = {
            "running": self.running(),
            "enabled_setting": bool(
                getattr(settings, "momentum_short_weakest_enabled", False)
            ),
            "desk": "momentum_short_weakest",
            "mode": "short_weakest_paper",
            "dry_run": True,
            "paper_only": True,
            "allow_live": False,
        }
        if self._runner is not None:
            base.update(self._runner.status())
        elif base["enabled_setting"]:
            # Show idle book even when not running so the dashboard card is visible.
            cfg = config_from_settings(settings)
            base.update(
                {
                    "book_eur": cfg.book_eur,
                    "cash_eur": cfg.book_eur,
                    "equity_eur": cfg.book_eur,
                    "deployed_eur": 0.0,
                    "positions": [],
                    "unrealized_net_eur": 0.0,
                    "realized_total_eur": 0.0,
                    "config": {
                        "book_eur": cfg.book_eur,
                        "trail_pct": cfg.trail_pct,
                        "hard_stop_pct": cfg.hard_stop_pct,
                        "top_n": cfg.top_n,
                        "mom_floor": cfg.mom_floor,
                    },
                }
            )
        return base

    async def start(self, *, settings: Settings | None = None) -> dict[str, Any]:
        if self.running():
            return {
                "ok": False,
                "started": False,
                "reason": "already_running",
                "status": self.status(),
            }
        settings = settings or get_settings()
        if not bool(getattr(settings, "momentum_short_weakest_enabled", False)):
            return {
                "ok": False,
                "started": False,
                "reason": "momentum_short_weakest_enabled_false",
                "hint": "Set MOMENTUM_SHORT_WEAKEST_ENABLED=true",
            }
        cfg = config_from_settings(settings)
        state_path = str(
            getattr(
                settings,
                "momentum_short_weakest_state_path",
                "./data/momentum_short_weakest_state.json",
            )
        )
        ledger_path = str(
            getattr(
                settings,
                "momentum_short_weakest_ledger_path",
                "./data/momentum_short_weakest_ledger.jsonl",
            )
        )
        alphai_path = str(
            getattr(settings, "alphai_daily_recommendations_path", None)
            or "data/alphai/daily_recommendations.json"
        )
        self._runner = ShortWeakestPaperRunner(
            cfg,
            state_path=state_path,
            ledger_path=ledger_path,
            alphai_path=alphai_path,
        )
        self._stop = False
        self._task = asyncio.create_task(
            self._runner.run(lambda: self._stop), name="momentum-short-weakest"
        )
        _write_flag(state_path, running=True, dry_run=True, paper_only=True)
        return {"ok": True, "started": True, "status": self.status()}

    async def stop(self) -> dict[str, Any]:
        self._stop = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=8.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        settings = get_settings()
        state_path = str(
            getattr(
                settings,
                "momentum_short_weakest_state_path",
                "./data/momentum_short_weakest_state.json",
            )
        )
        _write_flag(state_path, running=False, dry_run=True)
        self._task = None
        return {"ok": True, "stopped": True, "status": self.status()}

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        if self._runner is None:
            # ephemeral decide without loop
            settings = get_settings()
            cfg = config_from_settings(settings)
            runner = ShortWeakestPaperRunner(
                cfg,
                state_path=str(
                    getattr(
                        settings,
                        "momentum_short_weakest_state_path",
                        "./data/momentum_short_weakest_state.json",
                    )
                ),
                ledger_path=str(
                    getattr(
                        settings,
                        "momentum_short_weakest_ledger_path",
                        "./data/momentum_short_weakest_ledger.jsonl",
                    )
                ),
                alphai_path=str(
                    getattr(settings, "alphai_daily_recommendations_path", None)
                    or "data/alphai/daily_recommendations.json"
                ),
            )
            return await runner.decide(execute=execute)
        return await self._runner.decide(execute=execute)

    async def sell(self, holding_id: str) -> dict[str, Any]:
        if self._runner is None:
            return {"ok": False, "reason": "not_running"}
        return await self._runner.sell(holding_id)

    async def sell_all(self) -> dict[str, Any]:
        if self._runner is None:
            return {"ok": False, "reason": "not_running"}
        return await self._runner.sell_all()

    async def resume_if_flagged(self, settings: Settings | None = None) -> dict[str, Any] | None:
        settings = settings or get_settings()
        if not bool(getattr(settings, "momentum_short_weakest_enabled", False)):
            return None
        state_path = str(
            getattr(
                settings,
                "momentum_short_weakest_state_path",
                "./data/momentum_short_weakest_state.json",
            )
        )
        flag = _read_flag(state_path)
        if not flag or not flag.get("running"):
            return None
        return await self.start(settings=settings)


_MANAGER: ShortWeakestDeskManager | None = None


def get_short_weakest_desk_manager() -> ShortWeakestDeskManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ShortWeakestDeskManager()
    return _MANAGER


def reset_short_weakest_desk_manager() -> None:
    global _MANAGER
    _MANAGER = None


def short_weakest_flagged_running(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    state_path = str(
        getattr(
            settings,
            "momentum_short_weakest_state_path",
            "./data/momentum_short_weakest_state.json",
        )
    )
    flag = _read_flag(state_path)
    return bool(flag and flag.get("running"))

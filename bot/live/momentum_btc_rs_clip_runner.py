"""Paper runner for BTC-core + RS clip. Never sends venue orders."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.live.momentum_btc_rs_clip import (
    ClipConfig,
    ClipPosition,
    completed_ohlc,
    default_config,
    evaluate_clip,
    fill_px,
)
from bot.live.momentum_runner import CandleFeed
from bot.live.momentum_short_weakest import fetch_daily_ohlc

logger = logging.getLogger("bot.live.momentum_btc_rs_clip_runner")


def _flag_path(state_path: str) -> Path:
    return Path(state_path).with_name("momentum_btc_rs_clip_running.json")


def _write_flag(state_path: str, **payload: Any) -> None:
    path = _flag_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"updated_at": datetime.now(UTC).isoformat(), **payload}),
        encoding="utf-8",
    )


def config_from_settings(settings: Settings | None = None) -> ClipConfig:
    settings = settings or get_settings()
    base = default_config()

    def _f(name: str, default: float) -> float:
        raw = getattr(settings, name, default)
        return float(default if raw is None else raw)

    def _i(name: str, default: int) -> int:
        raw = getattr(settings, name, default)
        return int(default if raw is None else raw)

    return ClipConfig(
        book_eur=_f("momentum_btc_rs_clip_book_eur", base.book_eur),
        btc_frac=_f("momentum_btc_rs_clip_btc_frac", base.btc_frac),
        alt_frac=_f("momentum_btc_rs_clip_alt_frac", base.alt_frac),
        excess_floor=_f("momentum_btc_rs_clip_excess_floor", base.excess_floor),
        lookback_days=_i("momentum_btc_rs_clip_lookback_days", base.lookback_days),
        skip_days=_i("momentum_btc_rs_clip_skip_days", base.skip_days),
        rebalance_days=_i("momentum_btc_rs_clip_rebalance_days", base.rebalance_days),
        sma_n=_i("momentum_btc_rs_clip_sma_n", base.sma_n),
        min_qvol_eur=_f("momentum_btc_rs_clip_min_qvol_eur", base.min_qvol_eur),
        universe=base.universe,
        decision_hours_utc=base.decision_hours_utc,
        tick_sec=base.tick_sec,
        ohlc_days=base.ohlc_days,
        fee_rt=base.fee_rt,
        slip=base.slip,
        min_notional_eur=base.min_notional_eur,
    )


class BtcRsClipPaperRunner:
    """Independent €20k paper book. Does not touch Donchian lots or venue orders."""

    def __init__(
        self,
        cfg: ClipConfig,
        *,
        state_path: str,
        ledger_path: str,
        feed: CandleFeed | None = None,
    ) -> None:
        self.cfg = cfg
        self.state_path = state_path
        self.ledger_path = ledger_path
        self._feed = feed or CandleFeed()
        self.positions: list[ClipPosition] = []
        self.cash_eur = float(cfg.book_eur)
        self.realized_total_eur = 0.0
        self.day_realized_eur = 0.0
        self.last_rebalance_ms = 0
        self.last_decision: dict[str, Any] = {}
        self.marks: dict[str, float] = {}
        self.mark_ts: dict[str, float] = {}
        self._day_key = ""
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
        self.last_rebalance_ms = int(raw.get("last_rebalance_ms") or 0)
        self.positions = [ClipPosition.from_dict(row) for row in (raw.get("positions") or [])]
        self.last_decision = dict(raw.get("last_decision") or {})

    def _save_state(self) -> None:
        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.state_path).write_text(
            json.dumps(
                {
                    "cash_eur": self.cash_eur,
                    "realized_total_eur": self.realized_total_eur,
                    "day_realized_eur": self.day_realized_eur,
                    "last_rebalance_ms": self.last_rebalance_ms,
                    "positions": [p.to_dict() for p in self.positions],
                    "last_decision": self.last_decision,
                    "paper_only": True,
                    "allow_live": False,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _ledger_append(self, row: Mapping[str, Any]) -> None:
        Path(self.ledger_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(self.ledger_path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(UTC).isoformat(), **dict(row)}) + "\n")

    def _roll_day(self, now: datetime) -> None:
        day = now.strftime("%Y-%m-%d")
        if day != self._day_key:
            self._day_key = day
            self.day_realized_eur = 0.0

    def _deployed(self) -> float:
        return sum(p.notional_eur for p in self.positions)

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

    async def _load_ohlc(self) -> dict[str, list[list[float]]]:
        out: dict[str, list[list[float]]] = {}
        bases = ("BTC",) + tuple(self.cfg.universe)
        for base in bases:
            try:
                rows = await asyncio.to_thread(fetch_daily_ohlc, base, days=self.cfg.ohlc_days)
                out[base] = rows
            except Exception as exc:  # noqa: BLE001
                logger.warning("clip ohlc %s failed: %s", base, exc)
                out[base] = []
            await asyncio.sleep(0.05)
        return out

    def _close_lot(self, pos: ClipPosition, px: float, reason: str) -> float:
        fee = pos.notional_eur * (self.cfg.fee_rt / 2)
        ret = px / pos.entry_price - 1.0 if pos.entry_price > 0 else 0.0
        net = pos.notional_eur * ret - fee
        self.cash_eur += pos.notional_eur + net
        self.realized_total_eur += net
        self.day_realized_eur += net
        self.positions = [p for p in self.positions if p is not pos]
        self._ledger_append(
            {
                "event": "exit",
                "desk": "btc_rs_clip_paper",
                "base": pos.base,
                "role": pos.role,
                "venue": "paper",
                "dry_run": True,
                "notional_eur": round(pos.notional_eur, 2),
                "entry_price": pos.entry_price,
                "exit_price": px,
                "net_eur": round(net, 2),
                "fee_eur": round(fee, 4),
                "reason": reason,
                "holding_id": pos.holding_id,
            }
        )
        return net

    def _open_lot(self, base: str, notional: float, px: float, role: str, reasons: list[str]) -> ClipPosition | None:
        if px <= 0 or notional < self.cfg.min_notional_eur:
            return None
        fee = notional * (self.cfg.fee_rt / 2)
        cost = notional + fee
        if cost > self.cash_eur:
            notional = max(0.0, (self.cash_eur / (1.0 + self.cfg.fee_rt / 2)) * 0.995)
            fee = notional * (self.cfg.fee_rt / 2)
            cost = notional + fee
        if notional < self.cfg.min_notional_eur:
            return None
        self.cash_eur -= cost
        now_ms = int(time.time() * 1000)
        pos = ClipPosition(
            base=base,
            entry_price=px,
            notional_eur=notional,
            qty=notional / px,
            opened_ms=now_ms,
            role=role,
            entry_reason=",".join(reasons),
        )
        self.positions.append(pos)
        self._ledger_append(
            {
                "event": "entry",
                "desk": "btc_rs_clip_paper",
                "base": base,
                "role": role,
                "venue": "paper",
                "dry_run": True,
                "notional_eur": round(notional, 2),
                "entry_price": px,
                "fee_eur": round(fee, 4),
                "reason": pos.entry_reason,
                "holding_id": pos.holding_id,
            }
        )
        return pos

    def _last_close(self, ohlc: dict[str, list[list[float]]], base: str) -> float | None:
        rows = completed_ohlc(ohlc.get(base) or [])
        if not rows:
            return None
        return float(rows[-1][4])

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        async with self._decide_lock:
            now = datetime.now(UTC)
            self._roll_day(now)
            ohlc = await self._load_ohlc()
            held = {p.base: p.role for p in self.positions}
            decision = evaluate_clip(
                ohlc,
                self.cfg,
                held=held,
                cash_eur=self.cash_eur,
                deployed_eur=self._deployed(),
                now_ms=int(now.timestamp() * 1000),
                last_rebalance_ms=self.last_rebalance_ms,
                now=now,
            )
            applied: list[dict[str, Any]] = []
            if execute and decision.get("ok"):
                for ex in decision.get("exits") or []:
                    pos = next((p for p in self.positions if p.base == ex["base"]), None)
                    close = self._last_close(ohlc, ex["base"])
                    if pos and close:
                        px = fill_px(close, "sell", slip=self.cfg.slip)
                        net = self._close_lot(pos, px, str(ex.get("reason") or "exit"))
                        applied.append({"action": "exit", "base": ex["base"], "net_eur": round(net, 2)})
                for row in decision.get("entries") or []:
                    close = self._last_close(ohlc, row["base"])
                    if not close:
                        continue
                    px = fill_px(close, "buy", slip=self.cfg.slip)
                    pos = self._open_lot(
                        str(row["base"]),
                        float(row["notional_eur"]),
                        px,
                        str(row.get("role") or "btc"),
                        list(row.get("reasons") or []),
                    )
                    if pos:
                        applied.append({"action": "entry", "base": pos.base, "notional_eur": pos.notional_eur})
                if decision.get("rebalance_due") or applied:
                    self.last_rebalance_ms = int(now.timestamp() * 1000)
            self.last_decision = {
                **decision,
                "applied": applied,
                "execute": bool(execute),
                "at": now.isoformat(),
            }
            await self._refresh_marks()
            self._save_state()
            return {"ok": True, "decision": self.last_decision, "status": self.status()}

    def next_decision(self) -> str:
        now = datetime.now(UTC)
        hours = self.cfg.decision_hours_utc or (0,)
        for h in hours:
            cand = now.replace(hour=int(h), minute=5, second=0, microsecond=0)
            if cand > now:
                return cand.isoformat()
        nxt = (now + timedelta(days=1)).replace(hour=int(hours[0]), minute=5, second=0, microsecond=0)
        return nxt.isoformat()

    def status(self) -> dict[str, Any]:
        now = time.time()
        positions = []
        for p in self.positions:
            mark = self.marks.get(p.base) or p.entry_price
            age_h = max(0.0, (now * 1000 - p.opened_ms) / 3_600_000)
            positions.append(
                {
                    **p.to_dict(),
                    "mark": mark,
                    "mark_age_sec": (now - self.mark_ts[p.base]) if p.base in self.mark_ts else None,
                    "gross_return": p.gross_return(mark),
                    "unrealized_net_eur": round(p.unrealized_net(mark, self.cfg.fee_rt), 2),
                    "age_h": round(age_h, 2),
                    "side": "long",
                    "quantity": p.qty,
                }
            )
        equity = self.cash_eur + self._deployed() + self._unrealized()
        last = self.last_decision or {}
        return {
            "desk": "btc_rs_clip_paper",
            "mode": "btc_rs_clip_paper",
            "paper_only": True,
            "allow_live": False,
            "dry_run": True,
            "book_eur": self.cfg.book_eur,
            "cash_eur": round(self.cash_eur, 2),
            "deployed_eur": round(self._deployed(), 2),
            "equity_eur": round(equity, 2),
            "unrealized_net_eur": round(self._unrealized(), 2),
            "realized_total_eur": round(self.realized_total_eur, 2),
            "day_realized_eur": round(self.day_realized_eur, 2),
            "positions": positions,
            "last_decision": last,
            "live_caption": str(last.get("caption") or ""),
            "risk_on": bool(last.get("risk_on")),
            "sma50": last.get("sma50"),
            "btc": last.get("btc") or self.marks.get("BTC"),
            "gap_pct": last.get("gap_pct"),
            "want_alt": last.get("want_alt"),
            "next_decision": self.next_decision(),
            "config": {
                "btc_frac": self.cfg.btc_frac,
                "alt_frac": self.cfg.alt_frac,
                "excess_floor": self.cfg.excess_floor,
                "lookback_days": self.cfg.lookback_days,
                "skip_days": self.cfg.skip_days,
                "rebalance_days": self.cfg.rebalance_days,
                "sma_n": self.cfg.sma_n,
                "min_qvol_eur": self.cfg.min_qvol_eur,
                "book_eur": self.cfg.book_eur,
                "trail_pct": 0.0,
                "hard_stop_pct": 0.0,
            },
        }

    async def run(self, should_stop) -> None:  # noqa: ANN001
        last_hour_fire: set[str] = set()
        # First loop: take today's completed-bar decision so the paper book is not idle until 00:05.
        try:
            await self.decide(execute=True)
        except Exception:  # noqa: BLE001
            logger.exception("clip kick decide failed")
        while not should_stop():
            try:
                await self._refresh_marks()
                now = datetime.now(UTC)
                key = f"{now.date()}-{now.hour}"
                if now.hour in self.cfg.decision_hours_utc and now.minute < 8 and key not in last_hour_fire:
                    await self.decide(execute=True)
                    last_hour_fire.add(key)
                if len(last_hour_fire) > 48:
                    last_hour_fire = {key}
            except Exception:  # noqa: BLE001
                logger.exception("clip tick failed")
            await asyncio.sleep(float(self.cfg.tick_sec))
        self._save_state()


class BtcRsClipDeskManager:
    """Start/stop singleton — paper only, never live."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._runner: BtcRsClipPaperRunner | None = None
        self._stop = False

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        enabled = bool(getattr(settings, "momentum_btc_rs_clip_enabled", False))
        base: dict[str, Any] = {
            "running": self.running(),
            "enabled_setting": enabled,
            "desk": "btc_rs_clip_paper",
            "mode": "btc_rs_clip_paper",
            "dry_run": True,
            "paper_only": True,
            "allow_live": False,
        }
        if self._runner is not None:
            base.update(self._runner.status())
        elif enabled:
            cfg = config_from_settings(settings)
            state_path = str(
                getattr(settings, "momentum_btc_rs_clip_state_path", "./data/momentum_btc_rs_clip_state.json")
            )
            cash = cfg.book_eur
            realized = 0.0
            positions: list[dict[str, Any]] = []
            last: dict[str, Any] = {}
            try:
                raw = json.loads(Path(state_path).read_text(encoding="utf-8"))
                cash = float(raw.get("cash_eur", cash))
                realized = float(raw.get("realized_total_eur") or 0.0)
                positions = list(raw.get("positions") or [])
                last = dict(raw.get("last_decision") or {})
            except Exception:  # noqa: BLE001
                pass
            deployed = sum(float(p.get("notional_eur") or 0) for p in positions)
            base.update(
                {
                    "book_eur": cfg.book_eur,
                    "cash_eur": round(cash, 2),
                    "equity_eur": round(cash + deployed, 2),
                    "deployed_eur": round(deployed, 2),
                    "positions": positions,
                    "realized_total_eur": round(realized, 2),
                    "unrealized_net_eur": 0.0,
                    "last_decision": last,
                    "live_caption": str(last.get("caption") or "Paper clip staat klaar (niet gestart)."),
                    "config": {
                        "btc_frac": cfg.btc_frac,
                        "alt_frac": cfg.alt_frac,
                        "book_eur": cfg.book_eur,
                        "sma_n": cfg.sma_n,
                    },
                }
            )
        return base

    async def refresh_live(self) -> dict[str, Any]:
        if self._runner is not None:
            await self._runner._refresh_marks()
        return self.status()

    async def start(self, *, settings: Settings | None = None) -> dict[str, Any]:
        if self.running():
            return {"ok": False, "started": False, "reason": "already_running", "status": self.status()}
        settings = settings or get_settings()
        if not bool(getattr(settings, "momentum_btc_rs_clip_enabled", False)):
            return {
                "ok": False,
                "started": False,
                "reason": "momentum_btc_rs_clip_enabled_false",
                "hint": "Set MOMENTUM_BTC_RS_CLIP_ENABLED=true",
            }
        # Hard paper: ignore any allow_live flag if someone adds one later.
        cfg = config_from_settings(settings)
        state_path = str(
            getattr(settings, "momentum_btc_rs_clip_state_path", "./data/momentum_btc_rs_clip_state.json")
        )
        ledger_path = str(
            getattr(settings, "momentum_btc_rs_clip_ledger_path", "./data/momentum_btc_rs_clip_ledger.jsonl")
        )
        self._runner = BtcRsClipPaperRunner(cfg, state_path=state_path, ledger_path=ledger_path)
        self._stop = False
        self._task = asyncio.create_task(self._runner.run(lambda: self._stop), name="momentum-btc-rs-clip")
        _write_flag(state_path, running=True, dry_run=True, paper_only=True, allow_live=False)
        return {"ok": True, "started": True, "paper_only": True, "status": self.status()}

    async def stop(self) -> dict[str, Any]:
        self._stop = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=8.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        settings = get_settings()
        state_path = str(
            getattr(settings, "momentum_btc_rs_clip_state_path", "./data/momentum_btc_rs_clip_state.json")
        )
        _write_flag(state_path, running=False, dry_run=True, paper_only=True)
        self._task = None
        return {"ok": True, "stopped": True, "status": self.status()}

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        if self._runner is None:
            settings = get_settings()
            cfg = config_from_settings(settings)
            runner = BtcRsClipPaperRunner(
                cfg,
                state_path=str(
                    getattr(settings, "momentum_btc_rs_clip_state_path", "./data/momentum_btc_rs_clip_state.json")
                ),
                ledger_path=str(
                    getattr(settings, "momentum_btc_rs_clip_ledger_path", "./data/momentum_btc_rs_clip_ledger.jsonl")
                ),
            )
            return await runner.decide(execute=execute)
        return await self._runner.decide(execute=execute)

    async def resume_if_flagged(self, settings: Settings | None = None) -> dict[str, Any] | None:
        settings = settings or get_settings()
        if not bool(getattr(settings, "momentum_btc_rs_clip_enabled", False)):
            return None
        if self.running():
            return {"ok": True, "started": False, "reason": "already_running"}
        return await self.start(settings=settings)


_MANAGER: BtcRsClipDeskManager | None = None


def get_btc_rs_clip_desk_manager() -> BtcRsClipDeskManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = BtcRsClipDeskManager()
    return _MANAGER


def reset_btc_rs_clip_desk_manager() -> None:
    global _MANAGER
    _MANAGER = None

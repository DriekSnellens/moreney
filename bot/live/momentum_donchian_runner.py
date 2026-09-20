"""Paper/live-desk runner for the Donchian sleeves of the loop mix.

Synthetic long books marked with Bitvavo public prices (same pattern as
short-weakest). Allocator sets per-sleeve target EUR; book=0 flattens.
"""

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
from bot.live.desk_allocator import BOOK_EUR, cached_snapshot, target_book
from bot.live.momentum_donchian import (
    DonchianConfig,
    DonchianPosition,
    evaluate_donchian,
    loop_sleeve_configs,
)
from bot.live.momentum_runner import CandleFeed
from bot.live.momentum_short_weakest import fetch_daily_closes, fetch_daily_ohlc

logger = logging.getLogger("bot.live.momentum_donchian_runner")


def _flag_path(state_path: str) -> Path:
    return Path(state_path).with_name("momentum_donchian_running.json")


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


class _SleeveBook:
    def __init__(self, cfg: DonchianConfig, book_eur: float = 0.0) -> None:
        self.cfg = cfg
        self.book_eur = float(book_eur)
        self.cash_eur = float(book_eur)
        self.positions: list[DonchianPosition] = []
        self.realized_total_eur = 0.0
        self.last_decision: dict[str, Any] = {}

    def deployed(self) -> float:
        return sum(p.notional_eur for p in self.positions)

    def unrealized(self, marks: Mapping[str, float], fee_rt: float) -> float:
        u = 0.0
        for p in self.positions:
            m = marks.get(p.base)
            if m:
                u += p.unrealized_net(m, fee_rt)
        return u


class DonchianBundleRunner:
    def __init__(
        self,
        *,
        state_path: str,
        ledger_path: str,
        book_eur: float = BOOK_EUR,
        feed: CandleFeed | None = None,
    ) -> None:
        self.state_path = state_path
        self.ledger_path = ledger_path
        self.book_eur = float(book_eur)
        self._feed = feed or CandleFeed()
        self.sleeves = {c.name: _SleeveBook(c, 0.0) for c in loop_sleeve_configs()}
        self.marks: dict[str, float] = {}
        self.mark_ts: dict[str, float] = {}
        self._btc_closes: list[float] = []
        self._ohlc: dict[str, list[list[float]]] = {}
        self._alloc: dict[str, Any] = {}
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
        for name, sl in self.sleeves.items():
            row = (raw.get("sleeves") or {}).get(name) or {}
            sl.cash_eur = float(row.get("cash_eur", sl.book_eur))
            sl.book_eur = float(row.get("book_eur", sl.book_eur))
            sl.realized_total_eur = float(row.get("realized_total_eur") or 0.0)
            sl.positions = [
                DonchianPosition.from_dict({**p, "sleeve": name})
                for p in (row.get("positions") or [])
            ]
            sl.last_decision = dict(row.get("last_decision") or {})

    def _save_state(self) -> None:
        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "book_eur": self.book_eur,
            "sleeves": {
                name: {
                    "book_eur": sl.book_eur,
                    "cash_eur": sl.cash_eur,
                    "realized_total_eur": sl.realized_total_eur,
                    "positions": [p.to_dict() for p in sl.positions],
                    "last_decision": sl.last_decision,
                }
                for name, sl in self.sleeves.items()
            },
        }
        Path(self.state_path).write_text(json.dumps(payload), encoding="utf-8")

    def _ledger_append(self, row: Mapping[str, Any]) -> None:
        Path(self.ledger_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(self.ledger_path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(UTC).isoformat(), **row}) + "\n")

    async def _refresh_marks(self) -> None:
        bases = {"BTC"}
        for sl in self.sleeves.values():
            bases.update(p.base for p in sl.positions)
            bases.update(sl.cfg.universe)
        for base in sorted(bases):
            try:
                px = await self._feed.last_price(base)
                if px:
                    self.marks[base] = float(px)
                    self.mark_ts[base] = time.time()
            except Exception:  # noqa: BLE001
                continue

    async def _refresh_dailies(self) -> None:
        try:
            rows = await asyncio.to_thread(fetch_daily_closes, "BTC", days=80)
            self._btc_closes = [c for _, c in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("donchian BTC daily failed: %s", exc)
        need = {"BTC"}
        for sl in self.sleeves.values():
            if sl.book_eur > 1 or sl.positions:
                need.update(sl.cfg.universe)
        for base in sorted(need):
            try:
                self._ohlc[base] = await asyncio.to_thread(fetch_daily_ohlc, base, days=40)
                await asyncio.sleep(0.03)
            except Exception as exc:  # noqa: BLE001
                logger.warning("donchian ohlc %s failed: %s", base, exc)

    def _apply_allocator(self) -> dict[str, Any]:
        snap = cached_snapshot(
            self._btc_closes,
            btc_live=self.marks.get("BTC"),
            book=self.book_eur,
        )
        self._alloc = snap
        for name, sl in self.sleeves.items():
            target = float(target_book(name, snap))
            sl.book_eur = target
            # If freshly armed from 0, seed cash to the target (paper sleeve).
            deployed = sl.deployed()
            if sl.cash_eur + deployed < target * 0.5 and not sl.positions:
                sl.cash_eur = target
            # Shrink cash if target dropped (don't create negative).
            if sl.cash_eur + deployed > target + 1:
                sl.cash_eur = max(0.0, target - deployed)
        return snap

    async def _close(self, sl: _SleeveBook, pos: DonchianPosition, *, reason: str, now_ms: int) -> None:
        mark = self.marks.get(pos.base)
        if not mark:
            px = await self._feed.last_price(pos.base)
            mark = float(px) if px else pos.entry_price
        ret = pos.long_return(mark)
        net = pos.notional_eur * ret - pos.notional_eur * (sl.cfg.fee_rt)
        sl.cash_eur += pos.notional_eur + net
        sl.realized_total_eur += net
        sl.positions = [p for p in sl.positions if p.holding_id != pos.holding_id]
        self._ledger_append(
            {
                "event": "exit",
                "side": "long",
                "sleeve": sl.cfg.name,
                "base": pos.base,
                "notional_eur": pos.notional_eur,
                "entry_price": pos.entry_price,
                "exit_price": mark,
                "net_eur": round(net, 2),
                "reason": reason,
                "holding_id": pos.holding_id,
            }
        )

    async def _open(self, sl: _SleeveBook, row: Mapping[str, Any], *, now_ms: int) -> None:
        base = str(row["base"])
        if any(p.base == base for p in sl.positions):
            return
        notional = float(row["notional_eur"])
        mark = self.marks.get(base)
        if not mark:
            px = await self._feed.last_price(base)
            mark = float(px) if px else 0.0
        if mark <= 0 or notional > sl.cash_eur:
            return
        fee = notional * (sl.cfg.fee_rt / 2)
        sl.cash_eur -= fee + notional
        pos = DonchianPosition(
            base=base,
            entry_price=mark,
            notional_eur=notional,
            opened_ms=now_ms,
            entry_reason=",".join(str(x) for x in (row.get("reasons") or [])),
            sleeve=sl.cfg.name,
        )
        sl.positions.append(pos)
        self.marks[base] = mark
        self.mark_ts[base] = time.time()
        self._ledger_append(
            {
                "event": "entry",
                "side": "long",
                "sleeve": sl.cfg.name,
                "base": base,
                "notional_eur": notional,
                "entry_price": mark,
                "holding_id": pos.holding_id,
                "reason": pos.entry_reason,
            }
        )

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        async with self._decide_lock:
            return await self._decide_unlocked(execute=execute)

    async def _decide_unlocked(self, *, execute: bool) -> dict[str, Any]:
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)
        await self._refresh_marks()
        await self._refresh_dailies()
        snap = self._apply_allocator()
        out: dict[str, Any] = {"at": now.isoformat(), "regime": snap.get("label"), "why": snap.get("why"), "sleeves": {}}
        for name, sl in self.sleeves.items():
            held = {p.base for p in sl.positions}
            if sl.book_eur < sl.cfg.min_notional_eur:
                if execute:
                    for pos in list(sl.positions):
                        await self._close(sl, pos, reason="allocator_flatten", now_ms=now_ms)
                sl.last_decision = {
                    "ok": False,
                    "risk_block": "allocator_zero_book",
                    "exits": [{"base": b, "reason": "allocator_flatten"} for b in held],
                    "entries": [],
                    "reasons": ["allocator_zero_book"],
                }
                out["sleeves"][name] = sl.last_decision
                continue
            decision = evaluate_donchian(
                self._ohlc,
                self._btc_closes,
                sl.cfg,
                held=held,
                cash_eur=sl.cash_eur,
                now=now,
            )
            sl.last_decision = decision
            out["sleeves"][name] = {
                "ok": decision.get("ok"),
                "reasons": decision.get("reasons"),
                "risk_block": decision.get("risk_block"),
                "exits": decision.get("exits"),
                "entries": decision.get("entries"),
                "btc": decision.get("btc"),
            }
            if not execute:
                continue
            for ex in decision.get("exits") or []:
                pos = next((p for p in sl.positions if p.base == ex["base"]), None)
                if pos:
                    await self._close(sl, pos, reason=str(ex.get("reason") or "exit"), now_ms=now_ms)
            for row in decision.get("entries") or []:
                await self._open(sl, row, now_ms=now_ms)
            self._ledger_append({"event": "decision", "sleeve": name, **out["sleeves"][name]})
        self._save_state()
        return out

    async def manage_exits(self) -> None:
        """Intraday: Friday flatten + allocator flatten. Channel exits wait for daily decide."""
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)
        snap = self._apply_allocator()
        _ = snap
        for sl in self.sleeves.values():
            if sl.book_eur < sl.cfg.min_notional_eur and sl.positions:
                for pos in list(sl.positions):
                    await self._close(sl, pos, reason="allocator_flatten", now_ms=now_ms)
            if sl.cfg.friday_flatten and now.weekday() >= 4 and sl.positions:
                for pos in list(sl.positions):
                    await self._close(sl, pos, reason="friday_flatten", now_ms=now_ms)
        self._save_state()

    def status(self) -> dict[str, Any]:
        now = time.time()
        sleeves_out = []
        all_pos = []
        realized = 0.0
        unreal = 0.0
        deployed = 0.0
        for name, sl in self.sleeves.items():
            fee = sl.cfg.fee_rt
            u = sl.unrealized(self.marks, fee)
            d = sl.deployed()
            realized += sl.realized_total_eur
            unreal += u
            deployed += d
            pos_rows = []
            for p in sl.positions:
                mark = self.marks.get(p.base)
                pos_rows.append(
                    {
                        "holding_id": p.holding_id,
                        "base": p.base,
                        "side": "long",
                        "sleeve": name,
                        "venue": "paper",
                        "entry_price": p.entry_price,
                        "quantity": p.notional_eur / p.entry_price if p.entry_price else 0.0,
                        "notional_eur": round(p.notional_eur, 2),
                        "opened": datetime.fromtimestamp(p.opened_ms / 1000, UTC).isoformat(),
                        "mark": mark,
                        "mark_age_sec": round(now - self.mark_ts[p.base], 1) if p.base in self.mark_ts else None,
                        "gross_return": round(p.long_return(mark), 5) if mark else None,
                        "unrealized_net_eur": round(p.unrealized_net(mark, fee), 2) if mark else None,
                        "entry_reason": p.entry_reason,
                        "exiting": False,
                    }
                )
            all_pos.extend(pos_rows)
            dec = sl.last_decision or {}
            sleeves_out.append(
                {
                    "id": name,
                    "title": sl.cfg.title,
                    "book_eur": sl.book_eur,
                    "cash_eur": round(sl.cash_eur, 2),
                    "deployed_eur": round(d, 2),
                    "realized_total_eur": round(sl.realized_total_eur, 2),
                    "unrealized_net_eur": round(u, 2),
                    "n_positions": len(sl.positions),
                    "active": sl.book_eur >= sl.cfg.min_notional_eur,
                    "friday_flatten": sl.cfg.friday_flatten,
                    "channel": sl.cfg.channel,
                    "exit_n": sl.cfg.exit_n,
                    "risk_block": dec.get("risk_block") or "",
                    "reasons": dec.get("reasons") or [],
                    "positions": pos_rows,
                }
            )
        return {
            "desk": "momentum_donchian",
            "mode": "donchian_paper",
            "paper_only": True,
            "dry_run": True,
            "book_eur": self.book_eur,
            "allocator": self._alloc,
            "equity_eur": round(sum(sl.cash_eur + sl.unrealized(self.marks, sl.cfg.fee_rt) for sl in self.sleeves.values()), 2),
            "deployed_eur": round(deployed, 2),
            "realized_total_eur": round(realized, 2),
            "unrealized_net_eur": round(unreal, 2),
            "positions": all_pos,
            "sleeves": sleeves_out,
            "next_decision": self._next_decision_iso(),
        }

    def _next_decision_iso(self) -> str | None:
        now = datetime.now(UTC)
        hours = (8, 16)
        for h in hours:
            cand = now.replace(hour=h, minute=0, second=0, microsecond=0)
            if cand > now:
                return cand.isoformat()
        tomorrow = (now + timedelta(days=1)).replace(hour=hours[0], minute=0, second=0, microsecond=0)
        return tomorrow.isoformat()

    async def sell(self, holding_id: str) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        for sl in self.sleeves.values():
            pos = next((p for p in sl.positions if p.holding_id == holding_id), None)
            if pos:
                await self._close(sl, pos, reason="manual_sell", now_ms=now_ms)
                self._save_state()
                return {"ok": True, "holding_id": holding_id}
        return {"ok": False, "reason": "not_found"}

    async def sell_all(self) -> dict[str, Any]:
        n = 0
        now_ms = int(time.time() * 1000)
        for sl in self.sleeves.values():
            for pos in list(sl.positions):
                await self._close(sl, pos, reason="manual_sell_all", now_ms=now_ms)
                n += 1
        self._save_state()
        return {"ok": True, "closed": n}

    async def run(self, should_stop) -> None:  # noqa: ANN001
        logger.info("donchian mix runner started")
        last_hour_fire: set[str] = set()
        while not should_stop():
            try:
                await self._refresh_marks()
                if not self._btc_closes:
                    await self._refresh_dailies()
                self._apply_allocator()
                await self.manage_exits()
                now = datetime.now(UTC)
                key = f"{now.date()}T{now.hour}"
                scheduled = now.hour in (8, 16) and key not in last_hour_fire and now.minute < 5
                if scheduled:
                    await self.decide(execute=True)
                    last_hour_fire.add(key)
                if len(last_hour_fire) > 48:
                    last_hour_fire = {key}
            except Exception:  # noqa: BLE001
                logger.exception("donchian tick failed")
            await asyncio.sleep(30.0)
        self._save_state()
        logger.info("donchian mix runner stopped")


class DonchianDeskManager:
    def __init__(self) -> None:
        self._runner: DonchianBundleRunner | None = None
        self._task: asyncio.Task | None = None
        self._stop = False

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        enabled = bool(getattr(settings, "momentum_donchian_enabled", False))
        base = {
            "ok": True,
            "running": self.running(),
            "enabled_setting": enabled,
            "paper_only": True,
        }
        if self._runner is not None:
            base.update(self._runner.status())
            return base
        state_path = str(getattr(settings, "momentum_donchian_state_path", "./data/momentum_donchian_state.json"))
        if Path(state_path).exists() and self._runner is None:
            # idle snapshot from disk
            tmp = DonchianBundleRunner(
                state_path=state_path,
                ledger_path=str(getattr(settings, "momentum_donchian_ledger_path", "./data/momentum_donchian_ledger.jsonl")),
            )
            base.update(tmp.status())
        return base

    async def start(self, *, settings: Settings | None = None) -> dict[str, Any]:
        if self.running():
            return {"ok": False, "started": False, "reason": "already_running", "status": self.status()}
        settings = settings or get_settings()
        if not bool(getattr(settings, "momentum_donchian_enabled", False)):
            return {
                "ok": False,
                "started": False,
                "reason": "momentum_donchian_enabled_false",
            }
        state_path = str(getattr(settings, "momentum_donchian_state_path", "./data/momentum_donchian_state.json"))
        ledger_path = str(getattr(settings, "momentum_donchian_ledger_path", "./data/momentum_donchian_ledger.jsonl"))
        book = float(getattr(settings, "momentum_multi_strat_book_eur", BOOK_EUR) or BOOK_EUR)
        self._runner = DonchianBundleRunner(state_path=state_path, ledger_path=ledger_path, book_eur=book)
        self._stop = False
        self._task = asyncio.create_task(self._runner.run(lambda: self._stop), name="momentum-donchian")
        _write_flag(state_path, running=True, paper_only=True)
        return {"ok": True, "started": True, "status": self.status()}

    async def stop(self) -> dict[str, Any]:
        self._stop = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=8.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        settings = get_settings()
        state_path = str(getattr(settings, "momentum_donchian_state_path", "./data/momentum_donchian_state.json"))
        _write_flag(state_path, running=False)
        self._task = None
        return {"ok": True, "stopped": True, "status": self.status()}

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        if self._runner is None:
            settings = get_settings()
            runner = DonchianBundleRunner(
                state_path=str(getattr(settings, "momentum_donchian_state_path", "./data/momentum_donchian_state.json")),
                ledger_path=str(getattr(settings, "momentum_donchian_ledger_path", "./data/momentum_donchian_ledger.jsonl")),
                book_eur=float(getattr(settings, "momentum_multi_strat_book_eur", BOOK_EUR) or BOOK_EUR),
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
        if not bool(getattr(settings, "momentum_donchian_enabled", False)):
            return {"started": False, "reason": "disabled"}
        state_path = str(getattr(settings, "momentum_donchian_state_path", "./data/momentum_donchian_state.json"))
        flag = _read_flag(state_path)
        if flag is None or not flag.get("running"):
            # Auto-start with the mix even without a prior flag.
            return await self.start(settings=settings)
        return await self.start(settings=settings)


_manager: DonchianDeskManager | None = None


def get_donchian_desk_manager() -> DonchianDeskManager:
    global _manager
    if _manager is None:
        _manager = DonchianDeskManager()
    return _manager


def reset_donchian_desk_manager() -> None:
    global _manager
    _manager = None

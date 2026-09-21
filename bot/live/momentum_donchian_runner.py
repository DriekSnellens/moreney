"""Donchian sleeves of the loop mix — live longs via LiveMicroEngine.

Paper path stays for tests/shadow. Live buys/sells reuse momentum-desk
LiveGateway (fail-closed policy). Shorts stay paper (spot cannot short).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.live.desk_allocator import BOOK_EUR, cached_snapshot, target_book
from bot.live.momentum_donchian import (
    DonchianConfig,
    DonchianPosition,
    evaluate_donchian,
    friday_close_reached,
    loop_sleeve_configs,
    sleeve_live_caption,
)
from bot.live.momentum_runner import CandleFeed, LiveGateway, engine_settings_for_desk, parse_venues
from bot.live.momentum_short_weakest import fetch_daily_closes, fetch_daily_ohlc

logger = logging.getLogger("bot.live.momentum_donchian_runner")

_MIN_ORDER_EUR = 5.0
_TAKER_CROSS = 0.002


@dataclass
class _Fill:
    qty: float
    avg_price: float
    fee_eur: float

    @property
    def notional(self) -> float:
        return self.qty * self.avg_price


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
        dry_run: bool = True,
        venues: tuple[str, ...] = ("bitvavo",),
        gateways: Mapping[str, Any] | None = None,
    ) -> None:
        self.state_path = state_path
        self.ledger_path = ledger_path
        self.book_eur = float(book_eur)
        self._feed = feed or CandleFeed()
        self.dry_run = bool(dry_run)
        self.venues = tuple(venues) or ("bitvavo",)
        self._gws: dict[str, Any] = dict(gateways or {})
        self.sleeves = {c.name: _SleeveBook(c, 0.0) for c in loop_sleeve_configs()}
        self.marks: dict[str, float] = {}
        self.mark_ts: dict[str, float] = {}
        self._btc_closes: list[float] = []
        self._ohlc: dict[str, list[list[float]]] = {}
        self._alloc: dict[str, Any] = {}
        self._dailies_ts: float = 0.0
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

    async def _refresh_dailies(self, *, force: bool = False) -> None:
        now = time.time()
        if (
            not force
            and self._btc_closes
            and self._ohlc
            and (now - self._dailies_ts) < 900.0
        ):
            return
        try:
            rows = await asyncio.to_thread(fetch_daily_closes, "BTC", days=80)
            if rows:
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
        self._dailies_ts = time.time()

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

    def discard_paper_positions(self, *, reason: str = "paper_reset_for_live") -> int:
        """Drop synthetic lots so live mode does not skip real buys."""
        n = 0
        for sl in self.sleeves.values():
            keep: list[DonchianPosition] = []
            for pos in sl.positions:
                if not pos.is_paper():
                    keep.append(pos)
                    continue
                sl.cash_eur += pos.notional_eur
                self._ledger_append(
                    {
                        "event": "paper_reset",
                        "reason": reason,
                        "sleeve": sl.cfg.name,
                        "base": pos.base,
                        "notional_eur": pos.notional_eur,
                        "holding_id": pos.holding_id,
                    }
                )
                n += 1
            sl.positions = keep
        if n:
            self._save_state()
        return n

    def _primary_gw(self) -> Any | None:
        for v in self.venues:
            if v in self._gws:
                return self._gws[v]
        return next(iter(self._gws.values()), None)

    async def _fill(
        self,
        base: str,
        side: str,
        *,
        qty: float | None = None,
        notional_eur: float | None = None,
    ) -> _Fill | None:
        symbol = f"{base}EUR"
        gw = self._primary_gw()
        if self.dry_run or gw is None:
            px = float(self.marks.get(base) or 0.0)
            if px <= 0:
                last = await self._feed.last_price(base)
                px = float(last or 0.0)
            if px <= 0:
                return None
            q = float(qty) if qty is not None else float(notional_eur or 0.0) / px
            if q <= 0:
                return None
            return _Fill(qty=q, avg_price=px, fee_eur=q * px * 0.0015)
        try:
            bid, ask = await gw.best_bid_ask(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("donchian book %s failed: %s", symbol, exc)
            return None
        price = float(ask) * (1.0 + _TAKER_CROSS) if side == "buy" else float(bid) * (1.0 - _TAKER_CROSS)
        if price <= 0:
            return None
        q = float(qty) if qty is not None else float(notional_eur or 0.0) / price
        if q * price < _MIN_ORDER_EUR:
            return None
        try:
            state = await gw.place_limit(symbol, side, q, price, post_only=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("donchian %s %s rejected: %s", side, symbol, exc)
            return None
        deadline = time.time() + 20.0
        while getattr(state, "status", "") == "open" and time.time() < deadline:
            await asyncio.sleep(1.0)
            try:
                state = await gw.fetch_order(state.order_id, symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("donchian fetch_order %s: %s", symbol, exc)
                break
        if getattr(state, "status", "") == "open":
            try:
                await gw.cancel_order(state.order_id, symbol)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.4)
            try:
                state = await gw.fetch_order(state.order_id, symbol)
            except Exception:  # noqa: BLE001
                pass
        filled = float(getattr(state, "filled_qty", 0.0) or 0.0)
        avg = getattr(state, "avg_price", None)
        if filled <= 0 or not avg:
            return None
        fee = float(getattr(state, "fee_eur", 0.0) or 0.0)
        return _Fill(qty=filled, avg_price=float(avg), fee_eur=fee)

    async def _close(self, sl: _SleeveBook, pos: DonchianPosition, *, reason: str, now_ms: int) -> None:
        qty = float(pos.quantity or 0.0)
        if qty <= 0 and pos.entry_price > 0:
            qty = pos.notional_eur / pos.entry_price
        fill: _Fill | None = None
        if pos.is_paper() or self.dry_run:
            mark = self.marks.get(pos.base)
            if not mark:
                px = await self._feed.last_price(pos.base)
                mark = float(px) if px else pos.entry_price
            fill = _Fill(qty=qty, avg_price=float(mark), fee_eur=pos.notional_eur * (sl.cfg.fee_rt / 2))
        else:
            fill = await self._fill(pos.base, "sell", qty=qty)
            if fill is None:
                logger.warning("donchian live sell failed %s %s — keeping lot", sl.cfg.name, pos.base)
                return
        mark = fill.avg_price
        ret = pos.long_return(mark)
        net = pos.notional_eur * ret - fill.fee_eur
        sl.cash_eur += pos.notional_eur + net
        sl.realized_total_eur += net
        sl.positions = [p for p in sl.positions if p.holding_id != pos.holding_id]
        self._ledger_append(
            {
                "event": "exit",
                "side": "long",
                "sleeve": sl.cfg.name,
                "base": pos.base,
                "venue": pos.venue,
                "notional_eur": pos.notional_eur,
                "quantity": fill.qty,
                "entry_price": pos.entry_price,
                "exit_price": mark,
                "net_eur": round(net, 2),
                "reason": reason,
                "holding_id": pos.holding_id,
                "dry_run": self.dry_run,
            }
        )

    async def _open(self, sl: _SleeveBook, row: Mapping[str, Any], *, now_ms: int) -> None:
        base = str(row["base"])
        if any(p.base == base for p in sl.positions):
            return
        notional = float(row["notional_eur"])
        if notional > sl.cash_eur + 1.0 or notional < sl.cfg.min_notional_eur:
            return
        fill = await self._fill(base, "buy", notional_eur=notional)
        if fill is None or fill.qty <= 0:
            logger.warning("donchian buy skipped %s %s (no fill)", sl.cfg.name, base)
            return
        cost = fill.notional + fill.fee_eur
        if cost > sl.cash_eur + 1.0:
            logger.warning(
                "donchian buy over cash %s %s cost=%.2f cash=%.2f",
                sl.cfg.name,
                base,
                cost,
                sl.cash_eur,
            )
        sl.cash_eur = max(0.0, sl.cash_eur - cost)
        venue = "paper" if self.dry_run or not self._gws else (self.venues[0] if self.venues else "bitvavo")
        pos = DonchianPosition(
            base=base,
            entry_price=fill.avg_price,
            notional_eur=fill.notional,
            opened_ms=now_ms,
            entry_reason=",".join(str(x) for x in (row.get("reasons") or [])),
            sleeve=sl.cfg.name,
            venue=venue,
            quantity=fill.qty,
        )
        sl.positions.append(pos)
        self.marks[base] = fill.avg_price
        self.mark_ts[base] = time.time()
        self._ledger_append(
            {
                "event": "entry",
                "side": "long",
                "sleeve": sl.cfg.name,
                "base": base,
                "venue": venue,
                "notional_eur": round(fill.notional, 2),
                "quantity": fill.qty,
                "entry_price": fill.avg_price,
                "fee_eur": round(fill.fee_eur, 2),
                "holding_id": pos.holding_id,
                "reason": pos.entry_reason,
                "dry_run": self.dry_run,
            }
        )

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        async with self._decide_lock:
            return await self._decide_unlocked(execute=execute)

    async def _decide_unlocked(self, *, execute: bool) -> dict[str, Any]:
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)
        await self._refresh_marks()
        await self._refresh_dailies(force=True)
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
                deployed_eur=sl.deployed(),
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
            # Never open when bars/SMA are missing — even if a stale candidate leaked in.
            block = str(decision.get("risk_block") or "")
            if block not in {"data_not_ready", "sma_unavailable"}:
                for row in decision.get("entries") or []:
                    await self._open(sl, row, now_ms=now_ms)
            self._ledger_append({"event": "decision", "sleeve": name, **out["sleeves"][name]})
        self._save_state()
        return out

    async def manage_exits(self) -> None:
        """Intraday: allocator flatten. Friday-flat waits until Friday UTC close (Sat)."""
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)
        snap = self._apply_allocator()
        _ = snap
        fri_done = friday_close_reached(now)
        for sl in self.sleeves.values():
            if sl.book_eur < sl.cfg.min_notional_eur and sl.positions:
                for pos in list(sl.positions):
                    await self._close(sl, pos, reason="allocator_flatten", now_ms=now_ms)
            if sl.cfg.friday_flatten and fri_done and sl.positions:
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
        cash = 0.0
        mix_label = str((self._alloc or {}).get("label") or "")
        next_iso = self._next_decision_iso()
        for name, sl in self.sleeves.items():
            fee = sl.cfg.fee_rt
            u = sl.unrealized(self.marks, fee)
            d = sl.deployed()
            realized += sl.realized_total_eur
            unreal += u
            deployed += d
            cash += sl.cash_eur
            pos_rows = []
            for p in sl.positions:
                mark = self.marks.get(p.base)
                age_h = (
                    round((now * 1000.0 - p.opened_ms) / 3_600_000.0, 2)
                    if p.opened_ms
                    else None
                )
                pos_rows.append(
                    {
                        "holding_id": p.holding_id,
                        "base": p.base,
                        "side": "long",
                        "sleeve": name,
                        "venue": p.venue or ("paper" if self.dry_run else (self.venues[0] if self.venues else "bitvavo")),
                        "entry_price": p.entry_price,
                        "quantity": p.quantity or (p.notional_eur / p.entry_price if p.entry_price else 0.0),
                        "notional_eur": round(p.notional_eur, 2),
                        "opened": datetime.fromtimestamp(p.opened_ms / 1000, UTC).isoformat(),
                        "age_h": age_h,
                        "mark": mark,
                        "mark_age_sec": round(now - self.mark_ts[p.base], 1) if p.base in self.mark_ts else None,
                        "gross_return": round(p.long_return(mark), 5) if mark else None,
                        "unrealized_net_eur": round(p.unrealized_net(mark, fee), 2) if mark else None,
                        "entry_reason": p.entry_reason,
                        "exiting": False,
                        "peak_return": 0.0,
                        "trail_pct": 0.0,
                        "hard_stop_pct": 0.0,
                    }
                )
            all_pos.extend(pos_rows)
            dec = sl.last_decision or {}
            active = sl.book_eur >= sl.cfg.min_notional_eur
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
                    "active": active,
                    "friday_flatten": sl.cfg.friday_flatten,
                    "channel": sl.cfg.channel,
                    "exit_n": sl.cfg.exit_n,
                    "risk_block": dec.get("risk_block") or "",
                    "reasons": dec.get("reasons") or [],
                    "live_caption": sleeve_live_caption(
                        n_positions=len(sl.positions),
                        friday_flatten=sl.cfg.friday_flatten,
                        risk_block=str(dec.get("risk_block") or ""),
                        mix_label=mix_label,
                        exit_n=int(sl.cfg.exit_n),
                        channel=int(sl.cfg.channel),
                        next_decision=next_iso,
                        allocator_active=active,
                    ),
                    "positions": pos_rows,
                }
            )
        live = (not self.dry_run) and bool(self._gws)
        equity = round(
            sum(
                sl.cash_eur
                + sl.deployed()
                + sl.unrealized(self.marks, sl.cfg.fee_rt)
                for sl in self.sleeves.values()
            ),
            2,
        )
        return {
            "desk": "momentum_donchian",
            "mode": "donchian_live" if live else "donchian_paper",
            "paper_only": not live,
            "dry_run": self.dry_run,
            "allow_live": live,
            "venues": list(self.venues),
            "book_eur": self.book_eur,
            "allocator": self._alloc,
            "equity_eur": equity,
            "cash_eur": round(cash, 2),
            "exposure_eur": round(deployed, 2),
            "deployed_eur": round(deployed, 2),
            "realized_total_eur": round(realized, 2),
            "unrealized_net_eur": round(unreal, 2),
            "positions": all_pos,
            "sleeves": sleeves_out,
            "next_decision": next_iso,
            "decision_hours_utc": list(self._decision_hours()),
            "config": {
                "trail_pct": 0.0,
                "trail_tight_after": 0.0,
                "trail_tight_pct": 0.0,
                "hard_stop_pct": 0.0,
                "max_positions": sum(int(sl.cfg.max_pos) for sl in self.sleeves.values()),
            },
        }

    def _decision_hours(self) -> tuple[int, ...]:
        sl = next(iter(self.sleeves.values()), None)
        hours = tuple(int(h) for h in (sl.cfg.decision_hours_utc if sl else (0,)))
        return hours or (0,)

    def _in_decision_window(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return now.hour in self._decision_hours() and now.minute < 5

    def _next_decision_iso(self) -> str | None:
        now = datetime.now(UTC)
        hours = self._decision_hours()
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
                await self._refresh_dailies()
                self._apply_allocator()
                await self.manage_exits()
                now = datetime.now(UTC)
                key = f"{now.date()}T{now.hour}"
                hours = self._decision_hours()
                scheduled = now.hour in hours and key not in last_hour_fire and now.minute < 5
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
        self._engine: Any = None

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        enabled = bool(getattr(settings, "momentum_donchian_enabled", False))
        allow_live = bool(getattr(settings, "momentum_donchian_allow_live", False))
        base = {
            "ok": True,
            "running": self.running(),
            "enabled_setting": enabled,
            "allow_live": allow_live,
        }
        if self._runner is not None:
            base.update(self._runner.status())
            base["allow_live"] = allow_live
            return base
        state_path = str(getattr(settings, "momentum_donchian_state_path", "./data/momentum_donchian_state.json"))
        if Path(state_path).exists():
            tmp = DonchianBundleRunner(
                state_path=state_path,
                ledger_path=str(getattr(settings, "momentum_donchian_ledger_path", "./data/momentum_donchian_ledger.jsonl")),
            )
            base.update(tmp.status())
            base["allow_live"] = allow_live
            base["running"] = False
        else:
            base.update(
                {
                    "paper_only": not allow_live,
                    "dry_run": True,
                    "mode": "donchian_paper",
                }
            )
        return base

    async def start(self, *, settings: Settings | None = None) -> dict[str, Any]:
        settings = settings or get_settings()
        allow_live = bool(getattr(settings, "momentum_donchian_allow_live", False))
        if self.running():
            already_live = bool(self._runner and not self._runner.dry_run and self._runner._gws)
            if allow_live and already_live:
                return {
                    "ok": False,
                    "started": False,
                    "reason": "already_running",
                    "status": self.status(),
                }
            if not allow_live and self._runner and self._runner.dry_run:
                return {
                    "ok": False,
                    "started": False,
                    "reason": "already_running",
                    "status": self.status(),
                }
            await self.stop()
        if not bool(getattr(settings, "momentum_donchian_enabled", False)):
            return {
                "ok": False,
                "started": False,
                "reason": "momentum_donchian_enabled_false",
            }
        state_path = str(getattr(settings, "momentum_donchian_state_path", "./data/momentum_donchian_state.json"))
        ledger_path = str(getattr(settings, "momentum_donchian_ledger_path", "./data/momentum_donchian_ledger.jsonl"))
        book = float(getattr(settings, "momentum_multi_strat_book_eur", BOOK_EUR) or BOOK_EUR)
        venues = parse_venues(str(getattr(settings, "momentum_donchian_venues", "bitvavo") or "bitvavo"))
        gateways: dict[str, Any] = {}
        dry_run = not allow_live
        if allow_live:
            from bot.live.micro_engine import LiveMicroEngine
            from bot.live.momentum_desk import DeskConfig

            cfg = DeskConfig(
                clip_eur=max(book / 2.0, 500.0),
                max_positions=4,
                day_loss_limit_eur=float(getattr(settings, "momentum_desk_day_loss_limit_eur", 750.0)),
            )
            engine = LiveMicroEngine(engine_settings_for_desk(settings, cfg, venues))
            armed = engine.arm()
            if not armed.get("armed"):
                logger.error("donchian live arm failed: %s", armed)
                return {"ok": False, "started": False, "reason": "arm_failed", "detail": armed}
            for v in venues:
                if engine._registry.get_client(v, enable_trading=True) is None:  # noqa: SLF001
                    logger.warning("donchian: no trading credentials for %s; skipped", v)
                    continue
                gateways[v] = LiveGateway(engine, v)
            if not gateways:
                return {"ok": False, "started": False, "reason": "no_venue_credentials", "venues": list(venues)}
            venues = tuple(v for v in venues if v in gateways)
            dry_run = False
            self._engine = engine
        self._runner = DonchianBundleRunner(
            state_path=state_path,
            ledger_path=ledger_path,
            book_eur=book,
            dry_run=dry_run,
            venues=venues,
            gateways=gateways,
        )
        if not dry_run:
            dropped = self._runner.discard_paper_positions()
            logger.info("donchian live: dropped %s paper lots before venue orders", dropped)
        self._stop = False
        self._task = asyncio.create_task(self._runner.run(lambda: self._stop), name="momentum-donchian")
        _write_flag(state_path, running=True, paper_only=dry_run, dry_run=dry_run)
        if not dry_run:
            asyncio.create_task(self._kick_live_warmup(), name="donchian-live-kick")
        return {"ok": True, "started": True, "dry_run": dry_run, "venues": list(venues), "status": self.status()}

    async def _kick_live_warmup(self) -> None:
        """Load dailies + weekend flatten. Do not open bags outside hour 0."""
        await asyncio.sleep(2.0)
        try:
            if self._runner is None:
                return
            await self._runner._refresh_marks()
            await self._runner._refresh_dailies(force=True)
            self._runner._apply_allocator()
            await self._runner.manage_exits()
            if self._runner._in_decision_window():
                out = await self._runner.decide(execute=True)
                logger.info(
                    "donchian live kick decide: %s",
                    {k: v.get("risk_block") or v.get("reasons") for k, v in (out.get("sleeves") or {}).items()},
                )
            else:
                logger.info("donchian live kick: warmup only (outside UTC decide window)")
        except Exception:  # noqa: BLE001
            logger.exception("donchian live kick warmup failed")

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
        self._engine = None
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

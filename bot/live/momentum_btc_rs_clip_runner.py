"""BTC-core + RS clip runner. Paper by default; live Bitvavo fills when armed."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
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
from bot.live.momentum_runner import (
    CandleFeed,
    LiveGateway,
    engine_settings_for_desk,
    parse_venues,
    venue_cash_caps_from_settings,
)
from bot.live.momentum_short_weakest import fetch_daily_ohlc

logger = logging.getLogger("bot.live.momentum_btc_rs_clip_runner")

_MIN_ORDER_EUR = 5.0
_TAKER_CROSS = 0.002
_EQUITY_CURVE_MAX = 2016
_EQUITY_CURVE_MIN_GAP_SEC = 5.0


@dataclass
class _Fill:
    qty: float
    avg_price: float
    fee_eur: float

    @property
    def notional(self) -> float:
        return self.qty * self.avg_price


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
        alt_trail_pct=_f("momentum_btc_rs_clip_alt_trail_pct", base.alt_trail_pct),
    )


def reserved_quote_eur_from_settings(settings: Settings | None = None) -> float:
    """Bitvavo EUR the 15m desk may still spend — clip must not eat this."""
    settings = settings or get_settings()
    if not bool(getattr(settings, "momentum_desk_enabled", False)):
        return 0.0
    caps = venue_cash_caps_from_settings(settings)
    if "bitvavo" in caps:
        return max(0.0, float(caps["bitvavo"]))
    return max(0.0, float(getattr(settings, "momentum_desk_mix_bitvavo_cap_eur", 0.0) or 0.0))


def load_side_desk_reserved_qty(settings: Settings | None, venue: str) -> dict[str, float]:
    """Qty of each base on ``venue`` that belongs to the 15m desk, not clip."""
    want_v = str(venue or "").strip().lower()
    out: dict[str, float] = {}
    try:
        from bot.live.momentum_runner import get_momentum_desk_manager

        runner = getattr(get_momentum_desk_manager(), "_runner", None)
    except Exception:  # noqa: BLE001
        runner = None
    if runner is not None:
        for h in getattr(runner, "holdings", None) or []:
            pos = getattr(h, "pos", None)
            if pos is None:
                continue
            if str(getattr(pos, "venue", "") or "").strip().lower() != want_v:
                continue
            base = str(getattr(pos, "base", "") or "").upper()
            qty = float(getattr(pos, "quantity", 0.0) or 0.0)
            if base and qty > 0:
                out[base] = out.get(base, 0.0) + qty
        return out
    settings = settings or get_settings()
    path = Path(
        str(getattr(settings, "momentum_desk_state_path", "./data/momentum_desk_state.json"))
    )
    if not path.exists():
        return out
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    for row in raw.get("holdings") or []:
        pos = row.get("pos") or row
        if str(pos.get("venue") or "").strip().lower() != want_v:
            continue
        base = str(pos.get("base") or "").upper()
        qty = float(pos.get("quantity") or 0.0)
        if base and qty > 0:
            out[base] = out.get(base, 0.0) + qty
    return out


class BtcRsClipPaperRunner:
    """€20k clip book. Paper fills unless gateways are armed."""

    def __init__(
        self,
        cfg: ClipConfig,
        *,
        state_path: str,
        ledger_path: str,
        feed: CandleFeed | None = None,
        dry_run: bool = True,
        venues: tuple[str, ...] = ("bitvavo",),
        gateways: Mapping[str, Any] | None = None,
        reserved_quote_eur: float = 0.0,
        reserved_qty: Mapping[str, float] | None = None,
    ) -> None:
        self.cfg = cfg
        self.state_path = state_path
        self.ledger_path = ledger_path
        self._feed = feed or CandleFeed()
        self.dry_run = bool(dry_run)
        self.venues = tuple(venues) or ("bitvavo",)
        self._gws: dict[str, Any] = dict(gateways or {})
        self._reserved_quote_eur = max(0.0, float(reserved_quote_eur or 0.0))
        self._reserved_qty = {str(k).upper(): float(v) for k, v in dict(reserved_qty or {}).items()}
        self.positions: list[ClipPosition] = []
        self.cash_eur = float(cfg.book_eur)
        self.realized_total_eur = 0.0
        self.day_realized_eur = 0.0
        self.last_rebalance_ms = 0
        self.last_decision: dict[str, Any] = {}
        self.marks: dict[str, float] = {}
        self.mark_ts: dict[str, float] = {}
        self.equity_curve: list[list[float]] = []
        self._day_key = ""
        self._decide_lock = asyncio.Lock()
        self._last_curve_save = 0.0
        self._last_reconcile_mono = 0.0
        self._load_state()

    def _desk(self) -> str:
        return "btc_rs_clip_paper" if self.dry_run else "btc_rs_clip"

    def _primary_venue(self) -> str:
        return self.venues[0] if self.venues else "bitvavo"

    def _primary_gw(self) -> Any | None:
        for v in self.venues:
            if v in self._gws:
                return self._gws[v]
        return next(iter(self._gws.values()), None)

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
        curve: list[list[float]] = []
        for row in raw.get("equity_curve") or []:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            try:
                curve.append([float(row[0]), float(row[1])])
            except (TypeError, ValueError):
                continue
        self.equity_curve = curve[-_EQUITY_CURVE_MAX:]

    def _equity_now(self) -> float:
        return self.cash_eur + self._deployed() + self._unrealized()

    def _sample_equity(self, *, persist: bool = False) -> None:
        eq = round(self._equity_now(), 2)
        now_ms = time.time() * 1000.0
        gap_ms = _EQUITY_CURVE_MIN_GAP_SEC * 1000.0
        if self.equity_curve:
            last_t, _last_eq = self.equity_curve[-1]
            if (now_ms - last_t) < gap_ms:
                self.equity_curve[-1] = [round(now_ms), eq]
            else:
                self.equity_curve.append([round(now_ms), eq])
        else:
            self.equity_curve.append([round(now_ms), eq])
        if len(self.equity_curve) > _EQUITY_CURVE_MAX:
            self.equity_curve = self.equity_curve[-_EQUITY_CURVE_MAX:]
        now = time.time()
        if persist or (now - self._last_curve_save) >= _EQUITY_CURVE_MIN_GAP_SEC:
            self._save_state()
            self._last_curve_save = now

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
                    "equity_curve": [
                        [round(float(t), 1), round(float(eq), 2)]
                        for t, eq in self.equity_curve[-_EQUITY_CURVE_MAX:]
                    ],
                    "paper_only": self.dry_run,
                    "allow_live": not self.dry_run,
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
        bases = sorted({p.base for p in self.positions} | {"BTC"})

        async def _one(base: str) -> tuple[str, float | None]:
            try:
                px = await self._feed.last_price(base)
                return base, float(px) if px else None
            except Exception:  # noqa: BLE001
                return base, None

        rows = await asyncio.gather(*(_one(base) for base in bases))
        now = time.time()
        for base, px in rows:
            if px and px > 0:
                self.marks[base] = float(px)
                self.mark_ts[base] = now
        for pos in self.positions:
            mark = float(self.marks.get(pos.base) or 0.0)
            if mark <= 0:
                continue
            if pos.peak_px <= 0:
                # First mark after load: seed from live so we do not trail-stop
                # a drop that happened before the trail was armed.
                pos.peak_px = mark
            else:
                pos.peak_px = max(float(pos.peak_px), mark)
        self._sample_equity()

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

    def discard_paper_positions(self, *, reason: str = "paper_reset_for_live") -> int:
        """Drop synthetic lots so live mode does not skip real buys."""
        n = 0
        keep: list[ClipPosition] = []
        restored = 0.0
        for pos in self.positions:
            if not pos.is_paper():
                keep.append(pos)
                continue
            restored += float(pos.notional_eur)
            self._ledger_append(
                {
                    "event": "paper_reset",
                    "desk": self._desk(),
                    "reason": reason,
                    "base": pos.base,
                    "role": pos.role,
                    "notional_eur": round(pos.notional_eur, 2),
                    "holding_id": pos.holding_id,
                    "venue": pos.venue,
                    "dry_run": self.dry_run,
                }
            )
            n += 1
        self.positions = keep
        if n:
            if keep:
                self.cash_eur += restored
            else:
                self.cash_eur = float(self.cfg.book_eur)
                self.last_rebalance_ms = 0
                self.day_realized_eur = 0.0
            self._save_state()
        elif not keep:
            self.cash_eur = float(self.cfg.book_eur)
            self.last_rebalance_ms = 0
        return n

    def _other_desk_qty(self, base: str) -> float:
        want = str(base or "").upper()
        injected = float(self._reserved_qty.get(want, 0.0))
        live = 0.0
        try:
            from bot.live.momentum_runner import get_momentum_desk_manager

            runner = getattr(get_momentum_desk_manager(), "_runner", None)
            venue = self._primary_venue()
            if runner is not None:
                for h in getattr(runner, "holdings", None) or []:
                    pos = getattr(h, "pos", None)
                    if pos is None:
                        continue
                    if str(getattr(pos, "base", "") or "").upper() != want:
                        continue
                    if str(getattr(pos, "venue", "") or "").strip().lower() != venue:
                        continue
                    live += float(getattr(pos, "quantity", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            live = 0.0
        return max(injected, live)

    def _pos_qty(self, pos: ClipPosition) -> float:
        qty = float(pos.qty or 0.0)
        if qty <= 0 and pos.entry_price > 0:
            qty = pos.notional_eur / pos.entry_price
        return max(0.0, qty)

    async def _held_base(self, base: str) -> float | None:
        gw = self._primary_gw()
        if gw is None:
            return None
        held_fn = getattr(gw, "base_held", None)
        if held_fn is not None:
            try:
                held = await held_fn(base)
                if held is not None:
                    return float(held)
            except Exception as exc:  # noqa: BLE001
                logger.warning("clip held balance %s failed: %s", base, exc)
        free_fn = getattr(gw, "base_free", None)
        if free_fn is None:
            return None
        try:
            free = await free_fn(base)
        except Exception as exc:  # noqa: BLE001
            logger.warning("clip free balance %s failed: %s", base, exc)
            return None
        return None if free is None else float(free)

    async def _recent_sells(self, base: str, *, since_ms: int) -> list[dict[str, float]]:
        gw = self._primary_gw()
        fetch = getattr(gw, "recent_base_sells", None) if gw is not None else None
        if fetch is None:
            return []
        try:
            return list(await fetch(base, since_ms=max(0, int(since_ms) - 60_000)))
        except Exception as exc:  # noqa: BLE001
            logger.info("clip recent sells for %s failed: %s", base, exc)
            return []

    @staticmethod
    def _vwap_for_sold(
        sells: Sequence[Mapping[str, float]], sold_qty: float
    ) -> tuple[float, float] | None:
        if sold_qty <= 0 or not sells:
            return None
        need = float(sold_qty)
        taken_qty = 0.0
        taken_notional = 0.0
        taken_fee = 0.0
        for row in sorted(sells, key=lambda r: float(r.get("ts_ms") or 0), reverse=True):
            qty = float(row.get("qty") or 0.0)
            px = float(row.get("price") or 0.0)
            fee = float(row.get("fee_eur") or 0.0)
            if qty <= 0 or px <= 0:
                continue
            take = min(qty, need - taken_qty)
            if take <= 0:
                break
            frac = take / qty
            taken_qty += take
            taken_notional += take * px
            taken_fee += fee * frac
            if taken_qty + 1e-12 >= need:
                break
        if taken_qty + 1e-8 < need * 0.95:
            return None
        return taken_notional / taken_qty, taken_fee

    def _book_external(
        self,
        pos: ClipPosition,
        *,
        sold_qty: float,
        price: float,
        fee_eur: float,
    ) -> dict[str, Any]:
        """Ledger a Bitvavo-UI sell. Never places an order."""
        book_qty = self._pos_qty(pos)
        entry = float(pos.entry_price or 0.0)
        px = float(price) if price > 0 else entry
        close_qty = max(0.0, min(float(sold_qty), book_qty))
        rem = max(0.0, book_qty - close_qty)
        fee = max(0.0, float(fee_eur))
        frac = close_qty / book_qty if book_qty > 0 else 1.0
        sold_notional = float(pos.notional_eur) * frac
        net = close_qty * (px - entry) - fee if entry > 0 else sold_notional - fee
        self.cash_eur += close_qty * px - fee
        self.realized_total_eur += net
        self.day_realized_eur += net
        row: dict[str, Any] = {
            "event": "exit",
            "desk": self._desk(),
            "base": pos.base,
            "role": pos.role,
            "venue": pos.venue,
            "dry_run": self.dry_run,
            "notional_eur": round(sold_notional, 2),
            "quantity": close_qty,
            "entry_price": entry,
            "exit_price": px,
            "fee_eur": round(fee, 4),
            "net_eur": round(net, 2),
            "reason": "manual_external",
            "holding_id": pos.holding_id,
            "detail": "reconcile_external_delta",
            "remaining_qty": rem,
        }
        self._ledger_append(row)
        if rem * px < _MIN_ORDER_EUR:
            self.positions = [p for p in self.positions if p is not pos]
        else:
            pos.qty = rem
            pos.notional_eur = max(0.0, float(pos.notional_eur) - sold_notional)
        logger.info(
            "clip: booked external sold %s qty=%.8f px=%.4f net=%.2f rem=%.8f",
            pos.base,
            close_qty,
            px,
            net,
            rem,
        )
        return row

    async def _book_if_already_sold(self, pos: ClipPosition, px: float) -> float | None:
        """Ledger a lot that already left Bitvavo. Never places an order."""
        held = await self._held_base(pos.base)
        if held is None:
            return None
        mark = float(px or self.marks.get(pos.base) or pos.entry_price or 0.0)
        avail = max(0.0, float(held) - self._other_desk_qty(pos.base))
        book_qty = self._pos_qty(pos)
        sold = book_qty - avail
        if sold <= 0 or sold * max(mark, 1e-12) < _MIN_ORDER_EUR:
            return None
        vwap = self._vwap_for_sold(
            await self._recent_sells(pos.base, since_ms=int(pos.opened_ms or 0)),
            sold,
        )
        if vwap is not None:
            book_px, fee = vwap
        else:
            book_px, fee = (mark if mark > 0 else float(pos.entry_price)), 0.0
        row = self._book_external(pos, sold_qty=sold, price=book_px, fee_eur=fee)
        return float(row.get("net_eur") or 0.0)

    async def reconcile_external_inventory(self) -> list[dict[str, Any]]:
        """Drop clip lots that left Bitvavo outside the desk. No sell orders.

        Uses total held minus 15m reserved qty so a UI flatten cannot leave a
        ghost bag on the dashboard, and 15m coins are not imported as clip.
        """
        closed: list[dict[str, Any]] = []
        if self.dry_run or not self._gws or not self.positions:
            return closed
        async with self._decide_lock:
            for pos in list(self.positions):
                if pos.is_paper():
                    continue
                held = await self._held_base(pos.base)
                if held is None:
                    continue
                mark = float(self.marks.get(pos.base) or pos.entry_price or 0.0)
                avail = max(0.0, float(held) - self._other_desk_qty(pos.base))
                book_qty = self._pos_qty(pos)
                sold = book_qty - avail
                if sold <= 0 or sold * max(mark, 1e-12) < _MIN_ORDER_EUR:
                    continue
                vwap = self._vwap_for_sold(
                    await self._recent_sells(pos.base, since_ms=int(pos.opened_ms or 0)),
                    sold,
                )
                if vwap is not None:
                    px, fee = vwap
                else:
                    px, fee = (mark if mark > 0 else float(pos.entry_price)), 0.0
                closed.append(
                    self._book_external(pos, sold_qty=sold, price=px, fee_eur=fee)
                )
            if closed:
                self._sample_equity()
                self._save_state()
        return closed

    async def _venue_quote_eur(self) -> float | None:
        gw = self._primary_gw()
        if self.dry_run or gw is None or not hasattr(gw, "quote_balance_eur"):
            return None
        try:
            raw = await gw.quote_balance_eur()
        except Exception as exc:  # noqa: BLE001
            logger.warning("clip quote balance failed: %s", exc)
            return None
        if raw is None:
            return None
        return max(0.0, float(raw))

    async def _decision_cash(self) -> float:
        cash = float(self.cash_eur)
        venue_eur = await self._venue_quote_eur()
        if venue_eur is None:
            return cash
        left = max(0.0, venue_eur - self._reserved_quote_eur)
        return min(cash, left)

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
            return _Fill(qty=q, avg_price=px, fee_eur=q * px * (self.cfg.fee_rt / 2))
        try:
            bid, ask = await gw.best_bid_ask(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("clip book %s failed: %s", symbol, exc)
            return None
        price = (
            float(ask) * (1.0 + _TAKER_CROSS)
            if side == "buy"
            else float(bid) * (1.0 - _TAKER_CROSS)
        )
        if price <= 0:
            return None
        q = float(qty) if qty is not None else float(notional_eur or 0.0) / price
        if q * price < _MIN_ORDER_EUR:
            return None
        try:
            state = await gw.place_limit(symbol, side, q, price, post_only=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("clip %s %s rejected: %s", side, symbol, exc)
            return None
        deadline = time.time() + 20.0
        while getattr(state, "status", "") == "open" and time.time() < deadline:
            await asyncio.sleep(1.0)
            try:
                state = await gw.fetch_order(state.order_id, symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("clip fetch_order %s: %s", symbol, exc)
                break
        if getattr(state, "status", "") == "open":
            with suppress(Exception):
                await gw.cancel_order(state.order_id, symbol)
            await asyncio.sleep(0.4)
            with suppress(Exception):
                state = await gw.fetch_order(state.order_id, symbol)
        filled = float(getattr(state, "filled_qty", 0.0) or 0.0)
        avg = getattr(state, "avg_price", None)
        if filled <= 0 or not avg:
            return None
        fee = float(getattr(state, "fee_eur", 0.0) or 0.0)
        return _Fill(qty=filled, avg_price=float(avg), fee_eur=fee)

    async def _sellable_qty(self, pos: ClipPosition) -> float:
        qty = float(pos.qty or 0.0)
        if qty <= 0 and pos.entry_price > 0:
            qty = pos.notional_eur / pos.entry_price
        reserved = self._other_desk_qty(pos.base)
        gw = self._primary_gw()
        if self.dry_run or gw is None or pos.is_paper():
            return max(0.0, qty)
        held: float | None = None
        try:
            if hasattr(gw, "base_free"):
                held = await gw.base_free(pos.base)
            if (held is None or held <= 0) and hasattr(gw, "base_held"):
                held = await gw.base_held(pos.base)
        except Exception as exc:  # noqa: BLE001
            logger.warning("clip balance %s failed: %s", pos.base, exc)
            held = None
        if held is None:
            return max(0.0, qty)
        return max(0.0, min(qty, float(held) - reserved))

    async def _close_lot(self, pos: ClipPosition, px: float, reason: str) -> float | None:
        qty = float(pos.qty or 0.0)
        if qty <= 0 and pos.entry_price > 0:
            qty = pos.notional_eur / pos.entry_price
        fill: _Fill | None
        if pos.is_paper() or self.dry_run:
            mark = px if px > 0 else (self.marks.get(pos.base) or pos.entry_price)
            fill = _Fill(
                qty=qty, avg_price=float(mark), fee_eur=pos.notional_eur * (self.cfg.fee_rt / 2)
            )
        else:
            sell_qty = await self._sellable_qty(pos)
            if sell_qty * (px or pos.entry_price) < _MIN_ORDER_EUR:
                booked = await self._book_if_already_sold(pos, px)
                if booked is not None:
                    return booked
                logger.warning("clip live sell skipped %s — would take 15m qty", pos.base)
                return None
            fill = await self._fill(pos.base, "sell", qty=sell_qty)
            if fill is None:
                booked = await self._book_if_already_sold(pos, px)
                if booked is not None:
                    return booked
                logger.warning("clip live sell failed %s — keeping lot", pos.base)
                return None
        mark = fill.avg_price
        ret = mark / pos.entry_price - 1.0 if pos.entry_price > 0 else 0.0
        net = pos.notional_eur * ret - fill.fee_eur
        self.cash_eur += pos.notional_eur + net
        self.realized_total_eur += net
        self.day_realized_eur += net
        self.positions = [p for p in self.positions if p is not pos]
        self._ledger_append(
            {
                "event": "exit",
                "desk": self._desk(),
                "base": pos.base,
                "role": pos.role,
                "venue": pos.venue,
                "dry_run": self.dry_run,
                "notional_eur": round(pos.notional_eur, 2),
                "quantity": fill.qty,
                "entry_price": pos.entry_price,
                "exit_price": mark,
                "net_eur": round(net, 2),
                "fee_eur": round(fill.fee_eur, 4),
                "reason": reason,
                "holding_id": pos.holding_id,
            }
        )
        return net

    async def _trim_lot(
        self, pos: ClipPosition, sell_notional: float, reason: str
    ) -> float | None:
        """Sell part of a lot. Does not flatten the remainder."""
        if pos.notional_eur <= 0 or pos.qty <= 0:
            return None
        want = min(float(sell_notional), pos.notional_eur * 0.95)
        if want < self.cfg.min_notional_eur:
            return None
        px = float(self.marks.get(pos.base) or pos.entry_price or 0.0)
        qty = pos.qty * (want / pos.notional_eur)
        if qty <= 0:
            return None
        fill: _Fill | None
        if pos.is_paper() or self.dry_run:
            mark = px if px > 0 else pos.entry_price
            fill = _Fill(
                qty=qty, avg_price=float(mark), fee_eur=want * (self.cfg.fee_rt / 2)
            )
        else:
            sell_qty = min(qty, await self._sellable_qty(pos))
            if sell_qty * (px or pos.entry_price) < _MIN_ORDER_EUR:
                return None
            fill = await self._fill(pos.base, "sell", qty=sell_qty)
            if fill is None:
                return None
        frac = fill.qty / pos.qty if pos.qty > 0 else 0.0
        sold_notional = pos.notional_eur * frac
        mark = fill.avg_price
        ret = mark / pos.entry_price - 1.0 if pos.entry_price > 0 else 0.0
        net = sold_notional * ret - fill.fee_eur
        self.cash_eur += sold_notional + net
        self.realized_total_eur += net
        self.day_realized_eur += net
        pos.qty = max(0.0, pos.qty - fill.qty)
        pos.notional_eur = max(0.0, pos.notional_eur - sold_notional)
        self._ledger_append(
            {
                "event": "trim",
                "desk": self._desk(),
                "base": pos.base,
                "role": pos.role,
                "venue": pos.venue,
                "dry_run": self.dry_run,
                "notional_eur": round(sold_notional, 2),
                "quantity": fill.qty,
                "entry_price": pos.entry_price,
                "exit_price": mark,
                "net_eur": round(net, 2),
                "fee_eur": round(fill.fee_eur, 4),
                "reason": reason,
                "holding_id": pos.holding_id,
            }
        )
        return net

    async def manage_alt_trail(self) -> list[dict[str, Any]]:
        """Intraday 10% trail on the alt sleeve only. Seeds peak from live mark."""
        trail = float(self.cfg.alt_trail_pct or 0.0)
        if trail <= 0:
            return []
        applied: list[dict[str, Any]] = []
        for pos in list(self.positions):
            if pos.role != "alt":
                continue
            mark = float(self.marks.get(pos.base) or 0.0)
            if mark <= 0:
                continue
            peak = float(pos.peak_px or 0.0)
            if peak <= 0:
                pos.peak_px = mark
                continue
            if mark > peak:
                pos.peak_px = mark
                continue
            if mark > peak * (1.0 - trail):
                continue
            net = await self._close_lot(pos, mark, "alt_trail")
            if net is not None:
                applied.append(
                    {
                        "action": "exit",
                        "base": pos.base,
                        "net_eur": round(net, 2),
                        "reason": "alt_trail",
                    }
                )
                self.last_rebalance_ms = int(time.time() * 1000)
        if applied:
            self._save_state()
        return applied

    async def _open_lot(
        self, base: str, notional: float, px: float, role: str, reasons: list[str]
    ) -> ClipPosition | None:
        if notional < self.cfg.min_notional_eur:
            return None
        now_ms = int(time.time() * 1000)
        if self.dry_run or not self._gws:
            if px <= 0:
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
            pos = ClipPosition(
                base=base,
                entry_price=px,
                notional_eur=notional,
                qty=notional / px,
                opened_ms=now_ms,
                role=role,
                venue="paper",
                entry_reason=",".join(reasons),
                peak_px=px,
            )
            self.positions.append(pos)
            self._ledger_append(
                {
                    "event": "entry",
                    "desk": self._desk(),
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
        fill = await self._fill(base, "buy", notional_eur=notional)
        if fill is None or fill.qty <= 0:
            logger.warning("clip buy skipped %s (no fill)", base)
            return None
        cost = fill.notional + fill.fee_eur
        if cost > self.cash_eur + 1.0:
            logger.warning("clip buy over cash %s cost=%.2f cash=%.2f", base, cost, self.cash_eur)
        self.cash_eur = max(0.0, self.cash_eur - cost)
        venue = self._primary_venue()
        pos = ClipPosition(
            base=base,
            entry_price=fill.avg_price,
            notional_eur=fill.notional,
            qty=fill.qty,
            opened_ms=now_ms,
            role=role,
            venue=venue,
            entry_reason=",".join(reasons),
            peak_px=fill.avg_price,
        )
        self.positions.append(pos)
        self.marks[base] = fill.avg_price
        self.mark_ts[base] = time.time()
        self._ledger_append(
            {
                "event": "entry",
                "desk": self._desk(),
                "base": base,
                "role": role,
                "venue": venue,
                "dry_run": False,
                "notional_eur": round(fill.notional, 2),
                "quantity": fill.qty,
                "entry_price": fill.avg_price,
                "fee_eur": round(fill.fee_eur, 4),
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
            sleeves = {
                "btc": sum(p.notional_eur for p in self.positions if p.role == "btc"),
                "alt": sum(p.notional_eur for p in self.positions if p.role == "alt"),
            }
            cash = await self._decision_cash() if execute else self.cash_eur
            decision = evaluate_clip(
                ohlc,
                self.cfg,
                held=held,
                cash_eur=cash,
                deployed_eur=self._deployed(),
                now_ms=int(now.timestamp() * 1000),
                last_rebalance_ms=self.last_rebalance_ms,
                now=now,
                sleeve_eur=sleeves,
            )
            applied: list[dict[str, Any]] = []
            if execute and decision.get("ok"):
                for ex in decision.get("exits") or []:
                    pos = next((p for p in self.positions if p.base == ex["base"]), None)
                    close = self._last_close(ohlc, ex["base"])
                    if pos and close:
                        px = fill_px(close, "sell", slip=self.cfg.slip)
                        net = await self._close_lot(pos, px, str(ex.get("reason") or "exit"))
                        if net is not None:
                            applied.append(
                                {"action": "exit", "base": ex["base"], "net_eur": round(net, 2)}
                            )
                for row in decision.get("trims") or []:
                    pos = next((p for p in self.positions if p.base == row["base"]), None)
                    if pos is None:
                        continue
                    net = await self._trim_lot(
                        pos, float(row["sell_notional_eur"]), str(row.get("reason") or "trim")
                    )
                    if net is not None:
                        applied.append(
                            {
                                "action": "trim",
                                "base": row["base"],
                                "net_eur": round(net, 2),
                                "sold_eur": float(row["sell_notional_eur"]),
                            }
                        )
                for row in decision.get("entries") or []:
                    close = self._last_close(ohlc, row["base"])
                    if not close:
                        continue
                    px = fill_px(close, "buy", slip=self.cfg.slip)
                    want = float(row["notional_eur"])
                    left = await self._decision_cash()
                    want = min(want, left)
                    pos = await self._open_lot(
                        str(row["base"]),
                        want,
                        px,
                        str(row.get("role") or "btc"),
                        list(row.get("reasons") or []),
                    )
                    if pos:
                        applied.append(
                            {"action": "entry", "base": pos.base, "notional_eur": pos.notional_eur}
                        )
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

    async def sell(self, holding_id: str) -> dict[str, Any]:
        hid = str(holding_id or "").strip()
        if not hid:
            return {"ok": False, "reason": "missing_holding_id"}
        async with self._decide_lock:
            pos = next((p for p in self.positions if p.holding_id == hid), None)
            if pos is None:
                return {"ok": False, "reason": "not_found"}
            px = float(self.marks.get(pos.base) or pos.entry_price or 0.0)
            net = await self._close_lot(pos, px, "manual_sell")
            if net is None:
                return {"ok": False, "reason": "sell_failed", "base": pos.base}
            self._save_state()
            return {
                "ok": True,
                "holding_id": hid,
                "base": pos.base,
                "net_eur": round(net, 2),
            }

    async def sell_all(self) -> dict[str, Any]:
        async with self._decide_lock:
            closed: list[str] = []
            failed: list[str] = []
            for pos in list(self.positions):
                px = float(self.marks.get(pos.base) or pos.entry_price or 0.0)
                net = await self._close_lot(pos, px, "manual_sell_all")
                if net is None:
                    failed.append(pos.base)
                else:
                    closed.append(pos.base)
            if closed:
                self.last_rebalance_ms = 0
            self._save_state()
            return {
                "ok": not failed,
                "closed": len(closed),
                "bases": closed,
                "failed": failed,
            }

    def next_decision(self) -> str:
        now = datetime.now(UTC)
        hours = self.cfg.decision_hours_utc or (0,)
        for h in hours:
            cand = now.replace(hour=int(h), minute=5, second=0, microsecond=0)
            if cand > now:
                return cand.isoformat()
        nxt = (now + timedelta(days=1)).replace(
            hour=int(hours[0]), minute=5, second=0, microsecond=0
        )
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
                    "mark_age_sec": (now - self.mark_ts[p.base])
                    if p.base in self.mark_ts
                    else None,
                    "gross_return": p.gross_return(mark),
                    "unrealized_net_eur": round(p.unrealized_net(mark, self.cfg.fee_rt), 2),
                    "age_h": round(age_h, 2),
                    "side": "long",
                    "quantity": p.qty,
                }
            )
        equity = self._equity_now()
        last = self.last_decision or {}
        live = not self.dry_run
        return {
            "desk": self._desk(),
            "mode": "btc_rs_clip_live" if live else "btc_rs_clip_paper",
            "paper_only": not live,
            "allow_live": live,
            "dry_run": self.dry_run,
            "venues": list(self.venues),
            "reserved_quote_eur": round(self._reserved_quote_eur, 2),
            "book_eur": self.cfg.book_eur,
            "cash_eur": round(self.cash_eur, 2),
            "deployed_eur": round(self._deployed(), 2),
            "equity_eur": round(equity, 2),
            "unrealized_net_eur": round(self._unrealized(), 2),
            "realized_total_eur": round(self.realized_total_eur, 2),
            "day_realized_eur": round(self.day_realized_eur, 2),
            "equity_curve": [
                [round(float(t), 1), round(float(eq), 2)] for t, eq in self.equity_curve
            ],
            "positions": positions,
            "last_decision": last,
            "live_caption": str(last.get("caption") or ""),
            "risk_on": bool(last.get("risk_on")),
            "sma50": last.get("sma50"),
            "btc": self.marks.get("BTC") or last.get("btc"),
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
                "trail_pct": self.cfg.alt_trail_pct,
                "hard_stop_pct": 0.0,
            },
        }

    async def run(self, should_stop) -> None:  # noqa: ANN001
        last_hour_fire: set[str] = set()
        hours = self.cfg.decision_hours_utc or (0,)
        try:
            await self.reconcile_external_inventory()
        except Exception:  # noqa: BLE001
            logger.exception("clip kick reconcile failed")
        now = datetime.now(UTC)
        in_window = now.hour in hours and now.minute < 8
        # Empty-book kick would rebuy immediately; only fill inside the daily window.
        try:
            await self.decide(execute=in_window)
        except Exception:  # noqa: BLE001
            logger.exception("clip kick decide failed")
        if in_window:
            last_hour_fire.add(f"{now.date()}-{now.hour}")
        while not should_stop():
            try:
                await self._refresh_marks()
                try:
                    await self.reconcile_external_inventory()
                except Exception:  # noqa: BLE001
                    logger.exception("clip: external reconcile failed")
                async with self._decide_lock:
                    await self.manage_alt_trail()
                now = datetime.now(UTC)
                key = f"{now.date()}-{now.hour}"
                if now.hour in hours and now.minute < 8 and key not in last_hour_fire:
                    await self.decide(execute=True)
                    last_hour_fire.add(key)
                if len(last_hour_fire) > 48:
                    last_hour_fire = {key}
            except Exception:  # noqa: BLE001
                logger.exception("clip tick failed")
            await asyncio.sleep(float(self.cfg.tick_sec))
        self._save_state()


class BtcRsClipDeskManager:
    """Start/stop singleton — paper unless allow_live arms Bitvavo fills."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._runner: BtcRsClipPaperRunner | None = None
        self._stop = False
        self._engine: Any = None
        self._last_reconcile_mono = 0.0

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        enabled = bool(getattr(settings, "momentum_btc_rs_clip_enabled", False))
        allow_live = bool(getattr(settings, "momentum_btc_rs_clip_allow_live", False))
        base: dict[str, Any] = {
            "running": self.running(),
            "enabled_setting": enabled,
            "desk": "btc_rs_clip" if allow_live else "btc_rs_clip_paper",
            "mode": "btc_rs_clip_live" if allow_live else "btc_rs_clip_paper",
            "dry_run": not allow_live,
            "paper_only": not allow_live,
            "allow_live": allow_live,
        }
        if self._runner is not None:
            base.update(self._runner.status())
            base["allow_live"] = allow_live and not self._runner.dry_run
            base["paper_only"] = self._runner.dry_run
            return base
        if enabled:
            cfg = config_from_settings(settings)
            state_path = str(
                getattr(
                    settings,
                    "momentum_btc_rs_clip_state_path",
                    "./data/momentum_btc_rs_clip_state.json",
                )
            )
            cash = cfg.book_eur
            realized = 0.0
            positions: list[dict[str, Any]] = []
            last: dict[str, Any] = {}
            curve: list[list[float]] = []
            try:
                raw = json.loads(Path(state_path).read_text(encoding="utf-8"))
                cash = float(raw.get("cash_eur", cash))
                realized = float(raw.get("realized_total_eur") or 0.0)
                positions = list(raw.get("positions") or [])
                last = dict(raw.get("last_decision") or {})
                for row in raw.get("equity_curve") or []:
                    if not isinstance(row, (list, tuple)) or len(row) < 2:
                        continue
                    try:
                        curve.append([float(row[0]), float(row[1])])
                    except (TypeError, ValueError):
                        continue
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
                    "equity_curve": curve[-_EQUITY_CURVE_MAX:],
                    "last_decision": last,
                    "live_caption": str(
                        last.get("caption") or "BTC+RS clip staat klaar (niet gestart)."
                    ),
                    "config": {
                        "btc_frac": cfg.btc_frac,
                        "alt_frac": cfg.alt_frac,
                        "book_eur": cfg.book_eur,
                        "sma_n": cfg.sma_n,
                        "trail_pct": cfg.alt_trail_pct,
                        "excess_floor": cfg.excess_floor,
                        "lookback_days": cfg.lookback_days,
                    },
                }
            )
        return base

    async def refresh_live(self) -> dict[str, Any]:
        """Fresh marks every poll; venue reconcile is throttled so 1s UI stays light."""
        if self._runner is not None:
            try:
                await self._runner._refresh_marks()
            except Exception:  # noqa: BLE001
                logger.exception("clip: mark refresh for status failed")
            now = time.monotonic()
            if now - self._last_reconcile_mono >= 5.0:
                self._last_reconcile_mono = now
                try:
                    await self._runner.reconcile_external_inventory()
                except Exception:  # noqa: BLE001
                    logger.exception("clip: reconcile for status failed")
        return self.status()

    async def start(self, *, settings: Settings | None = None) -> dict[str, Any]:
        settings = settings or get_settings()
        allow_live = bool(getattr(settings, "momentum_btc_rs_clip_allow_live", False))
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
        if not bool(getattr(settings, "momentum_btc_rs_clip_enabled", False)):
            return {
                "ok": False,
                "started": False,
                "reason": "momentum_btc_rs_clip_enabled_false",
                "hint": "Set MOMENTUM_BTC_RS_CLIP_ENABLED=true",
            }
        cfg = config_from_settings(settings)
        state_path = str(
            getattr(
                settings,
                "momentum_btc_rs_clip_state_path",
                "./data/momentum_btc_rs_clip_state.json",
            )
        )
        ledger_path = str(
            getattr(
                settings,
                "momentum_btc_rs_clip_ledger_path",
                "./data/momentum_btc_rs_clip_ledger.jsonl",
            )
        )
        venues = parse_venues(
            str(getattr(settings, "momentum_btc_rs_clip_venues", "bitvavo") or "bitvavo")
        )
        gateways: dict[str, Any] = {}
        dry_run = not allow_live
        reserved_quote = reserved_quote_eur_from_settings(settings)
        reserved_qty = load_side_desk_reserved_qty(settings, venues[0] if venues else "bitvavo")
        if allow_live:
            from bot.live.micro_engine import LiveMicroEngine
            from bot.live.momentum_desk import DeskConfig

            book = float(cfg.book_eur)
            desk_cfg = DeskConfig(
                clip_eur=max(book, 500.0),
                max_positions=2,
                day_loss_limit_eur=max(book * 0.15, 2_000.0),
            )
            engine = LiveMicroEngine(engine_settings_for_desk(settings, desk_cfg, venues))
            armed = engine.arm()
            if not armed.get("armed"):
                logger.error("clip live arm failed: %s", armed)
                return {"ok": False, "started": False, "reason": "arm_failed", "detail": armed}
            for v in venues:
                if engine._registry.get_client(v, enable_trading=True) is None:  # noqa: SLF001
                    logger.warning("clip: no trading credentials for %s; skipped", v)
                    continue
                gateways[v] = LiveGateway(engine, v)
            if not gateways:
                return {
                    "ok": False,
                    "started": False,
                    "reason": "no_venue_credentials",
                    "venues": list(venues),
                }
            venues = tuple(v for v in venues if v in gateways)
            dry_run = False
            self._engine = engine
        self._runner = BtcRsClipPaperRunner(
            cfg,
            state_path=state_path,
            ledger_path=ledger_path,
            dry_run=dry_run,
            venues=venues,
            gateways=gateways,
            reserved_quote_eur=reserved_quote,
            reserved_qty=reserved_qty,
        )
        if not dry_run:
            dropped = self._runner.discard_paper_positions()
            logger.info("clip live: dropped %s paper lots before venue orders", dropped)
        self._stop = False
        self._task = asyncio.create_task(
            self._runner.run(lambda: self._stop), name="momentum-btc-rs-clip"
        )
        _write_flag(
            state_path,
            running=True,
            dry_run=dry_run,
            paper_only=dry_run,
            allow_live=not dry_run,
        )
        return {
            "ok": True,
            "started": True,
            "paper_only": dry_run,
            "allow_live": not dry_run,
            "dry_run": dry_run,
            "venues": list(venues),
            "status": self.status(),
        }

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
                "momentum_btc_rs_clip_state_path",
                "./data/momentum_btc_rs_clip_state.json",
            )
        )
        dry = True if self._runner is None else self._runner.dry_run
        _write_flag(state_path, running=False, dry_run=dry, paper_only=dry, allow_live=not dry)
        self._task = None
        return {"ok": True, "stopped": True, "status": self.status()}

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        if self._runner is None:
            settings = get_settings()
            cfg = config_from_settings(settings)
            runner = BtcRsClipPaperRunner(
                cfg,
                state_path=str(
                    getattr(
                        settings,
                        "momentum_btc_rs_clip_state_path",
                        "./data/momentum_btc_rs_clip_state.json",
                    )
                ),
                ledger_path=str(
                    getattr(
                        settings,
                        "momentum_btc_rs_clip_ledger_path",
                        "./data/momentum_btc_rs_clip_ledger.jsonl",
                    )
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

    async def reconcile(self) -> dict[str, Any]:
        if self._runner is None:
            return {"ok": False, "reason": "not_running"}
        closed = await self._runner.reconcile_external_inventory()
        return {
            "ok": True,
            "closed": len(closed),
            "rows": closed,
            "status": self.status(),
        }

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

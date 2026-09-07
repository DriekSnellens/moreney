"""Live runner for the Daily Momentum Desk.

Executes ``bot.live.momentum_desk`` decisions against one venue through the
existing fail-closed ``LiveMicroEngine`` (policy gates + audit). Design:

* 20s tick. Exits are evaluated once per closed 15m bar; entries once per
  configured decision hour (UTC).
* Buys: post-only maker at the bid, re-pegged for up to ``buy_rest_sec``,
  then one taker fallback. Trail / time exits: maker at the ask for
  ``sell_rest_sec`` then taker. Hard stops: taker immediately.
* Every fill is appended to a JSONL ledger with entry/exit reason codes;
  state (positions, risk ledger) is persisted so restarts are safe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from bot.core.config import Settings, get_settings
from bot.live.momentum_desk import (
    BAR_MS,
    AlphaIView,
    Candle,
    DeskConfig,
    ExitDecision,
    Position,
    RiskLedger,
    bar_stats,
    classify_regime,
    evaluate_exit,
    rank_candidates,
    select_entries,
    universe_stats,
)

logger = logging.getLogger(__name__)

BITVAVO_PUBLIC = "https://api.bitvavo.com/v2"
_MIN_ORDER_EUR = 5.0
_DISASTER_STOP_MULT = 2.0


# ---------------------------------------------------------------------------
# Gateway abstraction (live implementation + test fakes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderState:
    order_id: str
    status: str  # open | closed | canceled | rejected
    filled_qty: float
    avg_price: float | None
    fee_eur: float


class Gateway(Protocol):
    async def best_bid_ask(self, symbol: str) -> tuple[float, float]: ...

    async def place_limit(
        self, symbol: str, side: str, qty: float, price: float, *, post_only: bool
    ) -> OrderState: ...

    async def fetch_order(self, order_id: str, symbol: str) -> OrderState: ...

    async def cancel_order(self, order_id: str, symbol: str) -> OrderState: ...


class LiveGateway:
    """Routes through LiveMicroEngine (policy + audit) and the venue client."""

    def __init__(self, engine: Any, venue: str) -> None:
        self._engine = engine
        self._venue = venue

    def _client(self, *, trading: bool) -> Any:
        client = self._engine._registry.get_client(self._venue, enable_trading=trading)  # noqa: SLF001
        if client is None:
            raise RuntimeError(f"no credentials for venue {self._venue}")
        return client

    async def best_bid_ask(self, symbol: str) -> tuple[float, float]:
        book = await self._client(trading=False).fetch_order_book(symbol, limit=5)
        if not book.bids or not book.asks:
            raise RuntimeError(f"empty book for {symbol}")
        return float(book.bids[0].price), float(book.asks[0].price)

    async def place_limit(
        self, symbol: str, side: str, qty: float, price: float, *, post_only: bool
    ) -> OrderState:
        res = await self._engine.submit(
            {
                "venue": self._venue,
                "symbol": symbol,
                "side": side,
                "quantity": f"{qty:.8f}",
                "limit_price": f"{price:.8f}",
                "post_only": post_only,
                "local_open_orders": 0,
            },
            confirm=True,
        )
        if not res.get("submitted"):
            raise RuntimeError(f"order rejected: {res.get('reason')}: {res.get('message')}")
        row = res.get("order") or {}
        oid = row.get("exchange_order_id") or row.get("order_id")
        if not oid:
            raise RuntimeError("order accepted without exchange id")
        return OrderState(
            order_id=str(oid),
            status=_norm_status(row.get("status")),
            filled_qty=float(row.get("filled_quantity") or 0.0),
            avg_price=float(row["average_price"]) if row.get("average_price") else None,
            fee_eur=0.0,
        )

    async def fetch_order(self, order_id: str, symbol: str) -> OrderState:
        order = await self._client(trading=False).fetch_order(order_id, symbol)
        return _from_exchange_order(order)

    async def cancel_order(self, order_id: str, symbol: str) -> OrderState:
        order = await self._client(trading=True).cancel_order(order_id, symbol)
        return _from_exchange_order(order)

    async def quote_balance_eur(self) -> float | None:
        snap = await self._client(trading=False).get_balances()
        for bal in snap.balances:
            if str(bal.asset).upper() == "EUR":
                return float(bal.total)
        return 0.0


def _norm_status(raw: Any) -> str:
    text = str(getattr(raw, "value", raw) or "").lower()
    if text in {"filled", "closed"}:
        return "closed"
    if text in {"canceled", "cancelled"}:
        return "canceled"
    if text in {"rejected", "expired", "failed"}:
        return "rejected"
    return "open"


def _from_exchange_order(order: Any) -> OrderState:
    fee = float(order.fee_cost or 0.0)
    ccy = str(order.fee_currency or "EUR").upper()
    avg = float(order.average_price) if order.average_price else None
    if ccy != "EUR" and avg:
        fee = fee * avg
    return OrderState(
        order_id=str(order.id),
        status=_norm_status(order.status),
        filled_qty=float(order.filled_quantity or 0.0),
        avg_price=avg,
        fee_eur=fee,
    )


# ---------------------------------------------------------------------------
# Candle feed
# ---------------------------------------------------------------------------


class CandleFeed:
    """Bitvavo public 15m candles with a tiny per-call TTL cache."""

    def __init__(self, base_url: str = BITVAVO_PUBLIC, ttl_sec: float = 10.0) -> None:
        self._base_url = base_url
        self._ttl = ttl_sec
        self._cache: dict[tuple[str, int], tuple[float, list[list[float]]]] = {}

    async def candles(self, base: str, limit: int) -> list[list[float]]:
        key = (base, limit)
        hit = self._cache.get(key)
        now = time.time()
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        url = f"{self._base_url}/{base}-EUR/candles"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params={"interval": "15m", "limit": limit})
            resp.raise_for_status()
            rows = resp.json()
        out = sorted(
            (
                [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
                for r in rows
            ),
            key=lambda r: r[0],
        )
        self._cache[key] = (now, out)
        return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class Holding:
    pos: Position
    holding_id: str
    last_bar_ms: int = 0
    exiting: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pos": asdict(self.pos),
            "holding_id": self.holding_id,
            "last_bar_ms": self.last_bar_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Holding:
        return cls(
            pos=Position(**data["pos"]),
            holding_id=str(data.get("holding_id") or uuid.uuid4().hex[:10]),
            last_bar_ms=int(data.get("last_bar_ms") or 0),
        )


@dataclass
class Fill:
    qty: float
    avg_price: float
    fee_eur: float
    taker: bool

    @property
    def notional(self) -> float:
        return self.qty * self.avg_price


@dataclass
class RunnerOptions:
    venue: str = "bitvavo"
    tick_sec: float = 20.0
    buy_rest_sec: float = 90.0
    sell_rest_sec: float = 60.0
    repeg_sec: float = 20.0
    poll_sec: float = 3.0
    taker_cross_bps: float = 20.0
    decision_grace_sec: float = 30.0
    bar_close_grace_sec: float = 15.0
    state_path: str = "./data/momentum_desk_state.json"
    ledger_path: str = "./data/momentum_desk_ledger.jsonl"
    alphai_recommendations_path: str | None = "./data/alphai/daily_recommendations.json"
    dry_run: bool = False


class MomentumDeskRunner:
    def __init__(
        self,
        cfg: DeskConfig,
        gateway: Gateway | None,
        *,
        options: RunnerOptions | None = None,
        feed: CandleFeed | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.cfg = cfg
        self.opt = options or RunnerOptions()
        self._gw = gateway
        self._feed = feed or CandleFeed()
        self._clock = clock
        self._sleep = sleep
        self.holdings: list[Holding] = []
        self.ledger = RiskLedger.from_dict(cfg, None)
        self.last_decision_hour_ms = 0
        self.realized_total_eur = 0.0
        self.trade_count = 0
        self.last_regime: dict[str, Any] = {}
        self.last_error: str | None = None
        self.marks: dict[str, float] = {}
        self.cash_eur: float | None = None
        self._cash_ts: float = 0.0
        self.started_at = datetime.now(UTC).isoformat()
        self._load_state()

    # ----------------------------------------------------------------- state

    def _load_state(self) -> None:
        path = Path(self.opt.state_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("momentum desk: state unreadable, starting flat")
            return
        self.holdings = [Holding.from_dict(h) for h in data.get("holdings") or []]
        self.ledger = RiskLedger.from_dict(self.cfg, data.get("risk"))
        self.last_decision_hour_ms = int(data.get("last_decision_hour_ms") or 0)
        self.realized_total_eur = float(data.get("realized_total_eur") or 0.0)
        self.trade_count = int(data.get("trade_count") or 0)
        self.last_regime = dict(data.get("last_regime") or {})

    def _save_state(self) -> None:
        path = Path(self.opt.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "holdings": [h.to_dict() for h in self.holdings],
            "risk": self.ledger.to_dict(),
            "last_decision_hour_ms": self.last_decision_hour_ms,
            "realized_total_eur": round(self.realized_total_eur, 4),
            "trade_count": self.trade_count,
            "last_regime": self.last_regime,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(path)

    def _ledger_append(self, row: Mapping[str, Any]) -> None:
        path = Path(self.opt.ledger_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(UTC).isoformat(), **row}) + "\n")

    def status(self) -> dict[str, Any]:
        now_ms = int(self._clock() * 1000)
        positions = []
        unrealized = 0.0
        for h in self.holdings:
            mark = self.marks.get(h.pos.base)
            gross = h.pos.gross_return(mark) if mark else None
            net = None
            if mark:
                net = (
                    h.pos.quantity * (mark - h.pos.entry_price)
                    - h.pos.entry_fee_eur
                    - (h.pos.quantity * mark * self.cfg.fee_rt / 2)
                )
                unrealized += net
            positions.append(
                {
                    "base": h.pos.base,
                    "entry_price": h.pos.entry_price,
                    "quantity": h.pos.quantity,
                    "notional_eur": round(h.pos.notional_eur, 2),
                    "opened": datetime.fromtimestamp(h.pos.opened_ms / 1000, UTC).isoformat(),
                    "age_h": round((now_ms - h.pos.opened_ms) / 3_600_000, 2),
                    "peak_return": round(h.pos.peak / h.pos.entry_price - 1, 4),
                    "mark": mark,
                    "gross_return": round(gross, 4) if gross is not None else None,
                    "unrealized_net_eur": round(net, 2) if net is not None else None,
                    "entry_reason": h.pos.entry_reason,
                    "exiting": h.exiting,
                }
            )
        allowed, why = self.ledger.entries_allowed(now_ms)
        exposure = sum(
            h.pos.quantity * (self.marks.get(h.pos.base) or h.pos.entry_price)
            for h in self.holdings
        )
        equity = (self.cash_eur + exposure) if self.cash_eur is not None else None
        return {
            "desk": "momentum",
            "cash_eur": round(self.cash_eur, 2) if self.cash_eur is not None else None,
            "exposure_eur": round(exposure, 2),
            "equity_eur": round(equity, 2) if equity is not None else None,
            "venue": self.opt.venue,
            "dry_run": self.opt.dry_run,
            "started_at": self.started_at,
            "config": {
                k: (list(v) if isinstance(v, tuple) else v)
                for k, v in self.cfg.__dict__.items()
                if k not in {"clusters", "universe"}
            },
            "positions": positions,
            "unrealized_net_eur": round(unrealized, 2),
            "realized_total_eur": round(self.realized_total_eur, 2),
            "trade_count": self.trade_count,
            "risk": {**self.ledger.to_dict(), "entries_allowed": allowed, "block_reason": why},
            "last_regime": self.last_regime,
            "next_decision": self._next_decision_iso(now_ms),
            "last_error": self.last_error,
        }

    def _next_decision_iso(self, now_ms: int) -> str:
        hour_ms = 3_600_000
        cur = now_ms // hour_ms * hour_ms
        for i in range(0, 25):
            cand = cur + i * hour_ms
            hour = (cand // hour_ms) % 24
            if hour in self.cfg.decision_hours_utc and cand > self.last_decision_hour_ms:
                return datetime.fromtimestamp(cand / 1000, UTC).isoformat()
        return ""

    # ------------------------------------------------------------------ loop

    async def run(self, should_stop: Callable[[], bool]) -> None:
        logger.info("momentum desk runner started (dry_run=%s)", self.opt.dry_run)
        while not should_stop():
            try:
                await self.tick()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("momentum desk tick failed")
            self._save_state()
            await self._sleep(self.opt.tick_sec)

    async def tick(self) -> None:
        now_ms = int(self._clock() * 1000)
        await self._manage_exits(now_ms)
        await self._maybe_decide(now_ms)
        await self._refresh_cash()

    async def _refresh_cash(self) -> None:
        fetch = getattr(self._gw, "quote_balance_eur", None)
        if fetch is None or self._clock() - self._cash_ts < 60.0:
            return
        try:
            self.cash_eur = await fetch()
            self._cash_ts = self._clock()
        except Exception as exc:  # noqa: BLE001
            logger.warning("momentum desk: balance fetch failed: %s", exc)

    # ----------------------------------------------------------------- exits

    async def _manage_exits(self, now_ms: int) -> None:
        if not self.holdings:
            return
        last_closed = (now_ms // BAR_MS) * BAR_MS - BAR_MS
        bar_ready = now_ms >= last_closed + BAR_MS + int(self.opt.bar_close_grace_sec * 1000)
        for h in list(self.holdings):
            if h.exiting:
                continue
            rows = await self._feed.candles(h.pos.base, 4)
            if not rows:
                continue
            live_px = float(rows[-1][4])
            self.marks[h.pos.base] = live_px
            # Disaster stop on the live price — a 15m close is too slow for a crash.
            if live_px <= h.pos.entry_price * (1 - _DISASTER_STOP_MULT * self.cfg.hard_stop_pct):
                await self._exit(
                    h, ExitDecision("disaster_stop", h.pos.gross_return(live_px), True)
                )
                continue
            if not bar_ready or h.last_bar_ms >= last_closed:
                continue
            bar = next((r for r in rows if int(r[0]) == last_closed), None)
            if bar is None:
                continue
            h.last_bar_ms = last_closed
            decision = evaluate_exit(h.pos, bar, self.cfg)
            if decision is not None:
                await self._exit(h, decision)

    async def _exit(self, h: Holding, decision: ExitDecision) -> None:
        h.exiting = True
        try:
            fill = await self._sell(h.pos.base, h.pos.quantity, urgent=decision.urgent)
        finally:
            h.exiting = False
        if fill is None or fill.qty <= 0:
            self._ledger_append(
                {"event": "exit_failed", "base": h.pos.base, "reason": decision.reason}
            )
            return
        net = (
            fill.qty * (fill.avg_price - h.pos.entry_price)
            - h.pos.entry_fee_eur * (fill.qty / h.pos.quantity)
            - fill.fee_eur
        )
        now_ms = int(self._clock() * 1000)
        self.realized_total_eur += net
        self.trade_count += 1
        self.ledger.note_close(net, now_ms)
        self._ledger_append(
            {
                "event": "exit",
                "holding_id": h.holding_id,
                "base": h.pos.base,
                "qty": fill.qty,
                "price": fill.avg_price,
                "notional_eur": round(fill.notional, 2),
                "fee_eur": round(fill.fee_eur, 4),
                "taker": fill.taker,
                "reason": decision.reason,
                "gross_return": round(fill.avg_price / h.pos.entry_price - 1, 5),
                "peak_return": round(h.pos.peak / h.pos.entry_price - 1, 5),
                "hold_h": round((now_ms - h.pos.opened_ms) / 3_600_000, 2),
                "net_eur": round(net, 4),
                "entry_reason": h.pos.entry_reason,
            }
        )
        remaining = h.pos.quantity - fill.qty
        if remaining * fill.avg_price < _MIN_ORDER_EUR:
            self.holdings.remove(h)
        else:
            h.pos.quantity = remaining
            h.pos.notional_eur = remaining * h.pos.entry_price
        self._save_state()

    # --------------------------------------------------------------- entries

    def _decision_hour_due(self, now_ms: int) -> int | None:
        hour_ms = 3_600_000
        cur = now_ms // hour_ms * hour_ms
        hour = (cur // hour_ms) % 24
        if hour not in self.cfg.decision_hours_utc:
            return None
        if cur <= self.last_decision_hour_ms:
            return None
        if now_ms < cur + int(self.opt.decision_grace_sec * 1000):
            return None
        # Do not run a stale decision if the process was down for most of the hour.
        if now_ms > cur + 30 * 60_000:
            self.last_decision_hour_ms = cur
            return None
        return cur

    def _alphai_view(self) -> AlphaIView:
        path = self.opt.alphai_recommendations_path
        if not path:
            return AlphaIView()
        p = Path(path)
        if not p.exists():
            return AlphaIView()
        try:
            return AlphaIView.from_recommendations(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            logger.warning("momentum desk: AlphaI recommendations unreadable; ignoring")
            return AlphaIView()

    async def _maybe_decide(self, now_ms: int) -> None:
        hour_ms = self._decision_hour_due(now_ms)
        if hour_ms is None:
            return
        self.last_decision_hour_ms = hour_ms
        candles: dict[str, Sequence[Candle]] = {}
        for base in ("BTC", *self.cfg.universe):
            try:
                candles[base] = await self._feed.candles(base, 110)
            except Exception as exc:  # noqa: BLE001
                logger.warning("momentum desk: candles failed for %s: %s", base, exc)
        alphai = self._alphai_view()
        stats = universe_stats(candles, hour_ms, self.cfg)
        btc = bar_stats("BTC", candles["BTC"], hour_ms) if candles.get("BTC") else None
        regime = classify_regime(btc, stats, self.cfg, alphai=alphai)
        cands = (
            rank_candidates(stats, regime.btc_ret or 0.0, self.cfg, alphai=alphai)
            if regime.ok
            else []
        )
        allowed, why = self.ledger.entries_allowed(now_ms)
        entries = []
        if allowed:
            entries = select_entries(
                cands,
                regime,
                self.cfg,
                held_bases=[h.pos.base for h in self.holdings],
                blocked_bases=self.ledger.blocked_bases(
                    now_ms, self.cfg.max_entries_per_base_per_day
                ),
                alphai=alphai,
            )
        self.last_regime = {
            "at": datetime.fromtimestamp(hour_ms / 1000, UTC).isoformat(),
            "ok": regime.ok,
            "btc_ret": round(regime.btc_ret, 4) if regime.btc_ret is not None else None,
            "breadth": round(regime.breadth, 3),
            "reasons": list(regime.reasons),
            "candidates": [
                {"base": c.base, "excess": round(c.excess, 4), "from_high": round(c.from_high, 4)}
                for c in cands[:6]
            ],
            "entries": [e.base for e in entries],
            "risk_block": "" if allowed else why,
            "alphai": {
                "macro_caution": alphai.macro_caution,
                "avoid": sorted(alphai.avoid),
                "picks": sorted(alphai.picks),
            },
        }
        self._ledger_append({"event": "decision", **self.last_regime})
        for entry in entries:
            await self._enter(entry.base, entry.clip_eur, ",".join(entry.reasons), now_ms)

    async def _enter(self, base: str, clip_eur: float, reason: str, now_ms: int) -> None:
        fill = await self._buy(base, clip_eur)
        if fill is None or fill.qty <= 0:
            self._ledger_append({"event": "entry_failed", "base": base, "reason": reason})
            return
        pos = Position(
            base=base,
            entry_price=fill.avg_price,
            quantity=fill.qty,
            notional_eur=fill.notional,
            opened_ms=now_ms,
            peak=fill.avg_price,
            entry_fee_eur=fill.fee_eur,
            entry_reason=reason,
        )
        holding = Holding(
            pos=pos, holding_id=uuid.uuid4().hex[:10], last_bar_ms=now_ms // BAR_MS * BAR_MS
        )
        self.holdings.append(holding)
        self.ledger.note_entry(base, now_ms)
        self.marks[base] = fill.avg_price
        self._ledger_append(
            {
                "event": "entry",
                "holding_id": holding.holding_id,
                "base": base,
                "qty": fill.qty,
                "price": fill.avg_price,
                "notional_eur": round(fill.notional, 2),
                "fee_eur": round(fill.fee_eur, 4),
                "taker": fill.taker,
                "reason": reason,
            }
        )
        self._save_state()

    # ------------------------------------------------------------ execution

    async def _buy(self, base: str, notional_eur: float) -> Fill | None:
        return await self._work_order(
            base, "buy", notional_eur=notional_eur, rest_sec=self.opt.buy_rest_sec
        )

    async def _sell(self, base: str, qty: float, *, urgent: bool) -> Fill | None:
        return await self._work_order(
            base, "sell", qty=qty, rest_sec=0.0 if urgent else self.opt.sell_rest_sec
        )

    async def _work_order(
        self,
        base: str,
        side: str,
        *,
        qty: float | None = None,
        notional_eur: float | None = None,
        rest_sec: float,
    ) -> Fill | None:
        symbol = f"{base}EUR"
        if self.opt.dry_run or self._gw is None:
            bid, ask = await self._gw.best_bid_ask(symbol) if self._gw else (0.0, 0.0)
            px = bid if side == "buy" else ask
            if px <= 0:
                rows = await self._feed.candles(base, 2)
                px = float(rows[-1][4])
            q = qty if qty is not None else (notional_eur or 0.0) / px
            return Fill(qty=q, avg_price=px, fee_eur=q * px * self.cfg.fee_rt / 2, taker=False)

        filled_qty = 0.0
        filled_cost = 0.0
        fee_eur = 0.0
        taker_used = False
        remaining_qty = qty
        remaining_notional = notional_eur
        deadline = self._clock() + rest_sec

        async def _settle(state: OrderState) -> None:
            nonlocal filled_qty, filled_cost, fee_eur, remaining_qty, remaining_notional
            if state.filled_qty > 0 and state.avg_price:
                filled_qty += state.filled_qty
                filled_cost += state.filled_qty * state.avg_price
                fee_eur += state.fee_eur
                if remaining_qty is not None:
                    remaining_qty = max(0.0, remaining_qty - state.filled_qty)
                if remaining_notional is not None:
                    remaining_notional = max(
                        0.0, remaining_notional - state.filled_qty * state.avg_price
                    )

        def _done() -> bool:
            if remaining_qty is not None:
                return (
                    remaining_qty * (filled_cost / filled_qty if filled_qty else 1.0)
                    < _MIN_ORDER_EUR
                )
            return (remaining_notional or 0.0) < _MIN_ORDER_EUR

        # Maker phase: rest at the touch, re-peg periodically.
        while rest_sec > 0 and self._clock() < deadline and not _done():
            bid, ask = await self._gw.best_bid_ask(symbol)
            price = bid if side == "buy" else ask
            q = remaining_qty if remaining_qty is not None else (remaining_notional or 0.0) / price
            try:
                state = await self._gw.place_limit(symbol, side, q, price, post_only=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("momentum desk: maker %s %s rejected: %s", side, symbol, exc)
                break
            state = await self._poll(
                state, symbol, min(self.opt.repeg_sec, max(0.0, deadline - self._clock()))
            )
            if state.status == "open":
                state = await self._cancel_and_refetch(state, symbol)
            elif state.status == "closed":
                state = await self._refetch(state, symbol)
            await _settle(state)
            if state.status == "rejected":
                await self._sleep(self.opt.poll_sec)

        # Taker phase: cross the spread once for the remainder.
        if not _done():
            bid, ask = await self._gw.best_bid_ask(symbol)
            cross = self.opt.taker_cross_bps / 10_000
            price = ask * (1 + cross) if side == "buy" else bid * (1 - cross)
            q = remaining_qty if remaining_qty is not None else (remaining_notional or 0.0) / price
            try:
                state = await self._gw.place_limit(symbol, side, q, price, post_only=False)
                taker_used = True
                state = await self._poll(state, symbol, 30.0)
                if state.status == "open":
                    state = await self._cancel_and_refetch(state, symbol)
                elif state.status == "closed":
                    state = await self._refetch(state, symbol)
                await _settle(state)
            except Exception as exc:  # noqa: BLE001
                logger.warning("momentum desk: taker %s %s failed: %s", side, symbol, exc)

        if filled_qty <= 0:
            return None
        return Fill(
            qty=filled_qty, avg_price=filled_cost / filled_qty, fee_eur=fee_eur, taker=taker_used
        )

    async def _refetch(self, state: OrderState, symbol: str) -> OrderState:
        """Authoritative fill/fee figures come from fetch_order, not from the
        create/cancel responses (Bitvavo's cancel reply carries neither)."""
        try:
            return await self._gw.fetch_order(state.order_id, symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("momentum desk: refetch failed %s: %s", state.order_id, exc)
            return state

    async def _cancel_and_refetch(self, state: OrderState, symbol: str) -> OrderState:
        try:
            await self._gw.cancel_order(state.order_id, symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("momentum desk: cancel failed %s: %s", state.order_id, exc)
        await self._sleep(1.0)
        return await self._refetch(state, symbol)

    async def _poll(self, state: OrderState, symbol: str, max_sec: float) -> OrderState:
        end = self._clock() + max_sec
        while state.status == "open" and self._clock() < end:
            await self._sleep(self.opt.poll_sec)
            try:
                state = await self._gw.fetch_order(state.order_id, symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("momentum desk: fetch_order failed %s: %s", state.order_id, exc)
        return state


# ---------------------------------------------------------------------------
# Manager (process singleton for the API)
# ---------------------------------------------------------------------------


def desk_config_from_settings(settings: Settings) -> DeskConfig:
    hours = tuple(
        int(x)
        for x in str(getattr(settings, "momentum_desk_decision_hours_utc", "0")).split(",")
        if x.strip() != ""
    )
    return DeskConfig(
        decision_hours_utc=hours or (0,),
        clip_eur=float(getattr(settings, "momentum_desk_clip_eur", 500.0)),
        max_positions=int(getattr(settings, "momentum_desk_max_positions", 3)),
        trail_pct=float(getattr(settings, "momentum_desk_trail_pct", 0.03)),
        trail_tight_after=float(getattr(settings, "momentum_desk_trail_tight_after", 0.03)),
        trail_tight_pct=float(getattr(settings, "momentum_desk_trail_tight_pct", 0.015)),
        hard_stop_pct=float(getattr(settings, "momentum_desk_hard_stop_pct", 0.03)),
        time_exit_hours=float(getattr(settings, "momentum_desk_time_exit_hours", 48.0)),
        day_loss_limit_eur=float(getattr(settings, "momentum_desk_day_loss_limit_eur", 40.0)),
        week_loss_limit_eur=float(getattr(settings, "momentum_desk_week_loss_limit_eur", 100.0)),
        macro_caution_mode=str(getattr(settings, "momentum_desk_macro_caution_mode", "reduce")),
    )


def engine_settings_for_desk(settings: Settings, cfg: DeskConfig, venue: str) -> Settings:
    """Policy caps sized to the desk so LiveMicroEngine gates stay meaningful."""
    max_clip = cfg.clip_eur * max(1.0, cfg.alphai_clip_mult) + 1.0
    return settings.model_copy(
        update={
            "live_micro_symbols": "*",
            "live_micro_venues": venue,
            "live_micro_max_notional_eur": float(max_clip),
            "live_micro_max_open_orders_per_venue": int(cfg.max_positions + 1),
            "live_micro_max_daily_loss_eur": float(cfg.day_loss_limit_eur),
        }
    )


class MomentumDeskManager:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._runner: MomentumDeskRunner | None = None
        self._stop = False
        self._engine: Any = None

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        base: dict[str, Any] = {"running": self.running()}
        if self._runner is not None:
            base.update(self._runner.status())
        if self._task is not None and self._task.done() and self._task.exception():
            base["task_error"] = repr(self._task.exception())
        return base

    async def start(
        self,
        *,
        settings: Settings | None = None,
        dry_run: bool = False,
        venue: str = "bitvavo",
    ) -> dict[str, Any]:
        if self.running():
            return {"started": False, "reason": "already_running", "status": self.status()}
        settings = settings or get_settings()
        cfg = desk_config_from_settings(settings)
        options = RunnerOptions(
            venue=venue,
            dry_run=dry_run,
            state_path=str(getattr(settings, "momentum_desk_state_path", RunnerOptions.state_path)),
            ledger_path=str(
                getattr(settings, "momentum_desk_ledger_path", RunnerOptions.ledger_path)
            ),
            alphai_recommendations_path=str(
                getattr(settings, "alphai_daily_recommendations_path", None)
                or RunnerOptions.alphai_recommendations_path
            ),
        )
        gateway: Gateway | None = None
        if not dry_run:
            from bot.live.micro_engine import LiveMicroEngine

            engine = LiveMicroEngine(engine_settings_for_desk(settings, cfg, venue))
            armed = engine.arm()
            if not armed.get("armed"):
                return {"started": False, "reason": "arm_failed", "detail": armed}
            self._engine = engine
            gateway = LiveGateway(engine, venue)
        self._runner = MomentumDeskRunner(cfg, gateway, options=options)
        self._stop = False
        self._task = asyncio.create_task(self._runner.run(lambda: self._stop), name="momentum-desk")
        Path(options.state_path).parent.mkdir(parents=True, exist_ok=True)
        _write_flag(options.state_path, running=True, dry_run=dry_run, venue=venue)
        return {"started": True, "status": self.status()}

    async def stop(self) -> dict[str, Any]:
        self._stop = True
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=45.0)
            except TimeoutError:
                task.cancel()
        if self._runner is not None:
            _write_flag(self._runner.opt.state_path, running=False)
        return {"stopped": True, "status": self.status()}

    async def resume_if_flagged(self, settings: Settings | None = None) -> dict[str, Any] | None:
        settings = settings or get_settings()
        state_path = str(getattr(settings, "momentum_desk_state_path", RunnerOptions.state_path))
        flag = _read_flag(state_path)
        if not flag or not flag.get("running"):
            return None
        logger.warning("momentum desk: resuming after restart (dry_run=%s)", flag.get("dry_run"))
        return await self.start(
            settings=settings,
            dry_run=bool(flag.get("dry_run")),
            venue=str(flag.get("venue") or "bitvavo"),
        )


def _flag_path(state_path: str) -> Path:
    return Path(state_path).with_name("momentum_desk_running.json")


def _write_flag(state_path: str, **payload: Any) -> None:
    p = _flag_path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"updated_at": datetime.now(UTC).isoformat(), **payload}), encoding="utf-8"
    )


def _read_flag(state_path: str) -> dict[str, Any] | None:
    p = _flag_path(state_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


_manager: MomentumDeskManager | None = None


def get_momentum_desk_manager() -> MomentumDeskManager:
    global _manager
    if _manager is None:
        _manager = MomentumDeskManager()
    return _manager


def reset_momentum_desk_manager() -> None:
    global _manager
    _manager = None


__all__ = [
    "CandleFeed",
    "Fill",
    "Gateway",
    "Holding",
    "LiveGateway",
    "MomentumDeskManager",
    "MomentumDeskRunner",
    "OrderState",
    "RunnerOptions",
    "desk_config_from_settings",
    "engine_settings_for_desk",
    "get_momentum_desk_manager",
    "reset_momentum_desk_manager",
]

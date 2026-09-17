"""Live runner for the Daily Momentum Desk.

Executes ``bot.live.momentum_desk`` decisions against one or more venues
through the existing fail-closed ``LiveMicroEngine`` (policy gates + audit).
Design:

* 20s tick when flat; denser ``mark_tick_sec`` while holdings are open so
  venue marks + fade-velocity exits stay timely. Bar trail/stop/time exits
  still evaluate once per closed 15m bar; entries once per decision hour (UTC).
* Venue routing: signals come from Bitvavo candles for every position; each
  entry is executed on the first venue in ``RunnerOptions.venues`` (cheapest
  fees first) that has enough EUR for the clip, so the second venue acts as
  overflow capital rather than a duplicate book.
* Buys: post-only maker at the bid, re-pegged for up to ``buy_rest_sec``,
  then one taker fallback. Trail / time exits: maker at the ask for
  ``sell_rest_sec``, then sliced taker chase until flat (escalating cross).
  Hard stops: skip maker, same sliced chase immediately. Peak trailing only
  works if the sell actually completes — chase-to-flat is that guarantee.
* Every fill is appended to a JSONL ledger with entry/exit reason codes;
  state (positions, risk ledger) is persisted so restarts are safe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from bot.core.config import Settings, get_settings
from bot.live.momentum_desk import (
    BAR_MS,
    AlphaIView,
    BaseStats,
    Candle,
    DeskConfig,
    Entry,
    ExitDecision,
    FadeState,
    Position,
    RiskLedger,
    bar_stats,
    classify_regime,
    evaluate_exit,
    is_entry_weekday,
    is_scheduled_hour,
    max_clip_mult,
    rank_candidates,
    regime_label,
    select_entries,
    summarize_regime_pnl,
    universe_stats,
    unrealized_net_eur,
    update_fade_state,
)
from bot.live.momentum_trade_outcomes import MomentumTradeOutcomeStore

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
    # Coins taken as fee in the base asset (OKX maker buys). Net coins
    # credited = filled_qty - fee_base_qty; selling the gross fill fails.
    fee_base_qty: float = 0.0


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
        return _from_exchange_order(order, symbol)

    async def cancel_order(self, order_id: str, symbol: str) -> OrderState:
        order = await self._client(trading=True).cancel_order(order_id, symbol)
        return _from_exchange_order(order, symbol)

    async def quote_balance_eur(self) -> float | None:
        snap = await self._client(trading=False).get_balances()
        for bal in snap.balances:
            if str(bal.asset).upper() == "EUR":
                return float(bal.total)
        return 0.0

    async def base_free(self, base: str) -> float | None:
        """Free units of ``base`` available to sell (None if balance fetch fails)."""
        try:
            snap = await self._client(trading=False).get_balances()
        except Exception as exc:  # noqa: BLE001
            logger.warning("momentum desk: %s balance fetch failed: %s", self._venue, exc)
            return None
        want = base.upper()
        for bal in snap.balances:
            if str(bal.asset).upper() == want:
                return float(getattr(bal, "free", None) or bal.total or 0.0)
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


def _from_exchange_order(order: Any, symbol: str | None = None) -> OrderState:
    fee_raw = float(order.fee_cost or 0.0)
    ccy = str(order.fee_currency or "EUR").upper()
    avg = float(order.average_price) if order.average_price else None
    base = ""
    if symbol:
        # XRPEUR / XRP-EUR / XRP/EUR
        cleaned = str(symbol).upper().replace("-", "").replace("/", "")
        base = cleaned[:-3] if cleaned.endswith("EUR") else cleaned.split("EUR")[0]
    fee_base = fee_raw if (base and ccy == base and fee_raw > 0) else 0.0
    fee_eur = fee_raw
    if fee_base > 0 and avg:
        fee_eur = fee_base * avg
    elif ccy != "EUR" and avg and fee_raw > 0:
        fee_eur = fee_raw * avg
    return OrderState(
        order_id=str(order.id),
        status=_norm_status(order.status),
        filled_qty=float(order.filled_quantity or 0.0),
        avg_price=avg,
        fee_eur=fee_eur,
        fee_base_qty=fee_base,
    )


# ---------------------------------------------------------------------------
# Candle feed
# ---------------------------------------------------------------------------


class CandleFeed:
    """Bitvavo public 15m candles + last-trade ticker with small TTL caches."""

    def __init__(
        self,
        base_url: str = BITVAVO_PUBLIC,
        ttl_sec: float = 10.0,
        *,
        ticker_ttl_sec: float = 2.0,
    ) -> None:
        self._base_url = base_url
        self._ttl = ttl_sec
        self._ticker_ttl = ticker_ttl_sec
        self._cache: dict[tuple[str, int], tuple[float, list[list[float]]]] = {}
        self._ticker_cache: dict[str, tuple[float, float]] = {}

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

    async def last_price(self, base: str) -> float | None:
        """Last trade price for dashboard / live marks (Bitvavo public ticker)."""
        now = time.time()
        hit = self._ticker_cache.get(base)
        if hit and now - hit[0] < self._ticker_ttl:
            return hit[1]
        url = f"{self._base_url}/ticker/price"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, params={"market": f"{base}-EUR"})
                resp.raise_for_status()
                payload = resp.json()
            price = float(payload["price"] if isinstance(payload, dict) else payload[0]["price"])
        except Exception:  # noqa: BLE001
            return None
        if price <= 0:
            return None
        self._ticker_cache[base] = (now, price)
        return price


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class Holding:
    pos: Position
    holding_id: str
    last_bar_ms: int = 0
    exiting: bool = False
    # Live-only; not persisted (re-arms after restart).
    fade: FadeState = field(default_factory=FadeState)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pos": asdict(self.pos),
            "holding_id": self.holding_id,
            "last_bar_ms": self.last_bar_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Holding:
        from dataclasses import fields as dc_fields

        raw = dict(data.get("pos") or {})
        allowed = {f.name for f in dc_fields(Position)}
        pos_kwargs = {k: v for k, v in raw.items() if k in allowed}
        if "entry_ctx" in pos_kwargs and not isinstance(pos_kwargs["entry_ctx"], dict):
            pos_kwargs["entry_ctx"] = {}
        return cls(
            pos=Position(**pos_kwargs),
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
    # Preference order for entries; the first venue is the primary (cheapest).
    venues: tuple[str, ...] = ("bitvavo",)
    # Legacy fraction gate (kept for callers); residual fills use
    # ``min_residual_clip_eur`` so leftover cash still deploys.
    min_clip_fraction: float = 0.5
    # Absolute floor for a reduced leftover fill on the richest venue.
    # Example: clip €1690 with €816 left → still buy ~€816, do not skip.
    min_residual_clip_eur: float = 100.0
    tick_sec: float = 20.0
    # While holdings are open, sleep this long between ticks so venue BBO marks
    # and fade-velocity exits stay dense. Flat desk keeps ``tick_sec``.
    mark_tick_sec: float = 4.0
    buy_rest_sec: float = 90.0
    sell_rest_sec: float = 60.0
    repeg_sec: float = 20.0
    poll_sec: float = 3.0
    taker_cross_bps: float = 20.0
    # Exit hardening: large sells are split and chased to flat so trail/stop
    # exits cannot leave inventory after a single thin taker attempt.
    sell_slice_eur: float = 2500.0  # 0 = single shot
    sell_chase_rounds: int = 4  # extra taker rounds after the first
    sell_chase_step_bps: float = 15.0  # deepen the cross each chase round
    # Final wide cross after chase budget is exhausted (0 disables). Keeps
    # leftover inventory from sitting after thin books refuse normal crosses.
    sell_disaster_extra_bps: float = 80.0
    sell_taker_poll_sec: float = 30.0
    decision_grace_sec: float = 30.0
    bar_close_grace_sec: float = 15.0
    state_path: str = "./data/momentum_desk_state.json"
    ledger_path: str = "./data/momentum_desk_ledger.jsonl"
    alphai_recommendations_path: str | None = "./data/alphai/daily_recommendations.json"
    alphai_pick_outcomes_path: str | None = "./data/alphai/pick_outcomes.json"
    outcome_learning_path: str = "./data/momentum_trade_outcomes.json"
    outcome_learning_enabled: bool = True
    outcome_learning_auto_size: bool = True
    outcome_min_samples: int = 8
    outcome_full_samples: int = 25
    outcome_mult_min: float = 0.75
    outcome_mult_max: float = 1.15
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
        gateways: Mapping[str, Gateway] | None = None,
    ) -> None:
        self.cfg = cfg
        self.opt = options or RunnerOptions()
        # ``gateway`` is the primary venue's gateway; ``gateways`` adds the rest.
        self._gws: dict[str, Gateway] = {}
        if gateway is not None:
            self._gws[self.opt.venues[0]] = gateway
        for venue, gw in (gateways or {}).items():
            self._gws[venue] = gw
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
        self.mark_sources: dict[str, str] = {}
        self.marks_updated_at: float | None = None
        self.cash_by_venue: dict[str, float] = {}
        self._cash_ts: float = 0.0
        # Set when an exit frees a slot; consumed by ``_maybe_refill``.
        self._refill_pending: bool = False
        self._lock = asyncio.Lock()
        self.started_at = datetime.now(UTC).isoformat()
        self.outcomes = MomentumTradeOutcomeStore.load(
            self.opt.outcome_learning_path,
            enabled=bool(self.opt.outcome_learning_enabled),
            auto_size=bool(self.opt.outcome_learning_auto_size),
            min_samples=int(self.opt.outcome_min_samples),
            full_samples=int(self.opt.outcome_full_samples),
            mult_min=float(self.opt.outcome_mult_min),
            mult_max=float(self.opt.outcome_mult_max),
        )
        self._load_state()

    @property
    def cash_eur(self) -> float | None:
        if not self.cash_by_venue:
            return None
        return sum(self.cash_by_venue.values())

    def _gateway(self, venue: str) -> Gateway | None:
        return self._gws.get(venue)

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
                    "holding_id": h.holding_id,
                    "base": h.pos.base,
                    "venue": h.pos.venue,
                    "entry_price": h.pos.entry_price,
                    "quantity": h.pos.quantity,
                    "notional_eur": round(h.pos.notional_eur, 2),
                    "opened": datetime.fromtimestamp(h.pos.opened_ms / 1000, UTC).isoformat(),
                    "age_h": round((now_ms - h.pos.opened_ms) / 3_600_000, 2),
                    "peak_return": round(h.pos.peak / h.pos.entry_price - 1, 4),
                    "mark": mark,
                    "mark_source": self.mark_sources.get(h.pos.base),
                    "mark_age_sec": (
                        round(self._clock() - self.marks_updated_at, 1)
                        if self.marks_updated_at is not None and mark is not None
                        else None
                    ),
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
            "cash_by_venue": {k: round(v, 2) for k, v in sorted(self.cash_by_venue.items())},
            "exposure_eur": round(exposure, 2),
            "equity_eur": round(equity, 2) if equity is not None else None,
            "venue": self.opt.venues[0],
            "venues": list(self.opt.venues),
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
            "regime_pnl": self._regime_pnl_summary(),
            "next_decision": self._next_decision_iso(now_ms),
            "last_error": self.last_error,
            "outcome_learning": self.outcomes.summary(),
            "marks_updated_at": (
                datetime.fromtimestamp(self.marks_updated_at, UTC).isoformat()
                if self.marks_updated_at
                else None
            ),
        }

    def _regime_pnl_summary(self) -> dict[str, Any]:
        """Closed-trade PnL by entry regime_label from the JSONL ledger."""
        path = Path(self.opt.ledger_path)
        if not path.exists():
            return {}
        exits: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or row.get("event") != "exit":
                    continue
                exits.append(row)
        except OSError:
            return {}
        return summarize_regime_pnl(exits)

    def _next_decision_iso(self, now_ms: int) -> str:
        interval = float(getattr(self.cfg, "decision_interval_sec", 0.0) or 0.0)
        if interval > 0.0:
            slot_ms = max(1, int(interval * 1000))
            # Start at the next slot boundary; skip weekend when configured.
            cand = (now_ms // slot_ms + 1) * slot_ms
            for _ in range(0, int(3 * 86_400_000 / slot_ms) + 2):
                if cand > self.last_decision_hour_ms and is_entry_weekday(cand, self.cfg):
                    return datetime.fromtimestamp(cand / 1000, UTC).isoformat()
                cand += slot_ms
            return ""
        hour_ms = 3_600_000
        cur = now_ms // hour_ms * hour_ms
        # Up to 3 days ahead so a Friday-evening dashboard still shows Monday.
        for i in range(0, 73):
            cand = cur + i * hour_ms
            if is_scheduled_hour(cand, self.cfg) and cand > self.last_decision_hour_ms:
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
            sleep_for = self.opt.tick_sec
            if self.holdings and float(self.opt.mark_tick_sec) > 0:
                sleep_for = min(sleep_for, float(self.opt.mark_tick_sec))
            await self._sleep(sleep_for)

    async def tick(self) -> None:
        async with self._lock:
            now_ms = int(self._clock() * 1000)
            await self.refresh_marks()
            await self._manage_exits(now_ms)
            # Cash first: the venue router needs fresh balances at the decision hour.
            await self._refresh_cash()
            # Refill freed slots immediately (WR hours stay sparse; don't wait).
            await self._maybe_refill(now_ms)
            await self._maybe_decide(now_ms)

    async def refresh_marks(self) -> None:
        """Refresh open-position marks from the **holding's venue** book.

        Uses mid of ``best_bid_ask`` on the venue where the coins sit (Bitvavo
        or OKX). Falls back to the Bitvavo public ticker / forming candle only
        when no gateway is available (dry-run) or the venue book call fails —
        so OKX inventory is not marked off Bitvavo tape.
        """
        if not self.holdings:
            return
        updated = False
        for h in list(self.holdings):
            if h.exiting:
                continue
            px, source = await self._mark_for_holding(h)
            if px is None or px <= 0:
                continue
            self.marks[h.pos.base] = float(px)
            self.mark_sources[h.pos.base] = source
            updated = True
        if updated:
            self.marks_updated_at = self._clock()

    async def _mark_for_holding(self, h: Holding) -> tuple[float | None, str]:
        venue = str(h.pos.venue or self.opt.venues[0]).lower()
        base = h.pos.base
        gw = self._gateway(venue)
        if gw is not None:
            try:
                bid, ask = await gw.best_bid_ask(f"{base}EUR")
                bid_f, ask_f = float(bid), float(ask)
                if bid_f > 0 and ask_f > 0:
                    return (bid_f + ask_f) / 2.0, f"{venue}_bbo"
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "momentum desk: %s BBO mark failed for %s: %s", venue, base, exc
                )
        # Dry-run / missing gateway / book error: Bitvavo public tape.
        px = await self._feed.last_price(base)
        if px is not None and px > 0:
            return float(px), "bitvavo_ticker"
        rows = await self._feed.candles(base, 1)
        if rows:
            return float(rows[-1][4]), "bitvavo_candle"
        return None, "none"

    async def _refresh_cash(self) -> None:
        if self._clock() - self._cash_ts < 60.0:
            return
        fetched_any = False
        for venue, gw in self._gws.items():
            fetch = getattr(gw, "quote_balance_eur", None)
            if fetch is None:
                continue
            try:
                cash = await fetch()
            except Exception as exc:  # noqa: BLE001
                logger.warning("momentum desk: %s balance fetch failed: %s", venue, exc)
                continue
            if cash is not None:
                self.cash_by_venue[venue] = float(cash)
                fetched_any = True
        if fetched_any:
            self._cash_ts = self._clock()

    def _reserved_other_sleeves_eur(self) -> float:
        """Cash earmarked for hold (and optional core soft book leftover peer)."""
        reserved = 0.0
        try:
            from bot.live.momentum_hold_runner import hold_reserved_eur

            reserved += float(hold_reserved_eur() or 0.0)
        except Exception:  # noqa: BLE001
            pass
        # Soft core book: do not deploy above cfg.book_eur when set.
        book = float(getattr(self.cfg, "book_eur", 0.0) or 0.0)
        if book > 0:
            deployed = sum(max(0.0, float(h.pos.notional_eur)) for h in self.holdings)
            # Cap clip by free soft book — applied below via reduced cash.
            self._soft_book_left = max(0.0, book - deployed)
        else:
            self._soft_book_left = None
        return reserved

    def _route_entry(self, clip_eur: float) -> tuple[str, float] | None:
        """Pick the venue for a clip: first in preference order with enough
        EUR. Without any gateway (dry-run) or balance data the primary is used.

        If no venue can fund the full clip, deploy the leftover on the richest
        venue (most cash available) as long as that residual is still a
        meaningful order — do not require a large fraction of the planned clip.

        Hold-sleeve undeployed soft book is subtracted from available cash so
        core momentum cannot spend the buy&hold reserve.
        """
        venues = self.opt.venues
        reserved = self._reserved_other_sleeves_eur()
        soft_left = getattr(self, "_soft_book_left", None)
        if not self._gws or not self.cash_by_venue:
            clip = clip_eur
            if soft_left is not None:
                clip = min(clip, float(soft_left))
            min_ok = max(_MIN_ORDER_EUR, float(self.opt.min_residual_clip_eur))
            if clip < min_ok:
                return None
            return venues[0], round(clip, 2)
        need = clip_eur * 1.005  # tiny buffer for taker slippage / fee
        for venue in venues:
            cash = self.cash_by_venue.get(venue)
            if cash is None or venue not in self._gws:
                continue
            avail = max(0.0, float(cash) - reserved)
            if soft_left is not None:
                avail = min(avail, float(soft_left))
            if avail >= need:
                return venue, clip_eur
        # Full clip unavailable: use the venue with the most leftover cash.
        best = max(
            (
                (
                    v,
                    max(0.0, float(self.cash_by_venue.get(v, 0.0)) - reserved),
                )
                for v in venues
                if v in self._gws
            ),
            key=lambda item: item[1],
            default=None,
        )
        if best is None:
            return None
        venue, cash = best
        if soft_left is not None:
            cash = min(cash, float(soft_left))
        reduced = cash / 1.005
        min_ok = max(_MIN_ORDER_EUR, float(self.opt.min_residual_clip_eur))
        if reduced < min_ok:
            return None
        return venue, round(min(reduced, clip_eur), 2)

    # ----------------------------------------------------------------- exits

    async def _manage_exits(self, now_ms: int) -> None:
        if not self.holdings:
            return
        last_closed = (now_ms // BAR_MS) * BAR_MS - BAR_MS
        bar_ready = now_ms >= last_closed + BAR_MS + int(self.opt.bar_close_grace_sec * 1000)
        alphai = self._alphai_view() if bar_ready else None
        now_s = self._clock()
        for h in list(self.holdings):
            if h.exiting:
                continue
            live_px = self.marks.get(h.pos.base)
            rows: list[Candle] | None = None
            if live_px is None:
                rows = await self._feed.candles(h.pos.base, 4)
                if not rows:
                    continue
                live_px = float(rows[-1][4])
                self.marks[h.pos.base] = live_px
                self.mark_sources[h.pos.base] = "bitvavo_candle"
                self.marks_updated_at = now_s
            else:
                live_px = float(live_px)
            # Disaster stop on the live price — a 15m close is too slow for a crash.
            if live_px <= h.pos.entry_price * (1 - _DISASTER_STOP_MULT * self.cfg.hard_stop_pct):
                await self._exit(
                    h, ExitDecision("disaster_stop", h.pos.gross_return(live_px), True)
                )
                continue
            # Fade-velocity / ETA-to-zero on dense venue marks (independent of 15m bars).
            if float(self.cfg.fade_eta_sec) > 0.0:
                net = unrealized_net_eur(h.pos, live_px, self.cfg)
                h.fade, fade_dec = update_fade_state(
                    h.fade,
                    net_eur=net,
                    gross_return=h.pos.gross_return(live_px),
                    now=now_s,
                    cfg=self.cfg,
                )
                if fade_dec is not None:
                    await self._exit(h, fade_dec)
                    continue
            if not bar_ready or h.last_bar_ms >= last_closed:
                continue
            if rows is None:
                rows = await self._feed.candles(h.pos.base, 4)
            if not rows:
                continue
            bar = next((r for r in rows if int(r[0]) == last_closed), None)
            if bar is None:
                continue
            h.last_bar_ms = last_closed
            decision = evaluate_exit(h.pos, bar, self.cfg, alphai=alphai)
            if decision is not None:
                await self._exit(h, decision)

    async def _available_base(self, base: str, venue: str) -> float | None:
        gw = self._gateway(venue)
        fetch = getattr(gw, "base_free", None) if gw is not None else None
        if fetch is None:
            return None
        try:
            return await fetch(base)
        except Exception as exc:  # noqa: BLE001
            logger.warning("momentum desk: %s %s free balance failed: %s", venue, base, exc)
            return None

    async def _exit(self, h: Holding, decision: ExitDecision) -> Fill | None:
        h.exiting = True
        fail_detail: str | None = None
        try:
            sell_qty = h.pos.quantity
            free = await self._available_base(h.pos.base, h.pos.venue)
            if free is not None and free + 1e-12 < sell_qty:
                # Typical cause: entry fee was taken in the base asset (OKX), so
                # the book qty is the gross fill while only ``free`` is sellable.
                logger.info(
                    "momentum desk: clamping %s sell %.8f -> free %.8f on %s",
                    h.pos.base,
                    sell_qty,
                    free,
                    h.pos.venue,
                )
                h.pos.quantity = free
                h.pos.notional_eur = free * h.pos.entry_price
                sell_qty = free
            mark = self.marks.get(h.pos.base) or h.pos.entry_price
            if sell_qty * mark < _MIN_ORDER_EUR:
                fail_detail = "dust_or_no_balance"
                fill = None
            else:
                fill = await self._sell(
                    h.pos.base, sell_qty, urgent=decision.urgent, venue=h.pos.venue
                )
                if fill is None:
                    fail_detail = "order_rejected"
        finally:
            h.exiting = False
        if fill is None or fill.qty <= 0:
            self._ledger_append(
                {
                    "event": "exit_failed",
                    "base": h.pos.base,
                    "venue": h.pos.venue,
                    "reason": decision.reason,
                    "detail": fail_detail,
                    "book_qty": h.pos.quantity,
                }
            )
            self._save_state()
            return None
        net = (
            fill.qty * (fill.avg_price - h.pos.entry_price)
            - h.pos.entry_fee_eur * (fill.qty / h.pos.quantity)
            - fill.fee_eur
        )
        now_ms = int(self._clock() * 1000)
        self.realized_total_eur += net
        self.trade_count += 1
        self.ledger.note_close(net, now_ms)
        entry_ctx = dict(getattr(h.pos, "entry_ctx", None) or {})
        self._ledger_append(
            {
                "event": "exit",
                "holding_id": h.holding_id,
                "base": h.pos.base,
                "venue": h.pos.venue,
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
                "entry_ctx": entry_ctx,
                "regime_label": str(entry_ctx.get("regime_label") or ""),
            }
        )
        remaining = h.pos.quantity - fill.qty
        if remaining * fill.avg_price < _MIN_ORDER_EUR:
            self._record_outcome(h, net=net, decision=decision, now_ms=now_ms)
            self.holdings.remove(h)
            if bool(getattr(self.cfg, "refill_on_exit", True)) and len(
                self.holdings
            ) < int(self.cfg.max_positions):
                self._refill_pending = True
        else:
            h.pos.quantity = remaining
            h.pos.notional_eur = remaining * h.pos.entry_price
        self._save_state()
        return fill

    def _record_outcome(
        self, h: Holding, *, net: float, decision: ExitDecision, now_ms: int
    ) -> None:
        if not self.opt.outcome_learning_enabled:
            return
        try:
            from bot.live.momentum_trade_outcomes import parse_entry_ctx_from_reason

            ctx = dict(getattr(h.pos, "entry_ctx", None) or {})
            if not ctx:
                ctx = parse_entry_ctx_from_reason(h.pos.entry_reason)
            clip = float(h.pos.notional_eur or 0.0)
            if clip <= 0:
                clip = float(self.cfg.clip_eur)
            self.outcomes.record_close(
                holding_id=h.holding_id,
                base=h.pos.base,
                net_eur=float(net),
                clip_eur=clip,
                peak_return=float(h.pos.peak / h.pos.entry_price - 1.0)
                if h.pos.entry_price > 0
                else 0.0,
                hold_h=(now_ms - h.pos.opened_ms) / 3_600_000.0,
                exit_reason=str(decision.reason or ""),
                entry_ctx=ctx,
                opened_ms=int(h.pos.opened_ms),
                closed_ms=int(now_ms),
            )
            self.outcomes.save()
        except Exception:  # noqa: BLE001
            logger.warning("momentum desk: outcome learning record failed", exc_info=True)

    async def sell_all_now(self, *, urgent: bool = False) -> dict[str, Any]:
        """Sell every open holding sequentially (dashboard sell-all)."""
        ids = [h.holding_id for h in list(self.holdings)]
        results: list[dict[str, Any]] = []
        for hid in ids:
            results.append(await self.sell_now(hid, urgent=urgent))
        ok_n = sum(1 for r in results if r.get("ok"))
        return {
            "ok": ok_n == len(results) and bool(results),
            "sold": ok_n,
            "failed": len(results) - ok_n,
            "results": results,
            "all": True,
        }

    async def sell_now(self, holding_id: str, *, urgent: bool = False) -> dict[str, Any]:
        """Operator-initiated exit of one holding (dashboard sell button).

        Runs under the tick lock so it cannot race the rule-based exit of the
        same holding. Patient (maker, taker fallback) unless ``urgent``. The
        fill is booked in the ledger with reason ``manual`` like any other exit.
        """
        async with self._lock:
            h = next((x for x in self.holdings if x.holding_id == holding_id), None)
            if h is None:
                return {"ok": False, "reason": "unknown_holding"}
            if h.exiting:
                return {"ok": False, "reason": "exit_in_progress"}
            base = h.pos.base
            mark = self.marks.get(base) or h.pos.entry_price
            fill = await self._exit(
                h, ExitDecision("manual", h.pos.gross_return(mark), urgent=urgent)
            )
            if fill is None or fill.qty <= 0:
                return {"ok": False, "reason": "exit_failed", "base": base}
            left = h.pos.quantity if h in self.holdings else 0.0
            return {
                "ok": True,
                "base": base,
                "sold_qty": fill.qty,
                "remaining_qty": left,
                "partial": left > 0,
            }

    # --------------------------------------------------------------- entries

    def _decision_slot_due(self, now_ms: int) -> int | None:
        """Return the decision slot timestamp due now, or None.

        When ``decision_interval_sec`` > 0, fire once per interval on weekdays
        (filters stay strict — this only raises cadence). Otherwise keep the
        sparse ``decision_hours_utc`` hour slots with grace / staleness guards.
        """
        interval = float(getattr(self.cfg, "decision_interval_sec", 0.0) or 0.0)
        if interval > 0.0:
            if not is_entry_weekday(now_ms, self.cfg):
                return None
            slot_ms = max(1, int(interval * 1000))
            cur = now_ms // slot_ms * slot_ms
            if cur <= self.last_decision_hour_ms:
                return None
            return cur
        hour_ms = 3_600_000
        cur = now_ms // hour_ms * hour_ms
        if not is_scheduled_hour(cur, self.cfg):
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

    def _alphai_view(self, stats: Mapping[str, BaseStats] | None = None) -> AlphaIView:
        path = self.opt.alphai_recommendations_path
        if not path:
            return AlphaIView()
        p = Path(path)
        if not p.exists():
            return AlphaIView()
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("momentum desk: AlphaI recommendations unreadable; ignoring")
            return AlphaIView()
        if not isinstance(payload, dict):
            return AlphaIView()

        # Optional live overlays used only for clip sizing (not membership).
        try:
            from bot.integrations.alphai.price_confirm import enrich_daily_with_price_check
            from bot.integrations.alphai.pick_outcomes import PickOutcomeStore

            day_rets: dict[str, float] = {}
            if stats:
                for base, st in stats.items():
                    ret = getattr(st, "ret_24h", None)
                    if ret is None:
                        continue
                    try:
                        day_rets[str(base).upper()] = float(ret) * 100.0
                    except (TypeError, ValueError):
                        continue
            reliability = None
            try:
                outcomes_path = (
                    getattr(self.opt, "alphai_pick_outcomes_path", None)
                    or "./data/alphai/pick_outcomes.json"
                )
                store = PickOutcomeStore.load(outcomes_path)
                reliability = store.summary().get("base_reliability") or None
            except Exception:  # noqa: BLE001
                reliability = None
            if day_rets:
                payload = enrich_daily_with_price_check(
                    payload,
                    day_rets,
                    base_reliability=reliability,
                ) or payload
            elif reliability:
                payload = dict(payload)
                payload["base_reliability"] = {
                    str(k).upper(): float(v) for k, v in dict(reliability).items()
                }
        except Exception:  # noqa: BLE001
            logger.debug("momentum desk: AlphaI size overlays skipped", exc_info=True)
        return AlphaIView.from_recommendations(payload)

    async def _maybe_refill(self, now_ms: int) -> None:
        """One immediate entry pass after an exit frees a slot.

        Keeps sparse high-WR decision hours, but does not leave cash idle until
        the next scheduled slot when a runner is already printing. Does not
        advance ``last_decision_hour_ms`` so the hour schedule stays intact.
        """
        if not self._refill_pending:
            return
        self._refill_pending = False
        if not bool(getattr(self.cfg, "refill_on_exit", True)):
            return
        if len(self.holdings) >= int(self.cfg.max_positions):
            return
        if not is_entry_weekday(now_ms, self.cfg):
            return
        t_ms = (now_ms // BAR_MS) * BAR_MS
        await self._decide(t_ms, now_ms, execute=True, trigger="refill")

    async def _maybe_decide(self, now_ms: int) -> None:
        slot_ms = self._decision_slot_due(now_ms)
        if slot_ms is None:
            return
        self.last_decision_hour_ms = slot_ms
        # Interval mode: score the latest closed 15m bar. Hour mode: align to
        # the scheduled hour start (historical live behaviour).
        interval = float(getattr(self.cfg, "decision_interval_sec", 0.0) or 0.0)
        t_ms = (now_ms // BAR_MS) * BAR_MS if interval > 0.0 else slot_ms
        await self._decide(t_ms, now_ms, execute=True, trigger="schedule")

    async def decide_now(
        self, *, execute: bool, expect_bases: Iterable[str] | None = None
    ) -> dict[str, Any]:
        """Operator-triggered decision on the latest closed bar.

        ``execute=False`` previews (no orders, no ledger row, no state change);
        ``execute=True`` trades exactly like the scheduled decision, under the
        same risk ledger and one-entry-per-base-per-day rule. The scheduled
        decision hour is left untouched. ``expect_bases`` binds a commit to the
        preview the operator saw: if the desk would now enter a different set
        of bases nothing is bought and ``mismatch`` is set.
        """
        async with self._lock:
            now_ms = int(self._clock() * 1000)
            await self._refresh_cash()
            return await self._decide(
                (now_ms // BAR_MS) * BAR_MS,
                now_ms,
                execute=execute,
                trigger="manual",
                expect_bases=(
                    {b.upper() for b in expect_bases} if expect_bases is not None else None
                ),
            )

    async def _decide(
        self,
        t_ms: int,
        now_ms: int,
        *,
        execute: bool,
        trigger: str,
        expect_bases: set[str] | None = None,
    ) -> dict[str, Any]:
        candles: dict[str, Sequence[Candle]] = {}
        for base in ("BTC", *self.cfg.universe):
            try:
                candles[base] = await self._feed.candles(base, 110)
            except Exception as exc:  # noqa: BLE001
                logger.warning("momentum desk: candles failed for %s: %s", base, exc)
        stats = universe_stats(candles, t_ms, self.cfg)
        alphai = self._alphai_view(stats)
        btc = bar_stats("BTC", candles["BTC"], t_ms) if candles.get("BTC") else None
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
                now_ms=now_ms,
                outcome_store=self.outcomes if self.opt.outcome_learning_enabled else None,
            )

        # Size clips to venue cash *before* planning so operators see the
        # executable clip and we do not "plan full / skip for cash" later.
        # Pre-size clips to venue cash so plans match what can fill. Unfundable
        # names stay in the list so ``_enter`` still emits ``entry_skipped``.
        sized: list[Entry] = []
        for entry in entries:
            route = self._route_entry(entry.clip_eur)
            if route is None:
                sized.append(entry)
                continue
            _venue, clip = route
            reasons = entry.reasons
            if clip + 1e-9 < entry.clip_eur:
                reasons = tuple(reasons) + ("clip_reduced",)
                sized.append(
                    Entry(
                        base=entry.base,
                        clip_eur=round(clip, 2),
                        score=entry.score,
                        reasons=reasons,
                        entry_ctx=dict(entry.entry_ctx or {}),
                    )
                )
            else:
                sized.append(entry)
        entries = sized
        # Why the leaders that did not qualify were dropped, for the operator.
        rejected = []
        if regime.ok:
            btc_ret = regime.btc_ret or 0.0
            for base, st in sorted(
                stats.items(), key=lambda kv: kv[1].ret_24h - btc_ret, reverse=True
            )[:8]:
                if any(c.base == base for c in cands):
                    continue
                excess = st.ret_24h - btc_ret
                why_not = (
                    "alphai_avoid"
                    if base in alphai.avoid
                    else "excess_low"
                    if excess < self.cfg.min_excess
                    else "far_from_high"
                    if st.from_high < -self.cfg.max_from_high
                    else "volume_low"
                    if st.volume_eur < self.cfg.min_volume_eur
                    else "other"
                )
                rejected.append(
                    {
                        "base": base,
                        "excess": round(excess, 4),
                        "from_high": round(st.from_high, 4),
                        "why": why_not,
                    }
                )
        summary = {
            "at": datetime.fromtimestamp(t_ms / 1000, UTC).isoformat(),
            "trigger": trigger,
            "executed": execute,
            "ok": regime.ok,
            "soft": bool(getattr(regime, "soft", False)),
            "regime_label": regime_label(regime, self.cfg),
            "btc_ret": round(regime.btc_ret, 4) if regime.btc_ret is not None else None,
            "breadth": round(regime.breadth, 3),
            "reasons": list(regime.reasons),
            "candidates": [
                {
                    "base": c.base,
                    "excess": round(c.excess, 4),
                    "from_high": round(c.from_high, 4),
                    "alphai_pick": c.alphai_pick,
                }
                for c in cands[:6]
            ],
            "rejected": rejected[:6],
            "entries": [e.base for e in entries],
            "planned": [self._plan_row(e, stats) for e in entries],
            "risk_block": "" if allowed else why,
            "alphai": {
                "macro_caution": alphai.macro_caution,
                "avoid": sorted(alphai.avoid),
                "picks": sorted(alphai.picks),
            },
        }
        if not execute:
            return summary
        if expect_bases is not None and {e.base for e in entries} != expect_bases:
            summary["mismatch"] = True
            summary["expected"] = sorted(expect_bases)
            self._ledger_append(
                {
                    "event": "commit_rejected",
                    "expected": sorted(expect_bases),
                    "planned": [e.base for e in entries],
                }
            )
            return summary
        self.last_regime = summary
        self._ledger_append({"event": "decision", **summary})
        for entry in entries:
            await self._enter(
                entry.base,
                entry.clip_eur,
                ",".join(entry.reasons),
                now_ms,
                entry_ctx=dict(entry.entry_ctx or {}),
            )
        self._save_state()
        return summary

    def _plan_row(self, entry: Entry, stats: Mapping[str, BaseStats]) -> dict[str, Any]:
        """Planned entry with the figures an operator needs to judge it: routed
        venue, reference price (last 15m close), quantity, fee and stop levels.
        Execution uses the live book, so fills differ slightly."""
        route = self._route_entry(entry.clip_eur)
        venue, clip = route if route is not None else (None, entry.clip_eur)
        st = stats.get(entry.base)
        price = st.price if st is not None else None
        fee_side = self.cfg.fee_rt / 2
        row: dict[str, Any] = {
            "base": entry.base,
            "clip_eur": clip,
            "reasons": list(entry.reasons),
            "venue": venue,
            "price": price,
            "fee_in_eur": round(clip * fee_side, 2),
            "fee_out_eur": round(clip * fee_side, 2),
            "hard_stop_pct": self.cfg.hard_stop_pct,
            "early_stop_pct": self.cfg.early_stop_pct,
            "early_stop_until_peak": self.cfg.early_stop_until_peak,
            "trail_pct": self.cfg.trail_pct,
            "trail_tight_after": self.cfg.trail_tight_after,
            "trail_tight_pct": self.cfg.trail_tight_pct,
        }
        if price:
            qty = clip / price
            row["qty"] = round(qty, 8)
            row["break_even"] = round(price * (1 + self.cfg.fee_rt), 8)
            # Unproven entries use early stop when armed; else hard stop.
            entry_stop = (
                float(self.cfg.early_stop_pct)
                if float(self.cfg.early_stop_pct or 0.0) > 0.0
                and float(self.cfg.early_stop_until_peak or 0.0) > 0.0
                else float(self.cfg.hard_stop_pct)
            )
            row["hard_stop_price"] = round(price * (1 - self.cfg.hard_stop_pct), 8)
            row["hard_stop_eur"] = round(-clip * self.cfg.hard_stop_pct - clip * self.cfg.fee_rt, 2)
            row["entry_stop_pct"] = entry_stop
            row["entry_stop_price"] = round(price * (1 - entry_stop), 8)
            row["entry_stop_eur"] = round(-clip * entry_stop - clip * self.cfg.fee_rt, 2)
        if route is None:
            row["blocked"] = "insufficient_cash"
        return row

    async def _enter(
        self,
        base: str,
        clip_eur: float,
        reason: str,
        now_ms: int,
        *,
        entry_ctx: Mapping[str, Any] | None = None,
    ) -> None:
        route = self._route_entry(clip_eur)
        if route is None:
            self._ledger_append(
                {
                    "event": "entry_skipped",
                    "base": base,
                    "reason": "insufficient_cash",
                    "cash_by_venue": dict(self.cash_by_venue),
                    "clip_eur": clip_eur,
                }
            )
            return
        venue, clip = route
        if clip != clip_eur:
            reason = f"{reason},clip_reduced"
        fill = await self._buy(base, clip, venue=venue)
        if fill is None or fill.qty <= 0:
            self._ledger_append(
                {"event": "entry_failed", "base": base, "venue": venue, "reason": reason}
            )
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
            venue=venue,
            entry_ctx=dict(entry_ctx or {}),
        )
        holding = Holding(
            pos=pos, holding_id=uuid.uuid4().hex[:10], last_bar_ms=now_ms // BAR_MS * BAR_MS
        )
        self.holdings.append(holding)
        self.ledger.note_entry(base, now_ms)
        self.marks[base] = fill.avg_price
        self.mark_sources[base] = f"{venue}_fill"
        if venue in self.cash_by_venue:
            self.cash_by_venue[venue] -= fill.notional + fill.fee_eur
        self._ledger_append(
            {
                "event": "entry",
                "holding_id": holding.holding_id,
                "base": base,
                "venue": venue,
                "qty": fill.qty,
                "price": fill.avg_price,
                "notional_eur": round(fill.notional, 2),
                "fee_eur": round(fill.fee_eur, 4),
                "taker": fill.taker,
                "reason": reason,
                "entry_ctx": dict(pos.entry_ctx),
            }
        )
        self._save_state()

    # ------------------------------------------------------------ execution

    async def _buy(
        self, base: str, notional_eur: float, *, venue: str | None = None
    ) -> Fill | None:
        return await self._work_order(
            base, "buy", notional_eur=notional_eur, rest_sec=self.opt.buy_rest_sec, venue=venue
        )

    def _sell_slices(self, qty: float, px: float) -> list[float]:
        """Split a sell into book-friendly child sizes (last slice gets the remainder)."""
        if qty <= 0 or px <= 0:
            return []
        slice_eur = float(self.opt.sell_slice_eur or 0.0)
        if slice_eur <= 0 or qty * px <= slice_eur + _MIN_ORDER_EUR:
            return [qty]
        slice_qty = slice_eur / px
        out: list[float] = []
        left = qty
        while left * px >= _MIN_ORDER_EUR:
            if left <= slice_qty * 1.25:  # avoid a dust leftover child
                out.append(left)
                break
            chunk = min(left, slice_qty)
            out.append(chunk)
            left -= chunk
        return out

    async def _sell(
        self, base: str, qty: float, *, urgent: bool, venue: str | None = None
    ) -> Fill | None:
        """Sell ``qty``, slicing and chasing until flat (or chase budget exhausted).

        Peak trailing / hard stops only protect PnL if the exit actually fills.
        Patient exits rest as maker once, then every exit path — trail included —
        slices the remainder and escalates the taker cross across chase rounds.
        """
        venue = venue or self.opt.venues[0]
        gw = self._gateway(venue)
        mark = 0.0
        if gw is not None:
            try:
                bid, ask = await gw.best_bid_ask(f"{base}EUR")
                mark = float(bid or ask or 0.0)
            except Exception:  # noqa: BLE001
                mark = 0.0
        if mark <= 0:
            mark = float(self.marks.get(base) or 0.0)
        if mark <= 0:
            mark = 1.0

        filled_qty = 0.0
        filled_cost = 0.0
        fee_eur = 0.0
        taker_used = False
        remaining = float(qty)
        total_rounds = 1 + max(0, int(self.opt.sell_chase_rounds))

        for round_i in range(total_rounds):
            if remaining * mark < _MIN_ORDER_EUR:
                break
            before = remaining
            cross_bps = float(self.opt.taker_cross_bps) + round_i * float(
                self.opt.sell_chase_step_bps
            )
            # Maker rest only on the first child of round 0 for patient exits.
            rest_budget = 0.0 if urgent or round_i > 0 else float(self.opt.sell_rest_sec)
            slices = self._sell_slices(remaining, mark)
            for idx, chunk in enumerate(slices):
                if chunk * mark < _MIN_ORDER_EUR:
                    continue
                child_rest = rest_budget if idx == 0 else 0.0
                fill = await self._work_order(
                    base,
                    "sell",
                    qty=chunk,
                    rest_sec=child_rest,
                    venue=venue,
                    cross_bps=cross_bps,
                    taker_poll_sec=float(self.opt.sell_taker_poll_sec),
                )
                if fill is None or fill.qty <= 0:
                    continue
                filled_qty += fill.qty
                filled_cost += fill.qty * fill.avg_price
                fee_eur += fill.fee_eur
                taker_used = taker_used or fill.taker
                remaining = max(0.0, remaining - fill.qty)
                mark = fill.avg_price or mark
            if remaining * mark < _MIN_ORDER_EUR:
                break
            if remaining >= before - 1e-12:
                # No progress this round — further escalation still tried once more,
                # but if the book returns nothing we stop after the budget.
                logger.warning(
                    "momentum desk: sell chase round %s made no progress on %s "
                    "(left %.8f @ ~%.4f EUR)",
                    round_i,
                    base,
                    remaining,
                    mark,
                )

        # Disaster mop-up: one last wide cross if chase left economic size.
        disaster_extra = float(self.opt.sell_disaster_extra_bps)
        if remaining * mark >= _MIN_ORDER_EUR and disaster_extra > 0.0:
            cross_bps = float(self.opt.taker_cross_bps) + max(
                0, int(self.opt.sell_chase_rounds)
            ) * float(self.opt.sell_chase_step_bps) + disaster_extra
            logger.warning(
                "momentum desk: sell disaster mop-up on %s left %.8f @ cross %.0f bps",
                base,
                remaining,
                cross_bps,
            )
            for chunk in self._sell_slices(remaining, mark):
                if chunk * mark < _MIN_ORDER_EUR:
                    continue
                fill = await self._work_order(
                    base,
                    "sell",
                    qty=chunk,
                    rest_sec=0.0,
                    venue=venue,
                    cross_bps=cross_bps,
                    taker_poll_sec=float(self.opt.sell_taker_poll_sec),
                )
                if fill is None or fill.qty <= 0:
                    continue
                filled_qty += fill.qty
                filled_cost += fill.qty * fill.avg_price
                fee_eur += fill.fee_eur
                taker_used = True
                remaining = max(0.0, remaining - fill.qty)
                mark = fill.avg_price or mark

        if filled_qty <= 0:
            return None
        if remaining * mark >= _MIN_ORDER_EUR:
            logger.warning(
                "momentum desk: sell chase exhausted for %s — filled %.8f / %.8f "
                "(~%.0f EUR left)",
                base,
                filled_qty,
                qty,
                remaining * mark,
            )
        return Fill(
            qty=filled_qty,
            avg_price=filled_cost / filled_qty,
            fee_eur=fee_eur,
            taker=taker_used,
        )

    async def _work_order(
        self,
        base: str,
        side: str,
        *,
        qty: float | None = None,
        notional_eur: float | None = None,
        rest_sec: float,
        venue: str | None = None,
        cross_bps: float | None = None,
        taker_poll_sec: float | None = None,
    ) -> Fill | None:
        symbol = f"{base}EUR"
        venue = venue or self.opt.venues[0]
        gw = self._gateway(venue)
        if self.opt.dry_run or gw is None:
            bid, ask = await gw.best_bid_ask(symbol) if gw else (0.0, 0.0)
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
        # Reference price for the "remainder too small" test before any fill;
        # without it a sell of < 5 coins would be judged done before it was sent.
        ref_bid, ref_ask = await gw.best_bid_ask(symbol)
        ref_px = ref_bid if side == "sell" else ref_ask

        async def _settle(state: OrderState) -> None:
            nonlocal filled_qty, filled_cost, fee_eur, remaining_qty, remaining_notional
            if state.filled_qty > 0 and state.avg_price:
                # Buys that charge the fee in the base asset credit fewer coins
                # than the fill size; track the net so later sells don't overshoot.
                credited = state.filled_qty
                if side == "buy" and state.fee_base_qty > 0:
                    credited = max(0.0, state.filled_qty - state.fee_base_qty)
                filled_qty += credited
                filled_cost += credited * state.avg_price
                fee_eur += state.fee_eur
                if remaining_qty is not None:
                    remaining_qty = max(0.0, remaining_qty - state.filled_qty)
                if remaining_notional is not None:
                    remaining_notional = max(
                        0.0, remaining_notional - state.filled_qty * state.avg_price
                    )

        def _done() -> bool:
            if remaining_qty is not None:
                px = filled_cost / filled_qty if filled_qty else ref_px
                return remaining_qty * px < _MIN_ORDER_EUR
            return (remaining_notional or 0.0) < _MIN_ORDER_EUR

        # Maker phase: rest at the touch, re-peg periodically.
        while rest_sec > 0 and self._clock() < deadline and not _done():
            bid, ask = await gw.best_bid_ask(symbol)
            price = bid if side == "buy" else ask
            q = remaining_qty if remaining_qty is not None else (remaining_notional or 0.0) / price
            try:
                state = await gw.place_limit(symbol, side, q, price, post_only=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "momentum desk: maker %s %s@%s rejected: %s", side, symbol, venue, exc
                )
                break
            state = await self._poll(
                gw, state, symbol, min(self.opt.repeg_sec, max(0.0, deadline - self._clock()))
            )
            if state.status == "open":
                state = await self._cancel_and_refetch(gw, state, symbol)
            elif state.status == "closed":
                state = await self._refetch(gw, state, symbol)
            await _settle(state)
            if state.status == "rejected":
                await self._sleep(self.opt.poll_sec)

        # Taker phase: cross the spread once for the remainder (bps may be
        # escalated by ``_sell`` chase rounds).
        if not _done():
            bid, ask = await gw.best_bid_ask(symbol)
            bps = float(self.opt.taker_cross_bps if cross_bps is None else cross_bps)
            cross = bps / 10_000
            price = ask * (1 + cross) if side == "buy" else bid * (1 - cross)
            q = remaining_qty if remaining_qty is not None else (remaining_notional or 0.0) / price
            poll_for = (
                float(taker_poll_sec)
                if taker_poll_sec is not None
                else (float(self.opt.sell_taker_poll_sec) if side == "sell" else 30.0)
            )
            try:
                state = await gw.place_limit(symbol, side, q, price, post_only=False)
                taker_used = True
                state = await self._poll(gw, state, symbol, poll_for)
                if state.status == "open":
                    state = await self._cancel_and_refetch(gw, state, symbol)
                elif state.status == "closed":
                    state = await self._refetch(gw, state, symbol)
                await _settle(state)
            except Exception as exc:  # noqa: BLE001
                logger.warning("momentum desk: taker %s %s@%s failed: %s", side, symbol, venue, exc)

        if filled_qty <= 0:
            return None
        return Fill(
            qty=filled_qty, avg_price=filled_cost / filled_qty, fee_eur=fee_eur, taker=taker_used
        )

    async def _refetch(self, gw: Gateway, state: OrderState, symbol: str) -> OrderState:
        """Authoritative fill/fee figures come from fetch_order, not from the
        create/cancel responses (Bitvavo's cancel reply carries neither)."""
        try:
            return await gw.fetch_order(state.order_id, symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("momentum desk: refetch failed %s: %s", state.order_id, exc)
            return state

    async def _cancel_and_refetch(self, gw: Gateway, state: OrderState, symbol: str) -> OrderState:
        try:
            await gw.cancel_order(state.order_id, symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("momentum desk: cancel failed %s: %s", state.order_id, exc)
        await self._sleep(1.0)
        return await self._refetch(gw, state, symbol)

    async def _poll(
        self, gw: Gateway, state: OrderState, symbol: str, max_sec: float
    ) -> OrderState:
        end = self._clock() + max_sec
        while state.status == "open" and self._clock() < end:
            await self._sleep(self.opt.poll_sec)
            try:
                state = await gw.fetch_order(state.order_id, symbol)
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
        decision_interval_sec=float(
            getattr(settings, "momentum_desk_decision_interval_sec", 0.0)
        ),
        refill_on_exit=bool(getattr(settings, "momentum_desk_refill_on_exit", True)),
        clip_eur=float(getattr(settings, "momentum_desk_clip_eur", 500.0)),
        max_positions=int(getattr(settings, "momentum_desk_max_positions", 3)),
        # WR-pack getattr defaults (must match Settings / live-micro.env).
        trail_pct=float(getattr(settings, "momentum_desk_trail_pct", 0.03)),
        trail_tight_after=float(getattr(settings, "momentum_desk_trail_tight_after", 0.04)),
        trail_tight_pct=float(getattr(settings, "momentum_desk_trail_tight_pct", 0.02)),
        hard_stop_pct=float(getattr(settings, "momentum_desk_hard_stop_pct", 0.03)),
        early_stop_pct=float(getattr(settings, "momentum_desk_early_stop_pct", 0.02)),
        early_stop_until_peak=float(
            getattr(settings, "momentum_desk_early_stop_until_peak", 0.015)
        ),
        time_exit_hours=float(getattr(settings, "momentum_desk_time_exit_hours", 36.0)),
        day_loss_limit_eur=float(getattr(settings, "momentum_desk_day_loss_limit_eur", 100.0)),
        week_loss_limit_eur=float(getattr(settings, "momentum_desk_week_loss_limit_eur", 250.0)),
        macro_caution_mode=str(getattr(settings, "momentum_desk_macro_caution_mode", "ignore")),
        macro_caution_requires_alphai_pick=bool(
            getattr(settings, "momentum_desk_macro_caution_requires_alphai_pick", False)
        ),
        strong_clip_mult=float(getattr(settings, "momentum_desk_strong_clip_mult", 1.3)),
        weak_clip_mult=float(getattr(settings, "momentum_desk_weak_clip_mult", 0.7)),
        skip_weekend_entries=bool(getattr(settings, "momentum_desk_skip_weekend_entries", True)),
        min_excess=float(getattr(settings, "momentum_desk_min_excess", 0.025)),
        entry_fee_buffer_mult=float(getattr(settings, "momentum_desk_entry_fee_buffer_mult", 6.0)),
        max_chase_ret_24h=float(getattr(settings, "momentum_desk_max_chase_ret_24h", 0.0)),
        midflat_hours=float(getattr(settings, "momentum_desk_midflat_hours", 0.0)),
        green_deadline_hours=float(
            getattr(settings, "momentum_desk_green_deadline_hours", 0.0)
        ),
        green_min_peak=float(getattr(settings, "momentum_desk_green_min_peak", 0.01)),
        fade_eta_sec=float(getattr(settings, "momentum_desk_fade_eta_sec", 180.0)),
        fade_confirm_sec=float(getattr(settings, "momentum_desk_fade_confirm_sec", 20.0)),
        fade_smooth_sec=float(getattr(settings, "momentum_desk_fade_smooth_sec", 40.0)),
        fade_min_peak_eur=float(getattr(settings, "momentum_desk_fade_min_peak_eur", 15.0)),
        fade_min_peak_pct=float(getattr(settings, "momentum_desk_fade_min_peak_pct", 0.012)),
        fade_min_giveback_eur=float(
            getattr(settings, "momentum_desk_fade_min_giveback_eur", 5.0)
        ),
        alphai_clip_mult=float(getattr(settings, "momentum_desk_alphai_clip_mult", 1.3)),
        alphai_size_mode=str(
            getattr(settings, "momentum_desk_alphai_size_mode", "conviction")
        ),
        alphai_clip_mult_min=float(
            getattr(settings, "momentum_desk_alphai_clip_mult_min", 1.0)
        ),
        alphai_stale_minutes=float(
            getattr(settings, "momentum_desk_alphai_stale_minutes", 45.0)
        ),
        alphai_price_confirm_sizing=bool(
            getattr(settings, "momentum_desk_alphai_price_confirm_sizing", True)
        ),
        alphai_reliability_sizing=bool(
            getattr(settings, "momentum_desk_alphai_reliability_sizing", True)
        ),
        outcome_size_enabled=bool(
            getattr(settings, "momentum_desk_outcome_learning_enabled", True)
        ),
        soft_regime_on_weak_tape=bool(
            getattr(settings, "momentum_desk_soft_regime_on_weak_tape", True)
        ),
        soft_regime_clip_mult=float(
            getattr(settings, "momentum_desk_soft_regime_clip_mult", 0.5)
        ),
        weak_tape_idle_on_double=bool(
            getattr(settings, "momentum_desk_weak_tape_idle_on_double", True)
        ),
        soft_regime_idle_on_macro_caution=bool(
            getattr(settings, "momentum_desk_soft_regime_idle_on_macro_caution", False)
        ),
        soft_regime_fee_buffer_mult=float(
            getattr(settings, "momentum_desk_soft_regime_fee_buffer_mult", 6.0)
        ),
        book_eur=float(getattr(settings, "momentum_desk_book_eur", 0.0) or 0.0),
    )


def parse_venues(raw: Any) -> tuple[str, ...]:
    """``"bitvavo,okx"`` / ``["bitvavo", "okx"]`` -> deduplicated lowercase tuple."""
    items = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").split(",")
    out: list[str] = []
    for item in items:
        venue = str(item).strip().lower()
        if venue and venue not in out:
            out.append(venue)
    return tuple(out) or ("bitvavo",)


def engine_settings_for_desk(
    settings: Settings, cfg: DeskConfig, venue: str | Sequence[str]
) -> Settings:
    """Policy caps sized to the desk so LiveMicroEngine gates stay meaningful."""
    max_clip = cfg.clip_eur * max_clip_mult(cfg) + 1.0
    venues = parse_venues(venue if isinstance(venue, str) else list(venue))
    return settings.model_copy(
        update={
            "live_micro_symbols": "*",
            "live_micro_venues": ",".join(venues),
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
        self._commit_task: asyncio.Task[None] | None = None
        self._commit: dict[str, Any] = {}
        self._sell_task: asyncio.Task[None] | None = None
        self._manual_exit: dict[str, Any] = {}

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        base: dict[str, Any] = {"running": self.running()}
        if self._runner is not None:
            base.update(self._runner.status())
        if self._commit:
            base["commit"] = dict(self._commit)
        if self._manual_exit:
            base["manual_exit"] = dict(self._manual_exit)
        if self._task is not None and self._task.done() and self._task.exception():
            base["task_error"] = repr(self._task.exception())
        # Operator UI: keep rules HTML in sync with live config on every poll.
        try:
            from bot.live.momentum_dashboard import _rules

            base["ui"] = {"rules_html": _rules(base.get("config") or {})}
        except Exception:  # noqa: BLE001
            logger.exception("momentum desk: rules_html render failed")
        return base

    async def status_fresh(self) -> dict[str, Any]:
        """Status after refreshing open-position marks from the public ticker."""
        if self._runner is not None:
            try:
                await self._runner.refresh_marks()
            except Exception:  # noqa: BLE001
                logger.exception("momentum desk: mark refresh for status failed")
        return self.status()

    async def start(
        self,
        *,
        settings: Settings | None = None,
        dry_run: bool = False,
        venue: str | Sequence[str] = "bitvavo",
    ) -> dict[str, Any]:
        if self.running():
            return {"started": False, "reason": "already_running", "status": self.status()}
        settings = settings or get_settings()
        cfg = desk_config_from_settings(settings)
        venues = parse_venues(venue if isinstance(venue, str) else list(venue))
        gateways: dict[str, Gateway] = {}
        if not dry_run:
            from bot.live.micro_engine import LiveMicroEngine

            engine = LiveMicroEngine(engine_settings_for_desk(settings, cfg, venues))
            armed = engine.arm()
            if not armed.get("armed"):
                return {"started": False, "reason": "arm_failed", "detail": armed}
            for v in venues:
                if engine._registry.get_client(v, enable_trading=True) is None:  # noqa: SLF001
                    logger.warning("momentum desk: no trading credentials for %s; skipped", v)
                    continue
                gateways[v] = LiveGateway(engine, v)
            if not gateways:
                return {"started": False, "reason": "no_venue_credentials", "venues": venues}
            venues = tuple(v for v in venues if v in gateways)
            self._engine = engine
        options = RunnerOptions(
            venues=venues,
            dry_run=dry_run,
            mark_tick_sec=float(
                getattr(settings, "momentum_desk_mark_tick_sec", RunnerOptions.mark_tick_sec)
            ),
            state_path=str(getattr(settings, "momentum_desk_state_path", RunnerOptions.state_path)),
            ledger_path=str(
                getattr(settings, "momentum_desk_ledger_path", RunnerOptions.ledger_path)
            ),
            alphai_recommendations_path=str(
                getattr(settings, "alphai_daily_recommendations_path", None)
                or RunnerOptions.alphai_recommendations_path
            ),
            alphai_pick_outcomes_path=str(
                getattr(settings, "alphai_pick_outcomes_path", None)
                or RunnerOptions.alphai_pick_outcomes_path
            ),
            outcome_learning_path=str(
                getattr(
                    settings,
                    "momentum_desk_outcome_learning_path",
                    RunnerOptions.outcome_learning_path,
                )
            ),
            outcome_learning_enabled=bool(
                getattr(settings, "momentum_desk_outcome_learning_enabled", True)
            ),
            outcome_learning_auto_size=bool(
                getattr(settings, "momentum_desk_outcome_learning_auto_size", True)
            ),
            outcome_min_samples=int(
                getattr(settings, "momentum_desk_outcome_min_samples", 8)
            ),
            outcome_full_samples=int(
                getattr(settings, "momentum_desk_outcome_full_samples", 25)
            ),
            outcome_mult_min=float(
                getattr(settings, "momentum_desk_outcome_mult_min", 0.75)
            ),
            outcome_mult_max=float(
                getattr(settings, "momentum_desk_outcome_mult_max", 1.15)
            ),
        )
        self._runner = MomentumDeskRunner(cfg, None, options=options, gateways=gateways)
        self._stop = False
        self._task = asyncio.create_task(self._runner.run(lambda: self._stop), name="momentum-desk")
        Path(options.state_path).parent.mkdir(parents=True, exist_ok=True)
        _write_flag(
            options.state_path,
            running=True,
            dry_run=dry_run,
            venue=venues[0],
            venues=list(venues),
        )
        return {"started": True, "status": self.status()}

    async def decide(self, *, execute: bool) -> dict[str, Any]:
        if self._runner is None or not self.running():
            return {"ok": False, "reason": "not_running"}
        summary = await self._runner.decide_now(execute=execute)
        return {"ok": True, "decision": summary, "status": self.status()}

    def commit(self, bases: Sequence[str]) -> dict[str, Any]:
        """Execute a previewed decision in the background (orders can rest for
        minutes). Refused while a previous commit is still running."""
        if self._runner is None or not self.running():
            return {"ok": False, "reason": "not_running"}
        if self._commit_task is not None and not self._commit_task.done():
            return {"ok": False, "reason": "commit_in_progress"}
        expect = [b.strip().upper() for b in bases if b and b.strip()]
        runner = self._runner
        self._commit = {
            "started_at": datetime.now(UTC).isoformat(),
            "bases": expect,
            "done": False,
            "result": None,
        }

        async def _run() -> None:
            try:
                res = await runner.decide_now(execute=True, expect_bases=expect)
                self._commit["result"] = {
                    "entries": res.get("entries"),
                    "mismatch": bool(res.get("mismatch")),
                    "planned": [p.get("base") for p in res.get("planned") or []],
                    "risk_block": res.get("risk_block"),
                    "ok": res.get("ok"),
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception("momentum desk: commit failed")
                self._commit["result"] = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._commit["done"] = True
                self._commit["finished_at"] = datetime.now(UTC).isoformat()

        self._commit_task = asyncio.create_task(_run(), name="momentum-commit")
        return {"ok": True, "commit": dict(self._commit)}

    def sell(self, holding_id: str, *, urgent: bool = False) -> dict[str, Any]:
        """Sell one holding in the background (dashboard sell button). A
        patient sell can rest as maker for a minute, so the request returns at
        once and the outcome is surfaced via ``status()['manual_exit']``."""
        if self._runner is None or not self.running():
            return {"ok": False, "reason": "not_running"}
        if self._sell_task is not None and not self._sell_task.done():
            return {"ok": False, "reason": "sell_in_progress"}
        runner = self._runner
        h = next((x for x in runner.holdings if x.holding_id == holding_id), None)
        if h is None:
            return {"ok": False, "reason": "unknown_holding"}
        self._manual_exit = {
            "started_at": datetime.now(UTC).isoformat(),
            "holding_id": holding_id,
            "base": h.pos.base,
            "urgent": bool(urgent),
            "done": False,
            "result": None,
        }

        async def _run() -> None:
            try:
                self._manual_exit["result"] = await runner.sell_now(holding_id, urgent=urgent)
            except Exception as exc:  # noqa: BLE001
                logger.exception("momentum desk: manual sell failed")
                self._manual_exit["result"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._manual_exit["done"] = True
                self._manual_exit["finished_at"] = datetime.now(UTC).isoformat()

        self._sell_task = asyncio.create_task(_run(), name="momentum-sell")
        return {"ok": True, "manual_exit": dict(self._manual_exit)}

    def sell_all(self, *, urgent: bool = False) -> dict[str, Any]:
        """Sell every open holding in the background (dashboard sell-all)."""
        if self._runner is None or not self.running():
            return {"ok": False, "reason": "not_running"}
        if self._sell_task is not None and not self._sell_task.done():
            return {"ok": False, "reason": "sell_in_progress"}
        runner = self._runner
        bases = [h.pos.base for h in runner.holdings]
        if not bases:
            return {"ok": False, "reason": "no_positions"}
        self._manual_exit = {
            "started_at": datetime.now(UTC).isoformat(),
            "holding_id": "*",
            "base": ",".join(bases),
            "bases": bases,
            "urgent": bool(urgent),
            "all": True,
            "done": False,
            "result": None,
        }

        async def _run() -> None:
            try:
                self._manual_exit["result"] = await runner.sell_all_now(urgent=urgent)
            except Exception as exc:  # noqa: BLE001
                logger.exception("momentum desk: sell-all failed")
                self._manual_exit["result"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._manual_exit["done"] = True
                self._manual_exit["finished_at"] = datetime.now(UTC).isoformat()

        self._sell_task = asyncio.create_task(_run(), name="momentum-sell-all")
        return {"ok": True, "manual_exit": dict(self._manual_exit)}

    async def daily_report(self, day: str | None = None) -> dict[str, Any]:
        """Build the missed-entry / exit-opportunity report for one UTC day."""
        from bot.live.momentum_daily_report import build_daily_report, report_as_dict

        if self._runner is None or not self.running():
            return {"ok": False, "reason": "not_running"}
        runner = self._runner
        if day:
            try:
                day_d = datetime.fromisoformat(day).date()
            except ValueError:
                return {"ok": False, "reason": "bad_day"}
        else:
            day_d = datetime.now(UTC).date()
        # Need BTC + universe candles; reuse the live feed.
        bases = ("BTC", *runner.cfg.universe)
        candles: dict[str, list] = {}
        for base in bases:
            try:
                candles[base] = await runner._feed.candles(base, 320)  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001
                logger.warning("daily report: candles %s failed: %s", base, exc)
        # Ledger rows from the flag path.
        path = Path(runner.opt.ledger_path)
        rows: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-800:]:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        report = build_daily_report(
            day=day_d,
            cfg=runner.cfg,
            candles_by_base=candles,
            ledger_rows=rows,
            alphai=runner._alphai_view(),  # noqa: SLF001
            now_ms=int(runner._clock() * 1000),  # noqa: SLF001
        )
        return {"ok": True, "report": report_as_dict(report)}

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
            venue=flag.get("venues") or str(flag.get("venue") or "bitvavo"),
        )


def momentum_desk_flagged_running(settings: Settings | None = None) -> bool:
    """True when the desk should own the book after a process restart."""
    settings = settings or get_settings()
    state_path = str(getattr(settings, "momentum_desk_state_path", RunnerOptions.state_path))
    flag = _read_flag(state_path)
    return bool(flag and flag.get("running"))


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
    "momentum_desk_flagged_running",
    "parse_venues",
    "reset_momentum_desk_manager",
]

"""Bridge PaperRunner's PaperExecutor path to LiveMicroEngine with a capital pocket.

PaperRunner stays paper-only in source. This adapter is wired only by the
full-bot micro session: same strategy → GOE → profitability → risk pipeline,
but marketable fills on allowlisted venues go live within a € pocket that
recycles after sells (not a one-shot spend counter). Maker/post-only quotes
stay paper unless live_maker is enabled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderSide, OrderStatus, OrderType
from bot.core.models import ExecutionResult, OrderRequest
from bot.execution.paper_executor import PaperExecutor
from bot.live.micro_engine import LiveMicroEngine
from bot.portfolio.models import Fill, Order
from bot.portfolio.portfolio import PaperPortfolio
from bot.portfolio.venue_ledger import infer_base_asset

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_MIN_LIVE_NOTIONAL = Decimal("5")
_FILL_POLL_SECONDS = 2.5
_FILL_POLL_INTERVAL = 0.3
_DEFAULT_RESTING_MAX_AGE_SEC = 90.0
_QUOTE_FEE_CURRENCIES = frozenset({"EUR", "USDT", "USDC", "USD"})


def _buy_lot_qty_and_unit(
    *,
    amount: Decimal,
    price: Decimal,
    fee_amt: Decimal,
    fee_cur: str,
    base: str,
    quote: str,
) -> tuple[Decimal, Decimal]:
    """Fee-aware buy lot: (lot_qty, unit_cost in quote).

    OKX often charges maker fees in the base asset. Treating those as quote
    understates EUR cost and can authorize a false "profitable" sell.
    """
    if amount <= 0 or price <= 0:
        return amount, price
    fee_amt = max(_ZERO, Decimal(str(fee_amt or 0)))
    fee_cur_u = str(fee_cur or "").strip().upper()
    base_u = str(base or "").strip().upper()
    quote_u = str(quote or "").strip().upper()
    notional = amount * price
    if fee_amt <= 0:
        return amount, price

    quote_aliases = _QUOTE_FEE_CURRENCIES | ({quote_u} if quote_u else set())
    treat_as_base = fee_cur_u == base_u
    if not treat_as_base and fee_cur_u not in quote_aliases:
        # Missing/unknown fee currency: if fee looks too large vs quote-fee
        # expectations but small vs base size, treat as base (OKX pattern).
        quote_fee_floor = notional * Decimal("0.0005")
        if (
            fee_amt >= quote_fee_floor * 2
            and fee_amt / amount <= Decimal("0.01")
            and fee_amt < notional
        ):
            treat_as_base = True

    if treat_as_base:
        received = amount - fee_amt
        if received <= 0:
            return amount, (notional / amount) if amount > 0 else price
        return received, notional / received

    return amount, (notional + fee_amt) / amount


class MicroBudgetLiveExecutor(PaperExecutor):
    """PaperExecutor subclass: live taker fills + paper maker, € capital pocket."""

    name = "micro_budget_live"

    def __init__(
        self,
        settings: Settings,
        *,
        portfolio: PaperPortfolio,
        live_engine: LiveMicroEngine,
        budget_eur: Decimal,
        execute_venues: set[str] | None = None,
        exclude_bases: set[str] | None = None,
        live_maker: bool = False,
        allowed_bases: set[str] | None = None,
    ) -> None:
        super().__init__(settings, portfolio=portfolio)
        self._live = live_engine
        self._budget = Decimal(str(budget_eur))
        self._turnover = _ZERO  # lifetime traded notional (stats only)
        self._execute_venues = {
            v.strip().lower() for v in (execute_venues or {"bitvavo"}) if v.strip()
        }
        self._exclude_bases = {
            b.strip().upper() for b in (exclude_bases or {"BTC"}) if b.strip()
        }
        self._allowed_bases = (
            {b.strip().upper() for b in allowed_bases if b and str(b).strip()}
            if allowed_bases is not None
            else None
        )
        self._live_maker = bool(live_maker)
        self.skips: dict[str, int] = {}
        self.live_trades: list[dict[str, Any]] = []
        self._last_sync: dict[str, Any] | None = None
        self._resting: list[dict[str, Any]] = []
        self.live_fill_count = 0
        self.live_transaction_count = 0  # legacy alias of session counters
        self.session_live_fill_count = 0
        self.session_live_transaction_count = 0
        self.backfill_mirrored_count = 0
        self.realized_trade_pnl_eur = _ZERO  # closed-trade PnL after fees
        self._persist_path = Path(
            str(
                getattr(
                    settings,
                    "live_micro_bridge_persist_path",
                    "./data/live_micro_bridge_state.json",
                )
            )
        )
        self._long_hold_bases = {
            b.strip().upper()
            for b in str(
                getattr(settings, "live_micro_long_hold_bases", "ETH") or "ETH"
            ).split(",")
            if b.strip()
        }
        self.portfolio_value_eur: Decimal | None = None
        self.starting_portfolio_eur: Decimal | None = None
        # FIFO lots for realized PnL: base -> [(qty, unit_cost_eur)]
        self._cost_lots: dict[str, list[list[Decimal]]] = {}
        self._lots_seeded_venues: set[str] = set()
        # Only session fills / exchange trade history count as trusted cost basis.
        # Mark-seeded lots must not authorize a sell (would allow selling below true buy).
        self._trusted_cost_keys: set[str] = set()
        self._mirrored_trade_ids: set[str] = set()
        self._exit_cooldown_mono: dict[str, float] = {}
        self._session_started_ms: float | None = None
        self._resting_max_age_sec = float(
            getattr(settings, "live_micro_resting_max_age_sec", _DEFAULT_RESTING_MAX_AGE_SEC)
            or _DEFAULT_RESTING_MAX_AGE_SEC
        )
        self._bal_cache: dict[str, list[Any]] = {}
        self._bal_cache_mono: dict[str, float] = {}
        self._bal_cache_sec = 2.5
        self._venue_raw_balances: dict[str, list[Any]] = {}
        self._last_sync_by_venue: dict[str, dict[str, Any]] = {}
        self._mark_fetched_at: dict[str, float] = {}
        self._mark_ttl_sec = 30.0
        self._last_orphan_sweep_mono = 0.0
        self._orphan_sweep_sec = 60.0
        # Trailing take-profit (soft/hard + ATR) on session buys.
        from bot.live.trail_policy import MarkSeries, parse_corr_group

        self._trail: dict[str, dict[str, Any]] = {}
        self._session_lots: dict[str, list[list[Decimal]]] = {}
        self._mark_series: dict[str, MarkSeries] = {}
        self._alerts: list[dict[str, Any]] = []
        self._trail_enabled = bool(
            getattr(settings, "paper_trail_take_profit_enabled", False)
        )
        self._trail_session_only = bool(
            getattr(settings, "paper_trail_session_buys_only", True)
        )
        self._soft_arm_floor = Decimal(
            str(getattr(settings, "paper_trail_soft_arm_pct", 0.12) or 0.12)
        )
        self._soft_dd_floor = Decimal(
            str(getattr(settings, "paper_trail_soft_drawdown_pct", 0.08) or 0.08)
        )
        self._soft_partial = Decimal(
            str(getattr(settings, "paper_trail_soft_partial_pct", 0.25) or 0.25)
        )
        self._hard_arm_floor = Decimal(
            str(
                getattr(settings, "paper_trail_hard_arm_pct", None)
                or getattr(settings, "paper_trail_arm_gain_pct", 0.30)
                or 0.30
            )
        )
        self._hard_dd_floor = Decimal(
            str(
                getattr(settings, "paper_trail_hard_drawdown_pct", None)
                or getattr(settings, "paper_trail_drawdown_pct", 0.12)
                or 0.12
            )
        )
        self._hard_partial = Decimal(
            str(getattr(settings, "paper_trail_hard_partial_pct", 0.25) or 0.25)
        )
        # Legacy aliases used by snapshot / older tests.
        self._trail_arm_gain = self._hard_arm_floor
        self._trail_drawdown = self._hard_dd_floor
        self._trail_partial_enabled = True
        self._trail_partial_pct = self._soft_partial
        self._atr_enabled = bool(getattr(settings, "paper_trail_atr_enabled", True))
        self._atr_samples = int(getattr(settings, "paper_trail_atr_samples", 48) or 48)
        self._atr_arm_mult = Decimal(
            str(getattr(settings, "paper_trail_atr_arm_mult", 2.5) or 2.5)
        )
        self._atr_dd_mult = Decimal(
            str(getattr(settings, "paper_trail_atr_dd_mult", 1.0) or 1.0)
        )
        self._ladder_enabled = bool(
            getattr(settings, "paper_ladder_buy_enabled", False)
        )
        raw_ladder = str(
            getattr(settings, "paper_ladder_buy_pcts", "0.01,0.02,0.03") or ""
        )
        self._ladder_pcts: list[Decimal] = []
        for part in raw_ladder.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                self._ladder_pcts.append(Decimal(part))
            except Exception:  # noqa: BLE001
                continue
        if not self._ladder_pcts:
            self._ladder_pcts = [Decimal("0.01"), Decimal("0.02"), Decimal("0.03")]
        self._time_stop_enabled = bool(
            getattr(settings, "paper_time_stop_enabled", False)
        )
        self._time_stop_sec = float(
            getattr(settings, "paper_time_stop_sec", 86400) or 86400
        )
        self._dust_policy = str(
            getattr(settings, "paper_dust_policy", "off") or "off"
        ).strip().lower()
        self._dust_exit_slack = Decimal(
            str(getattr(settings, "paper_dust_exit_slack_bps", 0) or 0)
        ) / Decimal("10000")
        self._regime_block_buys = bool(
            getattr(settings, "paper_regime_block_buys", True)
        )
        self._buys_blocked = False
        self._daily_kill_active = False
        self._daily_kill_eur = Decimal(
            str(getattr(settings, "paper_daily_kill_eur", 50) or 50)
        )
        self._alert_pct_to_arm = Decimal(
            str(getattr(settings, "paper_alert_pct_to_arm", 0.05) or 0.05)
        )
        self._momentum_enabled = bool(
            getattr(settings, "paper_buy_momentum_enabled", False)
        )
        self._momentum_min = Decimal(
            str(getattr(settings, "paper_buy_momentum_min_return", 0) or 0)
        )
        self._momentum_samples = int(
            getattr(settings, "paper_buy_momentum_samples", 12) or 12
        )
        self._corr_group = parse_corr_group(
            str(getattr(settings, "live_micro_corr_group", "") or "")
        )
        self._max_per_corr = int(
            getattr(settings, "live_micro_max_per_corr_group", 2) or 2
        )
        self._position_opened_mono: dict[str, float] = {}
        self._position_opened_at: dict[str, float] = {}
        self._max_alt_bases = int(
            getattr(settings, "live_micro_max_alt_bases", 0) or 0
        )
        self._MarkSeries = MarkSeries
        self._try_load_persisted_state()

    def _is_long_hold(self, base: str) -> bool:
        return str(base or "").strip().upper() in self._long_hold_bases

    def _balance_qty(self, venue: str, base: str) -> Decimal:
        venue_l = venue.strip().lower()
        base_u = str(base or "").strip().upper()
        for bal in self._venue_raw_balances.get(venue_l) or []:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if asset != base_u:
                continue
            return Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                str(getattr(bal, "locked", 0) or 0)
            )
        sync = self._last_sync_by_venue.get(venue_l) or {}
        ledger = sync.get("ledger") or {}
        if base_u in ledger:
            try:
                return Decimal(str(ledger.get(base_u) or 0))
            except Exception:  # noqa: BLE001
                pass
        mapped = sync.get("balances") or {}
        if base_u in mapped:
            try:
                return Decimal(str(mapped.get(base_u) or 0))
            except Exception:  # noqa: BLE001
                pass
        bal = self._portfolio.state.balances.get(base_u)
        if bal is not None and bal.total > 0:
            return Decimal(str(bal.total))
        return _ZERO

    def _blocked_sells_session(self) -> int:
        return int(self.skips.get("sell_below_break_even", 0) or 0) + int(
            self.skips.get("time_stop_below_be", 0) or 0
        )

    def _mtm_summary(self) -> dict[str, str]:
        unrealized = _ZERO
        locked = _ZERO
        micro_locked = _ZERO
        long_hold_locked = _ZERO
        seen: set[str] = set()
        for trail_key, st in self._trail.items():
            try:
                cost = Decimal(str(st.get("cost") or 0))
                mark = Decimal(str(st.get("last_mark") or 0))
            except Exception:  # noqa: BLE001
                continue
            if cost <= 0 or mark <= 0:
                continue
            venue = str(st.get("venue") or trail_key.split(":", 1)[0])
            base = str(st.get("base") or trail_key.split(":", 1)[-1])
            qty = self._balance_qty(venue, base)
            if qty <= 0:
                qty = Decimal(str(st.get("session_qty") or 0))
            if qty <= 0:
                continue
            seen.add(trail_key)
            notional = qty * mark
            locked += notional
            unrealized += (mark - cost) * qty
            if self._is_long_hold(base):
                long_hold_locked += notional
            else:
                micro_locked += notional
        for venue in sorted(self._execute_venues):
            for bal in self._venue_raw_balances.get(venue) or []:
                asset = str(getattr(bal, "asset", "") or "").upper()
                if not asset or asset == self._quote or asset in self._exclude_bases:
                    continue
                if not self._is_long_hold(asset):
                    continue
                trail_key = self._lots_key(venue, asset)
                if trail_key in seen:
                    continue
                qty = self._balance_qty(venue, asset)
                if qty <= 0:
                    continue
                symbol = f"{asset}{self._quote}"
                mark = Decimal(
                    str(
                        self._portfolio.state.mark_prices.get(symbol)
                        or self._unit_cost(venue, asset)
                        or 0
                    )
                )
                cost = self._unit_cost(venue, asset) or mark
                if mark <= 0:
                    continue
                notional = qty * mark
                locked += notional
                long_hold_locked += notional
                if cost > 0:
                    unrealized += (mark - cost) * qty
        return {
            "unrealized_mtm_eur": str(unrealized.quantize(Decimal("0.01"))),
            "locked_notional_eur": str(locked.quantize(Decimal("0.01"))),
            "micro_locked_notional_eur": str(micro_locked.quantize(Decimal("0.01"))),
            "long_hold_notional_eur": str(long_hold_locked.quantize(Decimal("0.01"))),
            "blocked_sells_session": str(self._blocked_sells_session()),
        }

    def _serialize_lots(
        self, lots: dict[str, list[list[Decimal]]]
    ) -> dict[str, list[list[str]]]:
        out: dict[str, list[list[str]]] = {}
        for key, rows in lots.items():
            out[key] = [[str(qty), str(unit)] for qty, unit in rows]
        return out

    def _deserialize_lots(
        self, raw: dict[str, list[list[str]]] | None
    ) -> dict[str, list[list[Decimal]]]:
        out: dict[str, list[list[Decimal]]] = {}
        for key, rows in (raw or {}).items():
            parsed: list[list[Decimal]] = []
            for row in rows or []:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                parsed.append([Decimal(str(row[0])), Decimal(str(row[1]))])
            if parsed:
                out[str(key)] = parsed
        return out

    def export_runtime_state(self) -> dict[str, Any]:
        resting: list[dict[str, Any]] = []
        for row in self._resting:
            opp = row.get("opportunity_id")
            resting.append(
                {
                    **{k: v for k, v in row.items() if k != "opportunity_id"},
                    "opportunity_id": str(opp) if opp is not None else None,
                    "quantity": str(row.get("quantity") or 0),
                    "price": str(row.get("price") or 0),
                    "placed_at": float(
                        row.get("placed_at") or row.get("placed_mono") or time.time()
                    ),
                }
            )
        return {
            "version": 1,
            "saved_at": time.time(),
            "session_started_ms": self._session_started_ms,
            "trail": self._trail,
            "resting": resting,
            "mirrored_trade_ids": sorted(self._mirrored_trade_ids),
            "session_lots": self._serialize_lots(self._session_lots),
            "position_opened_at": dict(self._position_opened_at),
            "skips": dict(self.skips),
            "session_live_fill_count": int(self.session_live_fill_count),
            "session_live_transaction_count": int(self.session_live_transaction_count),
            "backfill_mirrored_count": int(self.backfill_mirrored_count),
            "live_fill_count": int(self.live_fill_count),
            "live_transaction_count": int(self.live_transaction_count),
            "realized_trade_pnl_eur": str(self.realized_trade_pnl_eur),
        }

    def _try_load_persisted_state(self) -> bool:
        return self.load_persisted_state(self._persist_path)

    def load_persisted_state(self, path: Path | str | None = None) -> bool:
        p = Path(path) if path is not None else self._persist_path
        if not p.exists():
            return False
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("micro bridge persist load failed path=%s err=%s", p, exc)
            return False
        if not isinstance(raw, dict):
            return False
        self._trail = {
            str(k): (v if isinstance(v, dict) else {})
            for k, v in (raw.get("trail") or {}).items()
        }
        self._resting = []
        for row in raw.get("resting") or []:
            if not isinstance(row, dict):
                continue
            self._resting.append(
                {
                    **row,
                    "quantity": Decimal(str(row.get("quantity") or 0)),
                    "price": Decimal(str(row.get("price") or 0)),
                    "placed_mono": time.monotonic(),
                }
            )
        self._mirrored_trade_ids = {
            str(x) for x in (raw.get("mirrored_trade_ids") or []) if str(x)
        }
        self._session_lots = self._deserialize_lots(raw.get("session_lots"))
        self._position_opened_at = {
            str(k): float(v)
            for k, v in (raw.get("position_opened_at") or {}).items()
            if v is not None
        }
        now = time.time()
        self._position_opened_mono = {
            key: time.monotonic() - max(0.0, now - opened)
            for key, opened in self._position_opened_at.items()
        }
        self.skips = {
            str(k): int(v)
            for k, v in (raw.get("skips") or {}).items()
            if str(k)
        }
        self.session_live_fill_count = int(raw.get("session_live_fill_count") or 0)
        self.session_live_transaction_count = int(
            raw.get("session_live_transaction_count") or 0
        )
        self.backfill_mirrored_count = int(raw.get("backfill_mirrored_count") or 0)
        self.live_fill_count = self.session_live_fill_count
        self.live_transaction_count = self.session_live_transaction_count
        try:
            self.realized_trade_pnl_eur = Decimal(
                str(raw.get("realized_trade_pnl_eur") or 0)
            )
        except Exception:  # noqa: BLE001
            self.realized_trade_pnl_eur = _ZERO
        if raw.get("session_started_ms") is not None:
            self._session_started_ms = float(raw.get("session_started_ms"))
        logger.info(
            "micro bridge state loaded path=%s trail=%s resting=%s session_fills=%s",
            p,
            len(self._trail),
            len(self._resting),
            self.session_live_transaction_count,
        )
        return True

    def persist_runtime_state(self) -> None:
        path = self._persist_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self.export_runtime_state(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp.replace(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("micro bridge persist failed path=%s err=%s", path, exc)

    def set_buys_blocked(self, blocked: bool) -> None:
        """Regime guard: when True, reject new BUY orders (sells/trails still run)."""
        self._buys_blocked = bool(blocked) or self._daily_kill_active

    def _push_alert(self, kind: str, message: str, **extra: Any) -> None:
        base = str(extra.get("base") or "")
        # Dedupe noisy near-arm / same-kind alerts within 5 minutes.
        now = time.time()
        for prev in reversed(self._alerts[-20:]):
            if (
                prev.get("kind") == kind
                and str(prev.get("base") or "") == base
                and now - float(prev.get("ts") or 0) < 300
            ):
                return
        row = {
            "ts": now,
            "kind": kind,
            "message": message,
            **extra,
        }
        self._alerts.append(row)
        if len(self._alerts) > 50:
            self._alerts = self._alerts[-50:]
        logger.warning("MICRO_ALERT kind=%s %s", kind, message)

    def _check_daily_kill(self) -> None:
        if self._daily_kill_eur <= 0:
            return
        if self.realized_trade_pnl_eur <= -self._daily_kill_eur:
            if not self._daily_kill_active:
                self._daily_kill_active = True
                self._buys_blocked = True
                self._push_alert(
                    "daily_kill",
                    f"realized PnL {self.realized_trade_pnl_eur} <= -{self._daily_kill_eur}; buys blocked",
                )

    def _invalidate_bal_cache(self, venue: str | None = None) -> None:
        if venue is None:
            self._bal_cache.clear()
            self._bal_cache_mono.clear()
            return
        key = venue.strip().lower()
        self._bal_cache.pop(key, None)
        self._bal_cache_mono.pop(key, None)

    @staticmethod
    def _lots_key(venue: str, base: str) -> str:
        return f"{venue.strip().lower()}:{base.upper()}"

    def _venue_budget_remaining(self, venue: str) -> Decimal:
        """Per-venue deployable EUR — each exchange gets its own pocket cap."""
        key = venue.strip().lower()
        ledger = self._portfolio.venue_ledger
        live_eur = _ZERO
        if ledger is not None:
            live_eur = ledger.available(key, self._quote)
        if live_eur <= 0:
            live_eur = self._live_free_sync(key, self._quote)
        if live_eur <= 0 and len(self._execute_venues) == 1:
            live_eur = self.free_quote_eur
        if live_eur > 0:
            return min(live_eur, self._budget)
        return _ZERO

    def _live_free_sync(self, venue: str, asset: str) -> Decimal:
        """Sync read of cached venue balances (no await)."""
        key = asset.upper()
        for bal in self._bal_cache.get(venue.strip().lower(), []):
            if str(getattr(bal, "asset", "")).upper() == key:
                return Decimal(str(getattr(bal, "free", 0) or 0))
        return _ZERO

    def _rebuild_aggregate_from_venues(self) -> dict[str, str]:
        """Merge all cached venue balances into the paper pocket."""
        from bot.core.models import Balance

        venue_maps: dict[str, list[Balance]] = {}
        for v, raw in self._venue_raw_balances.items():
            venue_maps[v] = [
                Balance(
                    asset=str(getattr(b, "asset", "") or ""),
                    free=Decimal(str(getattr(b, "free", 0) or 0)),
                    locked=Decimal(str(getattr(b, "locked", 0) or 0)),
                )
                for b in raw
            ]
        if not venue_maps:
            return {}
        return self._portfolio.sync_live_balances_from_venues(
            venue_maps,
            quote_available_cap=self._budget,
            allowed_bases=self._allowed_bases,
            exclude_bases=self._exclude_bases,
        )

    @property
    def free_quote_eur(self) -> Decimal:
        """Available EUR cash in the micro pocket (recycles after sells)."""
        try:
            return Decimal(str(self._portfolio.available(self._quote)))
        except Exception:  # noqa: BLE001
            return _ZERO

    @property
    def budget_remaining(self) -> Decimal:
        """Capital still free to deploy on buys — sum of per-venue pockets."""
        if len(self._execute_venues) > 1:
            total = sum(
                (self._venue_budget_remaining(v) for v in self._execute_venues),
                _ZERO,
            )
            return total
        free = self.free_quote_eur
        if free < 0:
            return _ZERO
        return free if free <= self._budget else self._budget

    def snapshot_bridge(self) -> dict[str, Any]:
        return {
            "budget_eur": str(self._budget),
            "free_quote_eur": str(self.free_quote_eur),
            "remaining_eur": str(self.budget_remaining),
            "turnover_eur": str(self._turnover),
            "portfolio_value_eur": (
                str(self.portfolio_value_eur) if self.portfolio_value_eur is not None else None
            ),
            "starting_portfolio_eur": (
                str(self.starting_portfolio_eur)
                if self.starting_portfolio_eur is not None
                else None
            ),
            # Operator PnL = realized trade profit after fees (not mark-to-market).
            "netto_winst_eur": str(self.realized_trade_pnl_eur),
            "realized_trade_pnl_eur": str(self.realized_trade_pnl_eur),
            "execute_venues": sorted(self._execute_venues),
            "exclude_bases": sorted(self._exclude_bases),
            "live_maker": self._live_maker,
            "skips": dict(self.skips),
            "live_trade_count": len(self.live_trades),
            "live_fill_count": int(self.session_live_fill_count),
            "live_transaction_count": int(self.session_live_transaction_count),
            "session_live_fill_count": int(self.session_live_fill_count),
            "session_live_transaction_count": int(self.session_live_transaction_count),
            "backfill_mirrored_count": int(self.backfill_mirrored_count),
            "resting_orders": len(self._resting),
            "long_hold_bases": sorted(self._long_hold_bases),
            **self._mtm_summary(),
            "capital_model": "pocket",
            "trail_take_profit": {
                "enabled": self._trail_enabled,
                "session_buys_only": self._trail_session_only,
                "soft_arm_pct": str(self._soft_arm_floor),
                "hard_arm_pct": str(self._hard_arm_floor),
                "arm_gain_pct": str(self._hard_arm_floor),
                "drawdown_pct": str(self._hard_dd_floor),
                "partial_enabled": True,
                "partial_pct": str(self._soft_partial),
                "atr_enabled": self._atr_enabled,
                "time_stop_sec": self._time_stop_sec if self._time_stop_enabled else None,
                "ladder_buy": self._ladder_enabled,
                "buys_blocked": self._buys_blocked,
                "daily_kill_active": self._daily_kill_active,
                "dust_policy": self._dust_policy,
                "momentum_enabled": self._momentum_enabled,
                "corr_group": sorted(self._corr_group),
                "max_per_corr_group": self._max_per_corr,
                "states": self._trail_states_public(),
                "alerts": list(self._alerts[-10:]),
            },
            "alerts": list(self._alerts[-10:]),
            "max_alt_bases": self._max_alt_bases,
            "held_alt_bases": sorted(self._held_alt_bases()),
            "last_sync": self._last_sync,
            "last_sync_by_venue": dict(self._last_sync_by_venue),
            "diagnostics": {
                "realized_net_pnl_eur": str(self.realized_trade_pnl_eur),
                "live_fills": int(self.session_live_fill_count),
                "live_transactions": int(self.session_live_transaction_count),
                "session_live_fills": int(self.session_live_fill_count),
                "session_live_transactions": int(self.session_live_transaction_count),
                "backfill_mirrored": int(self.backfill_mirrored_count),
                **self._mtm_summary(),
                "recent_live_trades": list(self.live_trades[-12:]),
                "skip_leaders": sorted(
                    ((k, v) for k, v in self.skips.items()),
                    key=lambda kv: kv[1],
                    reverse=True,
                )[:12],
                "why_idle": self._why_idle_hints(),
            },
        }

    def _why_idle_hints(self) -> list[str]:
        """Operator-facing blockers — ordered by severity / current truth."""
        hints: list[str] = []
        held = self._held_alt_bases()

        ks = getattr(self, "_kill_switch", None)
        if ks is not None:
            try:
                state = getattr(getattr(ks, "state", None), "value", None) or str(
                    getattr(ks, "state", "")
                )
                if str(state).lower() in {"paused", "emergency_stop"}:
                    reason = getattr(ks, "reason", None) or "unknown"
                    hints.append(
                        f"RISK_KILL_SWITCH_{str(state).upper()}: {reason}"
                    )
            except Exception:  # noqa: BLE001
                pass

        if self._daily_kill_active:
            hints.append("DAILY_KILL")
        if self._buys_blocked:
            hints.append("BUYS_BLOCKED_REGIME")

        resting_n = len(self._resting)
        if resting_n > 0:
            by_venue: dict[str, int] = {}
            for row in self._resting:
                v = str(row.get("venue") or "?").lower()
                by_venue[v] = by_venue.get(v, 0) + 1
            venue_bits = ",".join(f"{v}={n}" for v, n in sorted(by_venue.items()))
            hints.append(f"RESTING_ORDERS n={resting_n} ({venue_bits})")

        # Current underwater bags (not just lifetime skip counts).
        underwater: list[str] = []
        long_hold: list[str] = []
        waiting_arm: list[str] = []
        for trail_key, st in self._trail.items():
            try:
                cost = Decimal(str(st.get("cost") or 0))
                mark = Decimal(str(st.get("last_mark") or 0))
            except Exception:  # noqa: BLE001
                continue
            if cost <= 0 or mark <= 0:
                continue
            base = str(st.get("base") or trail_key.split(":", 1)[-1])
            gain = (mark - cost) / cost
            qty = self._balance_qty(
                str(st.get("venue") or trail_key.split(":", 1)[0]), base
            )
            notional = qty * mark if qty > 0 and mark > 0 else _ZERO
            notional_bit = f"€{float(notional):.0f}" if notional > 0 else ""
            label = f"{trail_key}:{float(gain * 100):+.2f}%"
            if notional_bit:
                label += f"({notional_bit})"
            if self._is_long_hold(base):
                long_hold.append(label)
                continue
            if gain < 0:
                underwater.append(label)
            elif not st.get("soft_armed"):
                soft = Decimal(str(st.get("soft_arm") or self._soft_arm_floor))
                need = soft - gain
                if need > 0:
                    waiting_arm.append(
                        f"{trail_key}:need+{float(need * 100):.2f}%"
                    )
        if long_hold:
            hints.append(
                "LONG_HOLD_OUTSIDE_MICRO "
                + ", ".join(long_hold[:6])
                + ("…" if len(long_hold) > 6 else "")
            )
        if underwater:
            hints.append(
                "HOLDING_BELOW_COST "
                + ", ".join(underwater[:8])
                + ("…" if len(underwater) > 8 else "")
            )
        be_skips = int(self.skips.get("sell_below_break_even", 0) or 0)
        ts_skips = int(self.skips.get("time_stop_below_be", 0) or 0)
        if be_skips or ts_skips:
            hints.append(
                f"SELLS_BLOCKED_NEVER_LOSS sell_be={be_skips} time_stop_be={ts_skips}"
            )
        mtm = self._mtm_summary()
        if Decimal(str(mtm.get("micro_locked_notional_eur") or 0)) > 0:
            hints.append(
                "MICRO_CAPITAL_LOCKED "
                f"micro=€{mtm.get('micro_locked_notional_eur')} "
                f"long_hold=€{mtm.get('long_hold_notional_eur')}"
            )
        if waiting_arm:
            hints.append(
                "WAITING_SOFT_ARM "
                + ", ".join(waiting_arm[:6])
                + ("…" if len(waiting_arm) > 6 else "")
            )

        if self._max_alt_bases > 0 and len(held) > self._max_alt_bases:
            hints.append(
                f"OVER_MAX_ALT_BASES held={sorted(held)} max={self._max_alt_bases}"
            )
        elif self._max_alt_bases > 0 and len(held) >= self._max_alt_bases:
            hints.append(
                f"AT_MAX_ALT_BASES held={sorted(held)} (adds to existing only)"
            )

        if self.skips.get("fees_eat_edge", 0) > 0:
            hints.append(f"FEES_EAT_EDGE n={self.skips.get('fees_eat_edge')}")
        if self.skips.get("momentum_block", 0) > 0:
            hints.append(f"MOMENTUM_BLOCK n={self.skips.get('momentum_block')}")
        if self.skips.get("corr_group_cap", 0) > 0:
            hints.append(f"CORR_GROUP_CAP n={self.skips.get('corr_group_cap')}")
        if self.skips.get("policy_blocked", 0) > 0:
            hints.append(f"POLICY_BLOCKED n={self.skips.get('policy_blocked')}")
        if self.skips.get("execution_error", 0) > 0:
            hints.append(f"EXECUTION_ERROR n={self.skips.get('execution_error')}")
        if self.skips.get("budget_exhausted", 0) > 0:
            hints.append(f"BUDGET_EXHAUSTED n={self.skips.get('budget_exhausted')}")

        # Per-venue free cash so OKX under-deployment is visible.
        venue_cash: list[str] = []
        for venue in sorted(self._execute_venues):
            try:
                rem = self._venue_budget_remaining(venue)
            except Exception:  # noqa: BLE001
                rem = None
            if rem is not None:
                venue_cash.append(f"{venue}=€{float(rem):.0f}")
        if venue_cash:
            hints.append("VENUE_CASH " + " ".join(venue_cash))

        if not hints:
            hints.append("SCANNING_NO_PASSING_EDGE")
        return hints

    def _trail_states_public(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for trail_key, st in sorted(self._trail.items()):
            cost = Decimal(str(st.get("cost") or 0))
            mark = Decimal(str(st.get("last_mark") or 0))
            gain = ((mark - cost) / cost) if cost > 0 and mark > 0 else _ZERO
            soft_arm = Decimal(str(st.get("soft_arm") or self._soft_arm_floor))
            hard_arm = Decimal(str(st.get("hard_arm") or self._hard_arm_floor))
            next_arm = soft_arm if not st.get("soft_armed") else hard_arm
            to_arm = next_arm - gain
            venue = str(st.get("venue") or trail_key.split(":", 1)[0])
            base = str(st.get("base") or trail_key.split(":", 1)[-1])
            opened = self._position_opened_mono.get(trail_key)
            opened_at = self._position_opened_at.get(trail_key)
            if opened_at:
                age = max(0.0, time.time() - opened_at)
            elif opened is not None:
                age = time.monotonic() - opened
            else:
                age = None
            qty = self._balance_qty(venue, base)
            if qty <= 0:
                qty = Decimal(str(st.get("session_qty") or 0))
            notional = (qty * mark) if qty > 0 and mark > 0 else _ZERO
            unrealized = (mark - cost) * qty if qty > 0 and cost > 0 and mark > 0 else _ZERO
            role = "long_hold" if self._is_long_hold(base) else "micro_recycle"
            out[trail_key] = {
                "venue": venue,
                "base": base,
                "role": role,
                "armed": bool(st.get("soft_armed") or st.get("hard_armed")),
                "soft_armed": bool(st.get("soft_armed")),
                "hard_armed": bool(st.get("hard_armed")),
                "partial_done": bool(st.get("soft_partial_done")),
                "hard_partial_done": bool(st.get("hard_partial_done")),
                "peak": str(st.get("peak") or ""),
                "cost": str(cost) if cost > 0 else "",
                "mark": str(mark) if mark > 0 else "",
                "qty": str(qty) if qty > 0 else "",
                "notional_eur": str(notional.quantize(Decimal("0.01"))) if notional > 0 else "",
                "unrealized_eur": str(unrealized.quantize(Decimal("0.01"))) if qty > 0 else "",
                "gain_pct": f"{float(gain * 100):.2f}",
                "pct_to_arm": f"{float(to_arm * 100):.2f}",
                "soft_arm_pct": f"{float(soft_arm * 100):.2f}",
                "hard_arm_pct": f"{float(hard_arm * 100):.2f}",
                "atr_pct": str(st.get("atr") or ""),
                "session_qty": str(st.get("session_qty") or ""),
                "triggered": bool(st.get("triggered")),
                "age_sec": round(age, 1) if age is not None else None,
            }
        for venue in sorted(self._execute_venues):
            for bal in self._venue_raw_balances.get(venue) or []:
                asset = str(getattr(bal, "asset", "") or "").upper()
                if not asset or asset == self._quote or asset in self._exclude_bases:
                    continue
                if not self._is_long_hold(asset):
                    continue
                trail_key = self._lots_key(venue, asset)
                if trail_key in out:
                    continue
                qty = self._balance_qty(venue, asset)
                if qty <= 0:
                    continue
                symbol = f"{asset}{self._quote}"
                mark = Decimal(
                    str(self._portfolio.state.mark_prices.get(symbol) or 0)
                )
                cost = self._unit_cost(venue, asset) or mark
                if mark <= 0:
                    continue
                gain = ((mark - cost) / cost) if cost > 0 else _ZERO
                notional = qty * mark
                unrealized = (mark - cost) * qty if cost > 0 else _ZERO
                opened_at = self._position_opened_at.get(trail_key)
                age = max(0.0, time.time() - opened_at) if opened_at else None
                out[trail_key] = {
                    "venue": venue,
                    "base": asset,
                    "role": "long_hold",
                    "armed": False,
                    "soft_armed": False,
                    "hard_armed": False,
                    "partial_done": False,
                    "hard_partial_done": False,
                    "peak": "",
                    "cost": str(cost) if cost > 0 else "",
                    "mark": str(mark),
                    "qty": str(qty),
                    "notional_eur": str(notional.quantize(Decimal("0.01"))),
                    "unrealized_eur": str(unrealized.quantize(Decimal("0.01"))),
                    "gain_pct": f"{float(gain * 100):.2f}",
                    "pct_to_arm": "—",
                    "soft_arm_pct": "—",
                    "hard_arm_pct": "—",
                    "atr_pct": "",
                    "session_qty": "0",
                    "triggered": False,
                    "age_sec": round(age, 1) if age is not None else None,
                }
        return out

    def _session_unit_cost(self, venue: str, base: str) -> Decimal | None:
        lots = self._session_lots.get(self._lots_key(venue, base)) or []
        total_qty = _ZERO
        total_cost = _ZERO
        for qty, unit in lots:
            if qty <= 0 or unit <= 0:
                continue
            total_qty += qty
            total_cost += qty * unit
        if total_qty <= 0:
            return None
        return total_cost / total_qty

    def _session_qty(self, venue: str, base: str) -> Decimal:
        return sum(
            (
                qty
                for qty, _unit in (
                    self._session_lots.get(self._lots_key(venue, base)) or []
                )
                if qty > 0
            ),
            _ZERO,
        )

    def _series_for(self, symbol: str) -> Any:
        series = self._mark_series.get(symbol)
        if series is None:
            series = self._MarkSeries(maxlen=max(self._atr_samples, self._momentum_samples))
            self._mark_series[symbol] = series
        return series

    def _note_position_opened(self, venue: str, base: str) -> None:
        key = self._lots_key(venue, base)
        if key not in self._position_opened_mono:
            self._position_opened_mono[key] = time.monotonic()
            self._position_opened_at[key] = time.time()

    def _held_alt_bases(self) -> set[str]:
        """Distinct non-quote assets with meaningful live/paper inventory."""
        held: set[str] = set()
        min_notional = Decimal(
            str(getattr(self._settings, "paper_maker_min_notional_eur", 10) or 10)
        )
        for symbol, pos in self._portfolio.state.positions.items():
            if pos.quantity <= 0:
                continue
            base = infer_base_asset(symbol)
            if not base or base == self._quote or base in self._exclude_bases:
                continue
            if self._is_long_hold(base):
                continue
            mark = self._portfolio.state.mark_prices.get(symbol) or pos.average_entry_price
            if mark and pos.quantity * mark >= min_notional:
                held.add(base)
            elif pos.quantity > 0 and (mark is None or mark <= 0):
                held.add(base)
        # Also count balances that may not yet have a position row.
        for asset, bal in self._portfolio.state.balances.items():
            a = str(asset or "").upper()
            if not a or a == self._quote or a in self._exclude_bases:
                continue
            if self._is_long_hold(a):
                continue
            if bal.total <= 0:
                continue
            symbol = f"{a}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol) or _ZERO
            if mark > 0 and bal.total * mark < min_notional:
                continue  # dust — don't burn a concentration slot
            if mark <= 0 and bal.total < Decimal("0.001"):
                continue
            held.add(a)
        return held

    def _seed_cost_lots_from_balances(self, venue: str, bals: list[Any]) -> None:
        """Seed provisional FIFO lots at mark (untrusted — not safe for sells)."""
        key = venue.strip().lower()
        if key in self._lots_seeded_venues:
            return
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote:
                continue
            qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                str(getattr(bal, "locked", 0) or 0)
            )
            if qty <= 0:
                continue
            symbol = f"{asset}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol)
            if mark is None or mark <= 0:
                continue
            lot_key = self._lots_key(venue, asset)
            # Mark seed is provisional only — never trusted for profitable-sell gate.
            self._cost_lots.setdefault(lot_key, []).append([qty, Decimal(str(mark))])
            self._note_position_opened(venue, asset)
        self._lots_seeded_venues.add(key)

    def _has_trusted_cost(self, venue: str, base: str) -> bool:
        return self._lots_key(venue, base) in self._trusted_cost_keys

    def _mark_cost_trusted(self, venue: str, base: str) -> None:
        self._trusted_cost_keys.add(self._lots_key(venue, base))

    async def _hydrate_cost_basis_from_trades(self, venue: str) -> dict[str, Any]:
        """Replace mark-seeded lots with FIFO cost rebuilt from exchange fills."""
        venue = venue.strip().lower()
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client"}
        get_ex = getattr(client, "_get_exchange", None)
        if not callable(get_ex):
            return {"ok": False, "reason": "no_exchange"}
        try:
            exchange = await get_ex()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "exchange_unavailable", "error": str(exc)[:160]}

        # Ensure lot keys exist for every held balance (even if mark-seed skipped).
        bals = self._venue_raw_balances.get(venue) or []
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote:
                continue
            qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                str(getattr(bal, "locked", 0) or 0)
            )
            if qty <= 0:
                continue
            lot_key = self._lots_key(venue, asset)
            if lot_key not in self._cost_lots:
                symbol_i = f"{asset}{self._quote}"
                mark = self._portfolio.state.mark_prices.get(symbol_i)
                if mark is None or mark <= 0:
                    try:
                        ticker = await client.fetch_ticker(symbol_i)
                        mark = Decimal(
                            str(
                                getattr(ticker, "last", None)
                                or getattr(ticker, "bid", None)
                                or getattr(ticker, "ask", None)
                                or 0
                            )
                        )
                    except Exception:  # noqa: BLE001
                        mark = _ZERO
                if mark and mark > 0:
                    self._cost_lots[lot_key] = [[qty, mark]]
                    self._note_position_opened(venue, asset)
                else:
                    # Placeholder so trade rebuild can still run.
                    self._cost_lots[lot_key] = [[qty, _ZERO]]

        hydrated: list[str] = []
        for lot_key, lots in list(self._cost_lots.items()):
            if not lot_key.startswith(f"{venue}:"):
                continue
            if lot_key in self._trusted_cost_keys:
                continue
            base = lot_key.split(":", 1)[1]
            held = sum((q for q, _u in lots if q > 0), _ZERO)
            # Prefer live balance qty when available.
            for bal in bals:
                if str(getattr(bal, "asset", "") or "").upper() == base:
                    bal_qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                        str(getattr(bal, "locked", 0) or 0)
                    )
                    if bal_qty > 0:
                        held = bal_qty
                    break
            if held <= 0:
                continue
            symbol = f"{base}/{self._quote}"
            try:
                raw = await exchange.fetch_my_trades(symbol, limit=100)
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "cost hydrate trades failed venue=%s base=%s err=%s",
                    venue,
                    base,
                    type(exc).__name__,
                )
                continue
            rebuilt: list[list[Decimal]] = []
            for trade in sorted(raw or [], key=lambda t: int(t.get("timestamp") or 0)):
                side = str(trade.get("side") or "").lower()
                amt = Decimal(str(trade.get("amount") or 0))
                px = Decimal(str(trade.get("price") or 0))
                if amt <= 0 or px <= 0:
                    continue
                fee_info = trade.get("fee") or {}
                fee_amt = Decimal(str(fee_info.get("cost") or 0))
                fee_cur = str(fee_info.get("currency") or "").upper()
                if side == "buy":
                    lot_qty, unit = _buy_lot_qty_and_unit(
                        amount=amt,
                        price=px,
                        fee_amt=fee_amt,
                        fee_cur=fee_cur,
                        base=base,
                        quote=self._quote,
                    )
                    if lot_qty <= 0 or unit <= 0:
                        continue
                    rebuilt.append([lot_qty, unit])
                elif side == "sell":
                    remaining = amt
                    while remaining > 0 and rebuilt:
                        lq, lc = rebuilt[0]
                        take = min(remaining, lq)
                        lq -= take
                        remaining -= take
                        if lq <= 0:
                            rebuilt.pop(0)
                        else:
                            rebuilt[0][0] = lq
            rebuilt_qty = sum((q for q, _u in rebuilt if q > 0), _ZERO)
            if rebuilt_qty <= 0:
                # Fallback: OKX conversion / bill ledger (manual buys may not appear in trades).
                try:
                    ledger = await exchange.fetch_ledger(base, limit=50)
                except Exception:  # noqa: BLE001
                    ledger = []
                try:
                    eur_ledger = await exchange.fetch_ledger(self._quote, limit=50)
                except Exception:  # noqa: BLE001
                    eur_ledger = []
                # Pair same-timestamp SOL credit with EUR debit.
                eur_by_ts: dict[int, Decimal] = {}
                for entry in eur_ledger or []:
                    ts = int(entry.get("timestamp") or 0)
                    amt = Decimal(str(entry.get("amount") or 0))
                    direction = str(entry.get("direction") or "").lower()
                    if ts and amt < 0 and direction in {"", "out", "debit"}:
                        eur_by_ts[ts] = eur_by_ts.get(ts, _ZERO) + (-amt)
                    elif ts and amt > 0 and direction == "out":
                        eur_by_ts[ts] = eur_by_ts.get(ts, _ZERO) + amt
                for entry in sorted(ledger or [], key=lambda e: int(e.get("timestamp") or 0)):
                    ts = int(entry.get("timestamp") or 0)
                    amt = Decimal(str(entry.get("amount") or 0))
                    direction = str(entry.get("direction") or "").lower()
                    if amt <= 0:
                        continue
                    if direction and direction not in {"in", "credit"}:
                        continue
                    spent = eur_by_ts.get(ts)
                    if spent is None or spent <= 0:
                        continue
                    rebuilt.append([amt, spent / amt])
                rebuilt_qty = sum((q for q, _u in rebuilt if q > 0), _ZERO)
                if rebuilt_qty <= 0:
                    continue
            # Incomplete trade history must NOT become trusted cost (never-loss).
            if rebuilt_qty < held * Decimal("0.98"):
                logger.info(
                    "cost hydrate incomplete venue=%s base=%s rebuilt=%s held=%s",
                    venue,
                    base,
                    rebuilt_qty,
                    held,
                )
                continue
            # Keep lots covering current held qty (drop excess oldest if needed).
            if rebuilt_qty > held * Decimal("1.001"):
                need = held
                trimmed: list[list[Decimal]] = []
                for q, u in reversed(rebuilt):
                    if need <= 0:
                        break
                    take = min(q, need)
                    trimmed.append([take, u])
                    need -= take
                rebuilt = list(reversed(trimmed))
            self._cost_lots[lot_key] = rebuilt
            self._trusted_cost_keys.add(lot_key)
            hydrated.append(base)
            self._sync_paper_entry_from_lots(venue, base)
            logger.info(
                "cost basis hydrated venue=%s base=%s lots=%s unit=%s",
                venue,
                base,
                len(rebuilt),
                self._unit_cost(venue, base),
            )
        return {"ok": True, "venue": venue, "hydrated": hydrated}

    async def _backfill_fills_from_trades(self, venue: str) -> dict[str, Any]:
        """Mirror recent exchange fills into pocket PnL / fill counters.

        Resting-order polls can miss fills (fetch_order races). Trade history is
        the source of truth for session fills on both Bitvavo and OKX.
        """
        venue = venue.strip().lower()
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client"}
        get_ex = getattr(client, "_get_exchange", None)
        if not callable(get_ex):
            return {"ok": False, "reason": "no_exchange"}
        try:
            exchange = await get_ex()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "exchange_unavailable", "error": str(exc)[:160]}

        started_ms = float(self._session_started_ms or 0)
        since_ms = max(0.0, started_ms - 6 * 3600 * 1000) if started_ms else 0.0
        mirrored = 0
        bases: set[str] = set()
        for bal in self._venue_raw_balances.get(venue) or []:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if asset and asset != self._quote:
                bases.add(asset)
        for lot_key in list(self._cost_lots):
            if lot_key.startswith(f"{venue}:"):
                bases.add(lot_key.split(":", 1)[1])

        from bot.core.enums import OrderSide as _OrderSide

        for base in sorted(bases):
            symbol = f"{base}/{self._quote}"
            try:
                raw = await exchange.fetch_my_trades(symbol, limit=50)
            except Exception:  # noqa: BLE001
                continue
            for trade in sorted(raw or [], key=lambda t: int(t.get("timestamp") or 0)):
                tid = str(trade.get("id") or "")
                if not tid:
                    continue
                mirror_key = f"{venue}:{tid}"
                if mirror_key in self._mirrored_trade_ids:
                    continue
                ts = int(trade.get("timestamp") or 0)
                if since_ms and ts and ts < since_ms:
                    continue
                side = str(trade.get("side") or "").lower()
                amt = Decimal(str(trade.get("amount") or 0))
                px = Decimal(str(trade.get("price") or 0))
                if amt <= 0 or px <= 0 or side not in {"buy", "sell"}:
                    continue
                # Don't re-realize pre-session sells (would double-count PnL on restart).
                if side == "sell" and started_ms and ts and ts < started_ms:
                    self._mirrored_trade_ids.add(mirror_key)
                    continue
                fee_info = trade.get("fee") or {}
                fee_amt = Decimal(str(fee_info.get("cost") or 0))
                fee_cur = str(fee_info.get("currency") or self._quote).upper()
                if fee_cur == base and fee_amt > 0:
                    fee_quote = fee_amt * px
                elif fee_cur == self._quote:
                    fee_quote = fee_amt
                else:
                    fee_quote = fee_amt if fee_amt > 0 else (amt * px * Decimal("0.001"))
                # Buys: if hydrate already trusted the cost, only dedupe the trade id.
                if side == "buy" and self._has_trusted_cost(venue, base):
                    self._mirrored_trade_ids.add(mirror_key)
                    self.backfill_mirrored_count += 1
                    mirrored += 1
                    continue
                try:
                    self._record_realized_fill(
                        side=_OrderSide.BUY if side == "buy" else _OrderSide.SELL,
                        symbol=f"{base}{self._quote}",
                        qty=amt,
                        price=px,
                        fee=fee_quote,
                        venue=venue,
                        fee_currency=fee_cur,
                    )
                except TypeError:
                    # Older signature without fee_currency.
                    self._record_realized_fill(
                        side=_OrderSide.BUY if side == "buy" else _OrderSide.SELL,
                        symbol=f"{base}{self._quote}",
                        qty=amt,
                        price=px,
                        fee=fee_quote,
                        venue=venue,
                    )
                self._mirrored_trade_ids.add(mirror_key)
                self.backfill_mirrored_count += 1
                mirrored += 1
                logger.info(
                    "FILL_BACKFILL venue=%s base=%s side=%s qty=%s px=%s trade=%s",
                    venue,
                    base,
                    side,
                    amt,
                    px,
                    tid,
                )
        return {"ok": True, "venue": venue, "mirrored": mirrored}


    def _record_realized_fill(
        self,
        *,
        side: OrderSide,
        symbol: str,
        qty: Decimal,
        price: Decimal,
        fee: Decimal,
        venue: str = "",
        fee_currency: str | None = None,
    ) -> None:
        """Update FIFO lots / realized PnL for a live mirrored fill."""
        if qty <= 0 or price <= 0:
            return
        base = infer_base_asset(symbol)
        lot_key = self._lots_key(venue or "bitvavo", base)
        lots = self._cost_lots.setdefault(lot_key, [])
        if side == OrderSide.BUY:
            lot_qty, unit = _buy_lot_qty_and_unit(
                amount=qty,
                price=price,
                fee_amt=fee,
                fee_cur=str(fee_currency or self._quote),
                base=base,
                quote=self._quote,
            )
            lots.append([lot_qty, unit])
            self._session_lots.setdefault(lot_key, []).append([lot_qty, unit])
            self._note_position_opened(venue or "bitvavo", base)
            self._mark_cost_trusted(venue or "bitvavo", base)
            return
        remaining = qty
        # Quote-denominated fee reduces proceeds; base fee is already outside qty.
        fee_cur = str(fee_currency or self._quote).upper()
        if fee_cur == base.upper() and fee > 0:
            proceeds = qty * price
        else:
            proceeds = qty * price - fee
        cost = _ZERO
        while remaining > 0 and lots:
            lot_qty, lot_cost = lots[0]
            take = min(remaining, lot_qty)
            cost += take * lot_cost
            lot_qty -= take
            remaining -= take
            if lot_qty <= 0:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty
        # Mirror consume on session lots (trail inventory).
        sess = self._session_lots.setdefault(lot_key, [])
        left = qty
        while left > 0 and sess:
            sq, _sc = sess[0]
            take = min(left, sq)
            sq -= take
            left -= take
            if sq <= 0:
                sess.pop(0)
            else:
                sess[0][0] = sq
        if remaining > 0:
            cost += remaining * price
        self.realized_trade_pnl_eur += proceeds - cost
        self._check_daily_kill()
        if not lots:
            self._position_opened_mono.pop(lot_key, None)
            self._trail.pop(lot_key, None)
        if not sess:
            self._trail.pop(lot_key, None)

    def _bump_skip(self, key: str) -> None:
        self.skips[key] = self.skips.get(key, 0) + 1

    def _resolve_venue(self, order_request: OrderRequest) -> str:
        """Pick the live venue for an order — never hardcode Bitvavo for EUR.

        Prefer explicit venue metadata, then buy/sell exchange from the
        opportunity, then the cash-richest execute venue. Missing venue must
        not silently route every *EUR pair to Bitvavo and starve OKX.
        """
        meta = order_request.metadata or {}
        venue = str(meta.get("venue") or meta.get("exchange") or "").strip().lower()
        if venue:
            return venue
        side = order_request.side
        side_l = str(side.value if hasattr(side, "value") else side).lower()
        if side_l.startswith("s"):
            venue = str(
                meta.get("sell_exchange") or meta.get("buy_exchange") or ""
            ).strip().lower()
        else:
            venue = str(
                meta.get("buy_exchange") or meta.get("sell_exchange") or ""
            ).strip().lower()
        if venue:
            return venue
        candidates = sorted(self._execute_venues)
        if not candidates:
            return ""
        best = ""
        best_score = Decimal("-1")
        for cand in candidates:
            # Prefer real free EUR on the venue (cache) so a full €2k+€2k
            # pocket does not collapse to alphabetical Bitvavo.
            live = self._live_free_sync(cand, self._quote)
            pocket = self._venue_budget_remaining(cand)
            score = live if live > 0 else pocket
            if score > best_score:
                best_score = score
                best = cand
        return best

    def _trading_client(self, venue: str) -> Any | None:
        registry = getattr(self._live, "_registry", None)
        if registry is None:
            return None
        return registry.get_client(venue, enable_trading=True)

    async def reconcile_from_exchange(self, venue: str = "bitvavo") -> dict[str, Any]:
        """Pull live balances into the paper pocket + venue ledger for strategy sizing."""
        venue = venue.strip().lower()
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client", "venue": venue}
        try:
            snap = await client.get_balances()
        except Exception as exc:  # noqa: BLE001
            logger.warning("micro reconcile balance fetch failed: %s", type(exc).__name__)
            return {"ok": False, "reason": "balance_fetch_failed", "error": str(exc)[:200]}

        bals = list(snap.balances or [])
        self._bal_cache[venue] = bals
        self._bal_cache_mono[venue] = time.monotonic()
        self._venue_raw_balances[venue] = bals

        if self._portfolio.venue_ledger is None:
            self._portfolio.init_venue_ledger(
                sorted(self._execute_venues), starting_quote=_ZERO
            )
        else:
            self._portfolio.venue_ledger.ensure_venues(sorted(self._execute_venues))

        ledger_balances: dict[str, Decimal] = {}
        for bal in bals:
            asset = str(bal.asset or "").upper()
            if not asset:
                continue
            if asset != self._quote:
                if asset in self._exclude_bases:
                    continue
                if self._allowed_bases is not None and asset not in self._allowed_bases:
                    continue
            free = Decimal(str(bal.free or 0))
            if free > 0:
                if asset == self._quote:
                    ledger_balances[asset] = min(free, self._budget)
                else:
                    ledger_balances[asset] = free
        self._portfolio.venue_ledger.replace_balances(venue, ledger_balances)

        mapped = self._rebuild_aggregate_from_venues()
        portfolio_value = await self.refresh_portfolio_value()
        if self.starting_portfolio_eur is None and portfolio_value is not None:
            self.starting_portfolio_eur = portfolio_value
        if self._session_started_ms is None:
            self._session_started_ms = time.time() * 1000.0
        # Seed EVERY execute venue (not only the first sync) so OKX lots exist.
        self._seed_cost_lots_from_balances(venue, bals)
        # Prefer real exchange fills over mark seeds before any auto-sell.
        try:
            hydrate = await self._hydrate_cost_basis_from_trades(venue)
            venue_sync_hydrate = hydrate
        except Exception as exc:  # noqa: BLE001
            venue_sync_hydrate = {"ok": False, "error": str(exc)[:160]}
        try:
            backfill = await self._backfill_fills_from_trades(venue)
            venue_sync_hydrate = {
                **(venue_sync_hydrate if isinstance(venue_sync_hydrate, dict) else {}),
                "fill_backfill": backfill,
            }
        except Exception as exc:  # noqa: BLE001
            logger.info("fill backfill failed venue=%s err=%s", venue, type(exc).__name__)

        venue_sync = {
            "ok": True,
            "venue": venue,
            "balances": mapped,
            "ledger": {k: str(v) for k, v in sorted(ledger_balances.items())},
            "venue_budget_remaining": str(self._venue_budget_remaining(venue)),
            "free_quote_eur": str(self._venue_budget_remaining(venue)),
            "remaining_eur": str(self.budget_remaining),
            "portfolio_value_eur": (
                str(self.portfolio_value_eur) if self.portfolio_value_eur is not None else None
            ),
            "cost_hydrate": venue_sync_hydrate,
        }
        self._last_sync_by_venue[venue] = venue_sync
        self._last_sync = venue_sync
        logger.info(
            "MICRO_SYNC venue=%s venue_eur=%s portfolio=%s total_remaining=%s assets=%s ledger=%s",
            venue,
            self._venue_budget_remaining(venue),
            self.portfolio_value_eur,
            self.budget_remaining,
            sorted(mapped.keys()),
            sorted(ledger_balances.keys()),
        )
        self.persist_runtime_state()
        return dict(venue_sync)

    async def _fetch_balances_cached(self, venue: str) -> list[Any]:
        venue = venue.strip().lower()
        now = time.monotonic()
        cached_mono = self._bal_cache_mono.get(venue, 0.0)
        if (
            venue in self._bal_cache
            and now - cached_mono < self._bal_cache_sec
        ):
            return self._bal_cache[venue]
        client = self._trading_client(venue)
        if client is None:
            return self._bal_cache.get(venue, [])
        snap = await client.get_balances()
        bals = list(snap.balances or [])
        self._bal_cache[venue] = bals
        self._bal_cache_mono[venue] = now
        self._venue_raw_balances[venue] = bals
        return bals

    async def refresh_portfolio_value(
        self,
        *,
        venue: str | None = None,
        balances: list[Any] | None = None,
    ) -> Decimal | None:
        """Mark portfolio to EUR across all execute venues (cash + crypto × last/bid)."""
        venues = [venue.strip().lower()] if venue else sorted(self._execute_venues)
        total = _ZERO
        now = time.monotonic()
        for v in venues:
            client = self._trading_client(v)
            if client is None:
                continue
            bals = balances if venue and balances is not None else None
            if bals is None:
                try:
                    bals = await self._fetch_balances_cached(v)
                except Exception:  # noqa: BLE001
                    continue
            for bal in bals:
                asset = str(getattr(bal, "asset", "") or "").upper()
                if not asset:
                    continue
                qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                    str(getattr(bal, "locked", 0) or 0)
                )
                if qty <= 0:
                    continue
                if asset == self._quote:
                    if venue is None and v in self._venue_raw_balances:
                        # Cap each venue's EUR when summing total portfolio value.
                        qty = min(qty, self._budget)
                    total += qty
                    continue
                symbol = f"{asset}{self._quote}"
                mark = self._portfolio.state.mark_prices.get(symbol)
                fetched_at = self._mark_fetched_at.get(symbol, 0.0)
                stale = now - fetched_at >= self._mark_ttl_sec
                if mark is None or mark <= 0 or stale:
                    try:
                        ticker = await client.fetch_ticker(symbol)
                        mark = Decimal(
                            str(ticker.last or ticker.bid or ticker.ask or 0)
                        )
                        if mark > 0:
                            self._portfolio.set_mark_price(symbol, mark)
                            self._mark_fetched_at[symbol] = now
                    except Exception:  # noqa: BLE001
                        mark = self._portfolio.state.mark_prices.get(symbol)
                if mark is not None and mark > 0:
                    total += qty * mark
        if total > 0:
            self.portfolio_value_eur = total
        return self.portfolio_value_eur

    async def _live_free(self, venue: str, asset: str) -> Decimal:
        try:
            bals = await self._fetch_balances_cached(venue)
        except Exception:  # noqa: BLE001
            return _ZERO
        key = asset.upper()
        for bal in bals:
            if str(getattr(bal, "asset", "")).upper() == key:
                return Decimal(str(getattr(bal, "free", 0) or 0))
        return _ZERO

    async def _poll_fill(
        self,
        *,
        venue: str,
        symbol: str,
        exchange_order_id: str | None,
        fallback_price: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Poll exchange order briefly; return (filled_qty, avg_price)."""
        if not exchange_order_id:
            return _ZERO, fallback_price
        client = self._trading_client(venue)
        if client is None or not hasattr(client, "fetch_order"):
            return _ZERO, fallback_price

        deadline = time.monotonic() + _FILL_POLL_SECONDS
        last_filled = _ZERO
        last_avg = fallback_price
        while True:
            try:
                order = await client.fetch_order(str(exchange_order_id), symbol)
            except Exception:  # noqa: BLE001
                break
            last_filled = Decimal(str(order.filled_quantity or 0))
            avg = order.average_price or order.price or fallback_price
            last_avg = Decimal(str(avg or fallback_price))
            status = order.status
            status_val = status.value if hasattr(status, "value") else str(status)
            if last_filled > 0:
                return last_filled, last_avg if last_avg > 0 else fallback_price
            if str(status_val).lower() in {
                "filled",
                "closed",
                "cancelled",
                "canceled",
                "rejected",
                "failed",
            }:
                return last_filled, last_avg if last_avg > 0 else fallback_price
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(_FILL_POLL_INTERVAL)
        return last_filled, last_avg if last_avg > 0 else fallback_price

    def _track_resting(
        self,
        *,
        venue: str,
        symbol: str,
        side: str,
        exchange_order_id: str | None,
        quantity: Decimal,
        price: Decimal,
        strategy: str,
        opportunity_id: Any,
    ) -> None:
        if not exchange_order_id:
            return
        self._resting.append(
            {
                "venue": venue,
                "symbol": symbol,
                "side": side,
                "exchange_order_id": str(exchange_order_id),
                "quantity": Decimal(str(quantity)),
                "price": Decimal(str(price)),
                "strategy": strategy,
                "opportunity_id": opportunity_id,
                "placed_mono": time.monotonic(),
                "placed_at": time.time(),
            }
        )
        self.persist_runtime_state()

    async def manage_resting_orders(self, venue: str = "bitvavo") -> dict[str, Any]:
        """Poll resting live orders: mirror fills, cancel stale quotes, free capital."""
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client"}
        mirrored = 0
        cancelled = 0
        still: list[dict[str, Any]] = []
        now = time.monotonic()
        max_age = self._resting_max_age_sec
        venue_l = venue.strip().lower()
        tracked_ids = {
            str(r.get("exchange_order_id"))
            for r in self._resting
            if str(r.get("venue") or "").strip().lower() == venue_l
        }

        for row in list(self._resting):
            row_venue = str(row.get("venue") or "").strip().lower()
            # Critical: never poll Bitvavo ids on OKX (or vice versa) — that
            # yields ExchangeError spam, drops fill mirrors, and hits max-open.
            if row_venue and row_venue != venue_l:
                still.append(row)
                continue
            oid = str(row.get("exchange_order_id") or "")
            symbol = str(row.get("symbol") or "")
            if not oid or not symbol:
                continue
            filled = _ZERO
            avg = Decimal(str(row.get("price") or 0))
            status_val = "open"
            try:
                order = await client.fetch_order(oid, symbol)
                filled = Decimal(str(order.filled_quantity or 0))
                avg = Decimal(
                    str(order.average_price or order.price or row.get("price") or 0)
                )
                status = order.status
                status_val = status.value if hasattr(status, "value") else str(status)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "resting fetch_order failed venue=%s id=%s symbol=%s err=%s",
                    venue_l,
                    oid,
                    symbol,
                    f"{type(exc).__name__}: {exc}"[:180],
                )

            if filled > 0 and avg > 0:
                from bot.core.models import OrderRequest
                from uuid import UUID

                side_raw = str(row.get("side") or "buy").lower()
                opp_side = (
                    OpportunitySide.BUY if side_raw.startswith("b") else OpportunitySide.SELL
                )
                opp_id = row.get("opportunity_id")
                try:
                    opp_uuid = opp_id if isinstance(opp_id, UUID) else uuid4()
                except Exception:  # noqa: BLE001
                    opp_uuid = uuid4()
                req = OrderRequest(
                    opportunity_id=opp_uuid,
                    symbol=symbol,
                    side=opp_side,
                    quantity=filled,
                    limit_price=avg,
                    metadata={"venue": venue, "exchange": venue},
                )
                await self._mirror_live_fill(
                    req,
                    filled_qty=filled,
                    average_price=avg,
                    venue=venue,
                    strategy=str(row.get("strategy") or "maker_inventory"),
                    exchange_order_id=oid,
                )
                mirrored += 1
                remaining_open = str(status_val).lower() in {"open", "submitted", "pending", "partial"}
                if remaining_open and filled < Decimal(str(row.get("quantity") or filled)):
                    # partial — keep tracking remainder
                    row["quantity"] = Decimal(str(row.get("quantity") or 0)) - filled
                    if row["quantity"] > 0:
                        still.append(row)
                continue

            age = now - float(row.get("placed_mono") or now)
            terminal = str(status_val).lower() in {
                "cancelled",
                "canceled",
                "rejected",
                "failed",
                "expired",
                "filled",
                "closed",
            }
            if terminal:
                continue
            # Never leave a loss-making sell resting on either venue.
            side_raw = str(row.get("side") or "buy").lower()
            if side_raw.startswith("s"):
                base = infer_base_asset(symbol)
                px = Decimal(str(row.get("price") or 0))
                ok_sell, gate_reason, be = self._sell_allowed_at(venue, base, px)
                if not ok_sell:
                    try:
                        await client.cancel_order(oid, symbol)
                        cancelled += 1
                        self._invalidate_bal_cache()
                        self._bump_skip("sell_below_break_even_cancelled")
                        logger.info(
                            "MICRO_LOSS_SELL_CANCEL venue=%s symbol=%s id=%s reason=%s be=%s px=%s",
                            venue,
                            symbol,
                            oid,
                            gate_reason,
                            be,
                            px,
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning("loss-sell cancel failed id=%s", oid)
                        still.append(row)
                    continue
            if age >= max_age:
                try:
                    await client.cancel_order(oid, symbol)
                    cancelled += 1
                    self._invalidate_bal_cache()
                    self._bump_skip("stale_quote_cancelled")
                    logger.info(
                        "MICRO_STALE_CANCEL venue=%s symbol=%s id=%s age=%.1fs",
                        venue,
                        symbol,
                        oid,
                        age,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("stale cancel failed id=%s", oid)
                    still.append(row)
                continue
            still.append(row)

        self._resting = still
        tracked_ids = {str(r.get("exchange_order_id")) for r in self._resting}

        # Orphan sweep is throttled — avoid cancelling fresh quotes we just tracked.
        do_orphan = (
            max_age <= 0
            or now - self._last_orphan_sweep_mono >= self._orphan_sweep_sec
        )
        if do_orphan:
            self._last_orphan_sweep_mono = now
            try:
                open_orders = await client.fetch_open_orders()
            except Exception:  # noqa: BLE001
                open_orders = []
            for order in open_orders or []:
                oid = str(getattr(order, "id", None) or "")
                if not oid or oid in tracked_ids:
                    continue
                symbol = (
                    str(getattr(order, "symbol", "") or "")
                    .upper()
                    .replace("/", "")
                    .replace("-", "")
                )
                if not symbol:
                    continue
                try:
                    await client.cancel_order(oid, symbol)
                    cancelled += 1
                    self._invalidate_bal_cache()
                    self._bump_skip("orphan_open_cancelled")
                except Exception:  # noqa: BLE001
                    logger.warning("orphan cancel failed id=%s", oid)

        live_exec = getattr(self._live, "executor", None)
        if live_exec is not None and hasattr(live_exec, "refresh_open_order_count"):
            try:
                if cancelled and hasattr(live_exec, "note_open_orders"):
                    live_exec.note_open_orders(len(self._resting))
                await live_exec.refresh_open_order_count(venue, force=bool(cancelled))
            except Exception:  # noqa: BLE001
                pass
        if mirrored or cancelled:
            await self.reconcile_from_exchange(venue)
        self.persist_runtime_state()
        return {
            "ok": True,
            "mirrored": mirrored,
            "cancelled": cancelled,
            "resting": len(self._resting),
        }

    def _sync_paper_entry_from_lots(self, venue: str, base: str) -> None:
        """Align paper average entry with trusted live lots (prevents phantom daily loss)."""
        unit = self._unit_cost(venue, base)
        if unit is None or unit <= 0:
            return
        symbol = f"{base.upper()}{self._quote}"
        pos = self._portfolio.state.positions.get(symbol)
        if pos is None or pos.quantity <= 0:
            return
        pos.average_entry_price = unit

    def reset_paper_realized_after_inventory_sync(self) -> None:
        """Inventory sync is not a trade — clear phantom paper realized PnL."""
        try:
            self._portfolio.state.stats.realized_pnl = _ZERO
        except Exception:  # noqa: BLE001
            logger.exception("failed to reset paper realized after sync")

    def _unit_cost(self, venue: str, base: str) -> Decimal | None:

        lots = self._cost_lots.get(self._lots_key(venue, base)) or []
        total_qty = _ZERO
        total_cost = _ZERO
        for qty, unit in lots:
            if qty <= 0 or unit <= 0:
                continue
            total_qty += qty
            total_cost += qty * unit
        if total_qty > 0:
            return total_cost / total_qty
        symbol = f"{base.upper()}{self._quote}"
        pos = self._portfolio.state.positions.get(symbol)
        if pos is not None and pos.quantity > 0 and pos.average_entry_price > 0:
            return Decimal(str(pos.average_entry_price))
        return None

    def _break_even_sell_price(
        self, venue: str, base: str, *, taker: bool = False
    ) -> Decimal | None:
        """Min sell price that nets profit after fees + buffer. Requires trusted cost."""
        if not self._has_trusted_cost(venue, base):
            return None
        unit = self._unit_cost(venue, base)
        if unit is None or unit <= 0:
            return None
        from bot.core.venue_fees import venue_maker_fee, venue_taker_fee

        fee = venue_taker_fee(venue) if taker else venue_maker_fee(venue)
        denom = Decimal("1") - fee
        if denom <= 0:
            return None
        be = unit / denom
        buffer_bps = Decimal(
            str(getattr(self._settings, "paper_maker_sell_profit_buffer_bps", 0) or 0)
        )
        if buffer_bps > 0:
            be *= Decimal("1") + buffer_bps / Decimal("10000")
        return be

    def _time_stop_floor_price(self, venue: str, base: str) -> Decimal | None:
        """Time-stop must clear a little profit above fee-aware break-even."""
        be = self._break_even_sell_price(venue, base)
        if be is None:
            return None
        extra_bps = Decimal(
            str(getattr(self._settings, "paper_time_stop_min_profit_bps", 0) or 0)
        )
        if extra_bps > 0:
            be *= Decimal("1") + extra_bps / Decimal("10000")
        return be

    async def _profitable_exit_quote(
        self, venue: str, base: str, mark: Decimal
    ) -> tuple[Decimal | None, bool, str]:
        """Pick a fillable exit price that still clears fee-aware break-even.

        Returns (limit_price, post_only, reason). When the bid is already above
        taker break-even, hit the bid (taker) so trail exits actually fill.
        """
        be_maker = self._break_even_sell_price(venue, base, taker=False)
        be_taker = self._break_even_sell_price(venue, base, taker=True)
        if be_maker is None or be_taker is None:
            return None, True, "no_break_even"
        if mark < be_maker:
            return None, True, "mark_below_maker_be"

        best_bid = _ZERO
        best_ask = _ZERO
        client = self._trading_client(venue)
        symbol = f"{base.upper()}{self._quote}"
        if client is not None:
            try:
                ticker = await client.fetch_ticker(symbol)
                best_bid = Decimal(str(getattr(ticker, "bid", None) or 0))
                best_ask = Decimal(str(getattr(ticker, "ask", None) or 0))
            except Exception:  # noqa: BLE001
                pass
        if best_bid <= 0:
            best_bid = mark
        if best_ask <= 0:
            best_ask = mark

        # Bid already clears taker BE → take liquidity for a sure profitable fill.
        if best_bid >= be_taker:
            return best_bid, False, "hit_bid_taker"
        # Otherwise rest as maker at/above maker BE, near the touch.
        maker_px = max(be_maker, min(best_ask, mark))
        if maker_px <= best_bid:
            maker_px = max(be_maker, best_bid + (best_bid * Decimal("0.0001")))
        return maker_px, True, "rest_maker_be"

    def _sell_allowed_at(
        self, venue: str, base: str, price: Decimal
    ) -> tuple[bool, str, Decimal | None]:
        """Gate every auto-sell: trusted cost and price >= fee-aware break-even."""
        if price <= 0:
            return False, "invalid_price", None
        if not self._has_trusted_cost(venue, base):
            return False, "sell_no_trusted_cost", None
        be = self._break_even_sell_price(venue, base)
        if be is None:
            return False, "sell_no_break_even", None
        if price < be:
            return False, "sell_below_break_even", be
        return True, "ok", be

    def _scaled_arms(self, base: str, cost: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        from bot.live.trail_policy import scale_thresholds

        symbol = f"{base.upper()}{self._quote}"
        atr = self._series_for(symbol).atr_pct()
        th = scale_thresholds(
            atr=atr,
            soft_arm_floor=self._soft_arm_floor,
            soft_dd_floor=self._soft_dd_floor,
            hard_arm_floor=self._hard_arm_floor,
            hard_dd_floor=self._hard_dd_floor,
            atr_arm_mult=self._atr_arm_mult,
            atr_dd_mult=self._atr_dd_mult,
            atr_enabled=self._atr_enabled,
        )
        return th.soft_arm, th.soft_dd, th.hard_arm, th.hard_dd

    def _corr_held_count(self, *, adding: str | None = None) -> int:
        if not self._corr_group:
            return 0
        held = self._held_alt_bases()
        if adding:
            held = set(held) | {adding.upper()}
        return len(held & self._corr_group)

    def _momentum_ok(self, symbol: str) -> bool:
        if not self._momentum_enabled:
            return True
        series = self._series_for(symbol)
        if len(series) < max(3, min(6, self._momentum_samples // 2)):
            return True  # not enough history yet — don't freeze entries
        mom = series.momentum_return()
        if mom is None:
            return True
        return mom >= self._momentum_min

    def _trail_update_state(
        self, venue: str, base: str, *, cost: Decimal, mark: Decimal
    ) -> dict[str, Any]:
        """Soft/hard arm vs session cost; ATR-scaled; peak drawdown trigger."""
        trail_key = self._lots_key(venue, base)
        soft_arm, soft_dd, hard_arm, hard_dd = self._scaled_arms(base, cost)
        atr = self._series_for(f"{base.upper()}{self._quote}").atr_pct()
        st = self._trail.setdefault(
            trail_key,
            {
                "venue": venue,
                "base": base.upper(),
                "soft_armed": False,
                "hard_armed": False,
                "armed": False,
                "peak": _ZERO,
                "cost": cost,
                "last_mark": mark,
                "triggered": False,
                "newly_soft": False,
                "newly_hard": False,
                "newly_armed": False,
                "soft_partial_done": False,
                "hard_partial_done": False,
                "partial_done": False,
                "time_stop_due": False,
                "soft_arm": str(soft_arm),
                "hard_arm": str(hard_arm),
                "drawdown": str(soft_dd),
                "atr": str(atr),
                "session_qty": str(self._session_qty(venue, base)),
            },
        )
        st["venue"] = venue
        st["base"] = base.upper()
        st["cost"] = cost
        prev_mark = Decimal(str(st.get("last_mark") or 0))
        st["last_mark"] = mark
        st["soft_arm"] = str(soft_arm)
        st["hard_arm"] = str(hard_arm)
        st["atr"] = str(atr)
        st["session_qty"] = str(self._session_qty(venue, base))
        st["newly_soft"] = False
        st["newly_hard"] = False
        st["newly_armed"] = False
        st["time_stop_due"] = False
        st["triggered"] = False
        if cost <= 0 or mark <= 0:
            return st
        gain = (mark - cost) / cost
        st["gain"] = str(gain)

        # Reject one-tick mark spikes (bad ticker/print) before arming trail.
        mark_spike = False
        if prev_mark > 0 and mark >= prev_mark * Decimal("1.08"):
            mark_spike = True
        elif gain >= max(soft_arm * Decimal("5"), Decimal("0.10")):
            mark_spike = True
        if mark_spike and not st.get("soft_armed") and gain >= soft_arm:
            logger.warning(
                "TRAIL_MARK_SPIKE_IGNORED base=%s cost=%s prev=%s mark=%s gain=%.2f%%",
                base,
                cost,
                prev_mark,
                mark,
                float(gain * 100),
            )
            self._bump_skip("trail_mark_spike")

        if not st.get("soft_armed") and gain >= soft_arm and not mark_spike:
            st["soft_armed"] = True
            st["armed"] = True
            st["peak"] = mark
            st["newly_soft"] = True
            st["newly_armed"] = True
            st["drawdown"] = str(soft_dd)
            self._push_alert(
                "soft_arm",
                f"{base} soft-arm +{float(gain * 100):.1f}% "
                f"(need {float(soft_arm * 100):.0f}%)",
                base=base,
            )
            logger.info(
                "TRAIL_SOFT_ARM base=%s cost=%s mark=%s gain=%.2f%% arm=%.2f%%",
                base,
                cost,
                mark,
                float(gain * 100),
                float(soft_arm * 100),
            )
        elif not st.get("soft_armed") and soft_arm > 0:
            to_arm = soft_arm - gain
            if 0 < to_arm <= self._alert_pct_to_arm:
                self._push_alert(
                    "near_soft_arm",
                    f"{base} near soft-arm gain={float(gain * 100):.1f}% "
                    f"need={float(soft_arm * 100):.0f}%",
                    base=base,
                )

        if st.get("soft_armed") and not st.get("hard_armed") and gain >= hard_arm:
            st["hard_armed"] = True
            st["newly_hard"] = True
            st["drawdown"] = str(hard_dd)
            if mark > Decimal(str(st.get("peak") or 0)):
                st["peak"] = mark
            self._push_alert(
                "hard_arm",
                f"{base} hard-arm +{float(gain * 100):.1f}%",
                base=base,
            )
            logger.info(
                "TRAIL_HARD_ARM base=%s cost=%s mark=%s gain=%.2f%% arm=%.2f%%",
                base,
                cost,
                mark,
                float(gain * 100),
                float(hard_arm * 100),
            )

        if not st.get("soft_armed"):
            if self._time_stop_enabled:
                opened = self._position_opened_mono.get(trail_key)
                if opened is not None and (
                    time.monotonic() - opened >= self._time_stop_sec
                ):
                    st["time_stop_due"] = True
            return st

        peak = Decimal(str(st.get("peak") or 0))
        if mark > peak:
            st["peak"] = mark
            peak = mark
        active_dd = hard_dd if st.get("hard_armed") else soft_dd
        st["drawdown"] = str(active_dd)
        if peak > 0 and mark <= peak * (Decimal("1") - active_dd):
            st["triggered"] = True
            self._push_alert(
                "trail_fire",
                f"{base} trail fire peak={peak} mark={mark} "
                f"dd={float(active_dd * 100):.1f}%",
                base=base,
            )
            logger.info(
                "TRAIL_TRIGGER base=%s cost=%s peak=%s mark=%s dd=%.2f%% hard=%s",
                base,
                cost,
                peak,
                mark,
                float((Decimal("1") - mark / peak) * 100),
                bool(st.get("hard_armed")),
            )
        return st

    async def _mark_price(self, venue: str, symbol: str) -> Decimal | None:
        mark = self._portfolio.state.mark_prices.get(symbol)
        now = time.monotonic()
        fetched_at = self._mark_fetched_at.get(symbol, 0.0)
        if mark is not None and mark > 0 and now - fetched_at < self._mark_ttl_sec:
            m = Decimal(str(mark))
            self._series_for(symbol).push(m)
            return m
        client = self._trading_client(venue)
        if client is None:
            if mark and mark > 0:
                m = Decimal(str(mark))
                self._series_for(symbol).push(m)
                return m
            return None
        try:
            ticker = await client.fetch_ticker(symbol)
            mark = Decimal(str(ticker.last or ticker.bid or ticker.ask or 0))
            if mark > 0:
                self._portfolio.set_mark_price(symbol, mark)
                self._mark_fetched_at[symbol] = now
                self._series_for(symbol).push(Decimal(str(mark)))
                return mark
        except Exception:  # noqa: BLE001
            pass
        if mark is not None and mark > 0:
            m = Decimal(str(mark))
            self._series_for(symbol).push(m)
            return m
        return None

    async def _cancel_resting_for_symbol(self, venue: str, symbol: str) -> int:
        client = self._trading_client(venue)
        if client is None:
            return 0
        cancelled = 0
        still: list[dict[str, Any]] = []
        venue_l = venue.strip().lower()
        for row in list(self._resting):
            row_venue = str(row.get("venue") or "").strip().lower()
            if row_venue and row_venue != venue_l:
                still.append(row)
                continue
            if str(row.get("symbol") or "").upper() != symbol.upper():
                still.append(row)
                continue
            oid = str(row.get("exchange_order_id") or "")
            if not oid:
                continue
            try:
                await client.cancel_order(oid, symbol)
                cancelled += 1
                self._invalidate_bal_cache()
            except Exception:  # noqa: BLE001
                still.append(row)
        self._resting = still
        return cancelled

    async def _submit_exit_sell(
        self,
        *,
        venue: str,
        symbol: str,
        qty: Decimal,
        mark: Decimal,
        reason: str,
        limit_price: Decimal | None = None,
        post_only: bool = False,
    ) -> ExecutionResult:
        px = limit_price
        if px is None or px <= 0:
            px = (mark * Decimal("0.998")).quantize(Decimal("0.00000001"))
        req = OrderRequest(
            opportunity_id=uuid4(),
            symbol=symbol,
            side=OpportunitySide.SELL,
            quantity=qty,
            limit_price=px,
            metadata={
                "venue": venue,
                "exchange": venue,
                "trail_take_profit": True,
                "post_only": post_only,
                "strategy": reason,
                "exit_reason": reason,
            },
        )
        return await self.execute(
            req, strategy=reason, order_type=OrderType.LIMIT
        )

    async def _refresh_free(
        self, venue: str, symbol: str, asset: str, locked: Decimal
    ) -> Decimal:
        if locked > 0:
            await self._cancel_resting_for_symbol(venue, symbol)
            self._invalidate_bal_cache(venue)
        return await self._live_free(venue, asset)

    async def check_trailing_take_profits(
        self, venue: str = "bitvavo"
    ) -> dict[str, Any]:
        """Soft/hard arm partials + peak drawdown on session buys; time-stop BE."""
        if not self._trail_enabled and not self._time_stop_enabled:
            return {"ok": True, "enabled": False, "triggered": []}
        venue = venue.strip().lower()
        bals = await self._fetch_balances_cached(venue)
        triggered: list[dict[str, Any]] = []
        armed_now: list[str] = []
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote:
                continue
            if asset in self._exclude_bases:
                continue
            if self._is_long_hold(asset):
                symbol = f"{asset}{self._quote}"
                mark = await self._mark_price(venue, symbol)
                cost = self._unit_cost(venue, asset)
                if mark is not None and cost is not None and cost > 0 and mark > 0:
                    self._trail_update_state(venue, asset, cost=cost, mark=mark)
                # Long-hold: do not stop the function early.
                # Exits still go through the never-loss / break-even gates.
            if self._allowed_bases is not None and asset not in self._allowed_bases:
                continue
            trail_key = self._lots_key(venue, asset)
            free = Decimal(str(getattr(bal, "free", 0) or 0))
            locked = Decimal(str(getattr(bal, "locked", 0) or 0))
            if free + locked <= 0:
                self._trail.pop(trail_key, None)
                self._position_opened_mono.pop(trail_key, None)
                self._session_lots.pop(trail_key, None)
                continue

            # Trail only on session buys (not mark-seeded pre-session bags).
            cost = (
                self._session_unit_cost(venue, asset)
                if self._trail_session_only
                else self._unit_cost(venue, asset)
            )
            session_qty = (
                self._session_qty(venue, asset)
                if self._trail_session_only
                else (free + locked)
            )
            if cost is None or cost <= 0 or session_qty <= 0:
                if self._time_stop_enabled:
                    blend = self._unit_cost(venue, asset)
                    if blend is None or blend <= 0:
                        continue
                    # Time-stop also requires trusted fee-aware cost — never guess.
                    if not self._has_trusted_cost(venue, asset):
                        self._bump_skip("trail_no_trusted_cost")
                        continue
                    symbol = f"{asset}{self._quote}"
                    mark = await self._mark_price(venue, symbol)
                    if mark is None or mark <= 0:
                        continue
                    self._note_position_opened(venue, asset)
                    opened = self._position_opened_mono.get(trail_key)
                    if opened is None or (
                        time.monotonic() - opened < self._time_stop_sec
                    ):
                        continue
                    floor = self._time_stop_floor_price(venue, asset)
                    if floor is None or mark < floor:
                        self._bump_skip("time_stop_below_be")
                        continue
                    free = await self._refresh_free(venue, symbol, asset, locked)
                    sell_qty = free
                    if sell_qty <= 0 or sell_qty * mark < _MIN_LIVE_NOTIONAL:
                        continue
                    result = await self._submit_exit_sell(
                        venue=venue,
                        symbol=symbol,
                        qty=sell_qty,
                        mark=mark,
                        reason="time_stop_breakeven",
                        limit_price=max(floor, mark * Decimal("0.999")),
                        post_only=True,
                    )
                    triggered.append(
                        {
                            "venue": venue,
                            "base": asset,
                            "symbol": symbol,
                            "reason": "time_stop_breakeven",
                            "qty": str(sell_qty),
                            "mark": str(mark),
                            "cost": str(blend),
                            "floor": str(floor),
                            "status": result.status.value,
                            "order_id": str(result.order_id)
                            if result.order_id
                            else None,
                            "error": result.message,
                        }
                    )
                    if result.status != OrderStatus.REJECTED:
                        self._position_opened_mono.pop(trail_key, None)
                continue

            # Untrusted / mark-seeded cost must never arm or exit a trail.
            if not self._has_trusted_cost(venue, asset):
                self._bump_skip("trail_no_trusted_cost")
                continue

            symbol = f"{asset}{self._quote}"
            mark = await self._mark_price(venue, symbol)
            if mark is None or mark <= 0:
                continue
            self._note_position_opened(venue, asset)
            st = self._trail_update_state(venue, asset, cost=cost, mark=mark)
            if st.get("soft_armed") and not st.get("triggered"):
                armed_now.append(f"{venue}:{asset}")

            sell_qty = _ZERO
            reason = ""
            limit_px: Decimal | None = None
            post_only = False

            if (
                st.get("newly_soft")
                and self._trail_partial_enabled
                and not st.get("soft_partial_done")
            ):
                free = await self._refresh_free(venue, symbol, asset, locked)
                cap = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                sell_qty = (cap * self._soft_partial).quantize(Decimal("0.00000001"))
                reason = "trail_soft_partial"
                st["soft_partial_done"] = True
                st["partial_done"] = True
            elif (
                st.get("newly_hard")
                and self._trail_partial_enabled
                and not st.get("hard_partial_done")
            ):
                free = await self._refresh_free(venue, symbol, asset, locked)
                cap = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                sell_qty = (cap * self._hard_partial).quantize(Decimal("0.00000001"))
                reason = "trail_hard_partial"
                st["hard_partial_done"] = True
            elif st.get("triggered"):
                free = await self._refresh_free(venue, symbol, asset, locked)
                sell_qty = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                reason = "trail_drawdown"
            elif st.get("time_stop_due"):
                floor = self._time_stop_floor_price(venue, asset)
                if floor is not None and mark >= floor:
                    free = await self._refresh_free(venue, symbol, asset, locked)
                    sell_qty = free
                    reason = "time_stop_breakeven"
                    limit_px = max(floor, mark * Decimal("0.999"))
                    post_only = True
                else:
                    self._bump_skip("time_stop_below_be")
                    continue
            else:
                continue

            if sell_qty <= 0 or sell_qty * mark < _MIN_LIVE_NOTIONAL:
                self._bump_skip("trail_dust")
                if reason == "trail_drawdown":
                    st["triggered"] = False
                if reason == "trail_soft_partial":
                    st["soft_partial_done"] = False
                    st["partial_done"] = False
                if reason == "trail_hard_partial":
                    st["hard_partial_done"] = False
                continue

            # Never trail-exit at a loss (fees included). Hold until profitable.
            ok_sell, gate_reason, be = self._sell_allowed_at(venue, asset, mark)
            if not ok_sell:
                self._bump_skip(gate_reason)
                if reason == "trail_drawdown":
                    st["triggered"] = False
                if reason == "trail_soft_partial":
                    st["soft_partial_done"] = False
                    st["partial_done"] = False
                if reason == "trail_hard_partial":
                    st["hard_partial_done"] = False
                continue

            cooldown_key = f"{venue}:{asset}:{reason}"
            last_try = self._exit_cooldown_mono.get(cooldown_key, 0.0)
            if time.monotonic() - last_try < 45.0:
                self._bump_skip("exit_cooldown")
                continue

            exit_px, post_only, quote_reason = await self._profitable_exit_quote(
                venue, asset, mark
            )
            if exit_px is None:
                self._bump_skip(f"exit_quote_{quote_reason}")
                if reason == "trail_drawdown":
                    st["triggered"] = False
                if reason == "trail_soft_partial":
                    st["soft_partial_done"] = False
                    st["partial_done"] = False
                if reason == "trail_hard_partial":
                    st["hard_partial_done"] = False
                continue
            if limit_px is None or limit_px < exit_px:
                limit_px = exit_px
            # Keep limit at least at the profitable quote (never below BE path).
            limit_px = max(limit_px, exit_px)
            self._exit_cooldown_mono[cooldown_key] = time.monotonic()
            logger.info(
                "TRAIL_EXIT_QUOTE venue=%s base=%s reason=%s quote=%s px=%s post_only=%s",
                venue,
                asset,
                reason,
                quote_reason,
                limit_px,
                post_only,
            )

            result = await self._submit_exit_sell(
                venue=venue,
                symbol=symbol,
                qty=sell_qty,
                mark=mark,
                reason=reason,
                limit_price=limit_px,
                post_only=post_only,
            )
            row = {
                "venue": venue,
                "base": asset,
                "symbol": symbol,
                "reason": reason,
                "qty": str(sell_qty),
                "mark": str(mark),
                "cost": str(cost),
                "status": result.status.value,
                "order_id": str(result.order_id) if result.order_id else None,
                "error": result.message,
            }
            triggered.append(row)
            if result.status == OrderStatus.REJECTED:
                if reason == "trail_drawdown":
                    st["triggered"] = False
                if reason == "trail_soft_partial":
                    st["soft_partial_done"] = False
                    st["partial_done"] = False
                if reason == "trail_hard_partial":
                    st["hard_partial_done"] = False
                self._bump_skip(f"{reason}_reject")
            else:
                logger.info(
                    "TRAIL_EXIT venue=%s base=%s reason=%s qty=%s mark=%s status=%s",
                    venue,
                    asset,
                    reason,
                    sell_qty,
                    mark,
                    result.status.value,
                )
                if reason in {"trail_drawdown", "time_stop_breakeven"}:
                    self._trail.pop(trail_key, None)
                    self._position_opened_mono.pop(trail_key, None)

        return {
            "ok": True,
            "enabled": self._trail_enabled,
            "venue": venue,
            "armed": armed_now,
            "triggered": triggered,
            "alerts": list(self._alerts[-10:]),
            "states": self._trail_states_public(),
        }

    async def manage_dust_positions(
        self, venue: str = "bitvavo"
    ) -> dict[str, Any]:
        """Top up sub-min positions, else exit near break-even; trim over-cap bags."""
        policy = self._dust_policy
        if policy in {"", "off", "none"}:
            return {"ok": True, "policy": policy, "actions": []}
        min_notional = Decimal(
            str(getattr(self._settings, "paper_maker_min_notional_eur", 40) or 40)
        )
        bals = await self._fetch_balances_cached(venue)
        actions: list[dict[str, Any]] = []
        sized: list[tuple[str, Decimal, Decimal, Decimal]] = []
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote or asset in self._exclude_bases:
                continue
            if self._allowed_bases is not None and asset not in self._allowed_bases:
                continue
            free = Decimal(str(getattr(bal, "free", 0) or 0))
            if free <= 0:
                continue
            symbol = f"{asset}{self._quote}"
            mark = await self._mark_price(venue, symbol)
            if mark is None or mark <= 0:
                continue
            notional = free * mark
            if notional < _MIN_LIVE_NOTIONAL:
                continue
            sized.append((asset, free, mark, notional))

        held = {a for a, _f, _m, n in sized if n >= min_notional * Decimal("0.5")}
        over_cap = self._max_alt_bases > 0 and len(held) > self._max_alt_bases

        for asset, free, mark, notional in sized:
            symbol = f"{asset}{self._quote}"
            need_eur = min_notional - notional
            is_sub_min = notional < min_notional
            is_trim_target = False
            if over_cap and sized:
                smallest = min(sized, key=lambda r: r[3])
                is_trim_target = asset == smallest[0] and notional <= (
                    min_notional * Decimal("2")
                )
            if not is_sub_min and not is_trim_target:
                continue

            did = None
            if (
                is_sub_min
                and policy in {"top_up", "top_up_or_exit"}
                and not self._buys_blocked
                and not self._daily_kill_active
            ):
                can_add = asset in held or (
                    self._max_alt_bases <= 0 or len(held) < self._max_alt_bases
                )
                live_eur = await self._live_free(venue, self._quote)
                spend = min(
                    need_eur * Decimal("1.01"),
                    live_eur,
                    self._venue_budget_remaining(venue),
                )
                if can_add and spend >= _MIN_LIVE_NOTIONAL:
                    qty = (spend / mark).quantize(Decimal("0.00000001"))
                    px = (mark * Decimal("0.999")).quantize(Decimal("0.00000001"))
                    req = OrderRequest(
                        opportunity_id=uuid4(),
                        symbol=symbol,
                        side=OpportunitySide.BUY,
                        quantity=qty,
                        limit_price=px,
                        metadata={
                            "venue": venue,
                            "exchange": venue,
                            "post_only": True,
                            "dust_top_up": True,
                            "ladder_leg": True,
                            "strategy": "dust_top_up",
                        },
                    )
                    result = await self.execute(
                        req, strategy="dust_top_up", order_type=OrderType.LIMIT
                    )
                    did = {
                        "action": "top_up",
                        "base": asset,
                        "status": str(result.status),
                        "qty": str(qty),
                    }
                    self._bump_skip("dust_top_up")
            if did is None and policy in {"exit_breakeven", "top_up_or_exit"}:
                # No slack below break-even — user rule: always sell at a profit after fees.
                be = self._break_even_sell_price(venue, asset)
                floor = be
                if floor is not None and mark >= floor:
                    reason = (
                        "inventory_trim_breakeven"
                        if is_trim_target and not is_sub_min
                        else "dust_exit_breakeven"
                    )
                    result = await self._submit_exit_sell(
                        venue=venue,
                        symbol=symbol,
                        qty=free,
                        mark=mark,
                        reason=reason,
                        limit_price=max(be or mark, mark * Decimal("0.999")),
                        post_only=True,
                    )
                    did = {
                        "action": (
                            "inventory_trim"
                            if is_trim_target and not is_sub_min
                            else "exit_breakeven"
                        ),
                        "base": asset,
                        "status": str(result.status),
                        "qty": str(free),
                        "be": str(be) if be is not None else None,
                        "floor": str(floor),
                    }
                    self._bump_skip(
                        "inventory_trim"
                        if is_trim_target and not is_sub_min
                        else "dust_exit_breakeven"
                    )
            if did is not None:
                actions.append(did)
                logger.info("DUST_POLICY %s", did)
        return {"ok": True, "policy": policy, "actions": actions}

    async def execute(
        self,
        order_request: OrderRequest,
        *,
        order_book: Any = None,
        strategy: str = "",
        order_type: OrderType = OrderType.LIMIT,
    ) -> ExecutionResult:
        meta = dict(order_request.metadata or {})
        post_only = bool(meta.get("post_only"))
        if post_only and not self._live_maker:
            return await super().execute(
                order_request,
                order_book=order_book,
                strategy=strategy,
                order_type=order_type,
            )

        if meta.get("buy_only") and not bool(
            getattr(self._settings, "paper_maker_allow_buy_only", True)
        ):
            self._bump_skip("buy_only_disabled")
            return await self._reject_before_live(
                order_request,
                reason="BUY_ONLY_DISABLED",
                message="winst-mode rejects buy-only quotes",
            )

        symbol = order_request.symbol.upper().replace("/", "").replace("-", "")
        base = infer_base_asset(symbol)
        if base in self._exclude_bases or symbol.startswith("BTC"):
            self._bump_skip("excluded_base")
            return await self._reject_before_live(
                order_request, reason="EXCLUDED_BASE", message=f"base {base} excluded"
            )

        venue = self._resolve_venue(order_request)
        if venue not in self._execute_venues:
            self._bump_skip("venue_not_live")
            return await self._reject_before_live(
                order_request,
                reason="VENUE_NOT_LIVE",
                message=f"venue {venue or 'unknown'} has no live keys in this session",
            )

        remaining = self._venue_budget_remaining(venue)
        side_is_buy = order_request.side == OpportunitySide.BUY
        if side_is_buy and self._daily_kill_active:
            self._bump_skip("daily_kill")
            return await self._reject_before_live(
                order_request,
                reason="DAILY_KILL",
                message=(
                    f"realized PnL {self.realized_trade_pnl_eur} "
                    f"hit -{self._daily_kill_eur} EUR kill; buys blocked"
                ),
            )
        if (
            side_is_buy
            and self._regime_block_buys
            and self._buys_blocked
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
        ):
            self._bump_skip("regime_block_buys")
            return await self._reject_before_live(
                order_request,
                reason="REGIME_BLOCK_BUYS",
                message="buys blocked while regime is reduce-only/toxic",
            )
        if side_is_buy and remaining < _MIN_LIVE_NOTIONAL:
            self._bump_skip("budget_exhausted")
            return await self._reject_before_live(
                order_request,
                reason="BUDGET_EXHAUSTED",
                message=f"micro pocket free EUR {remaining}",
            )
        # Trend profile: at most N distinct alt bases — add to existing, don't spray.
        if side_is_buy and self._max_alt_bases > 0:
            held = self._held_alt_bases()
            if base not in held and len(held) >= self._max_alt_bases:
                self._bump_skip("max_alt_bases")
                return await self._reject_before_live(
                    order_request,
                    reason="MAX_ALT_BASES",
                    message=(
                        f"already holding {sorted(held)} "
                        f"(max {self._max_alt_bases} bases for trail concentration)"
                    ),
                )
        # Correlation cluster: max N from ADA/ATOM/NEAR/SOL/XRP group.
        if (
            side_is_buy
            and self._max_per_corr > 0
            and base in self._corr_group
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
        ):
            held = self._held_alt_bases()
            if base not in held and self._corr_held_count(adding=base) > self._max_per_corr:
                self._bump_skip("corr_group_cap")
                return await self._reject_before_live(
                    order_request,
                    reason="CORR_GROUP_CAP",
                    message=(
                        f"corr group already at {self._max_per_corr}: "
                        f"{sorted(held & self._corr_group)}"
                    ),
                )
        if (
            side_is_buy
            and self._momentum_enabled
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
            and not meta.get("trail_take_profit")
        ):
            if not self._momentum_ok(symbol):
                self._bump_skip("momentum_block")
                return await self._reject_before_live(
                    order_request,
                    reason="MOMENTUM_BLOCK",
                    message=(
                        f"mark momentum below {self._momentum_min} "
                        f"over ~{self._momentum_samples} samples"
                    ),
                )

        px = Decimal(str(order_request.limit_price or 0))
        qty = Decimal(str(order_request.quantity or 0))
        if px <= 0 or qty <= 0:
            self._bump_skip("bad_size")
            return await self._reject_before_live(
                order_request, reason="BAD_SIZE", message="quantity/price required"
            )

        # Size against live Bitvavo free balances (paper pocket can lag fills).
        if side_is_buy:
            live_eur = await self._live_free(venue, self._quote)
            spend_cap = min(remaining, live_eur) if live_eur > 0 else remaining
            notional = qty * px
            if notional > spend_cap:
                qty = (spend_cap / px).quantize(Decimal("0.00000001"))
                notional = qty * px
                if qty <= 0 or notional < _MIN_LIVE_NOTIONAL:
                    self._bump_skip("insufficient_live_quote")
                    return await self._reject_before_live(
                        order_request,
                        reason="INSUFFICIENT_LIVE_QUOTE",
                        message=f"live {self._quote} free {live_eur} pocket {remaining}",
                    )
                order_request = order_request.model_copy(update={"quantity": qty})
            # Ladder entries: first leg joins the strategy bid (touch), deeper
            # legs only as backup. Using mark*(1-dip) previously parked all
            # bids ~1% below market so they never filled.
            if (
                self._ladder_enabled
                and post_only
                and not meta.get("ladder_leg")
                and not meta.get("dust_top_up")
                and len(self._ladder_pcts) >= 2
            ):
                ref = px if px > 0 else await self._mark_price(venue, symbol)
                if ref is None or ref <= 0:
                    ref = px
                # Post-only safety: never cross the ask.
                best_ask = _ZERO
                best_bid = _ZERO
                if order_book is not None:
                    try:
                        if order_book.asks:
                            best_ask = Decimal(str(order_book.asks[0].price))
                        if order_book.bids:
                            best_bid = Decimal(str(order_book.bids[0].price))
                    except Exception:  # noqa: BLE001
                        best_ask = _ZERO
                        best_bid = _ZERO
                if best_bid > 0:
                    ref = max(ref, best_bid)
                leg_qty = (qty / Decimal(len(self._ladder_pcts))).quantize(
                    Decimal("0.00000001")
                )
                if leg_qty * ref >= _MIN_LIVE_NOTIONAL:
                    last_result: ExecutionResult | None = None
                    for dip in self._ladder_pcts:
                        leg_px = (ref * (Decimal("1") - dip)).quantize(
                            Decimal("0.00000001")
                        )
                        if best_ask > 0 and leg_px >= best_ask:
                            leg_px = (best_ask * Decimal("0.9999")).quantize(
                                Decimal("0.00000001")
                            )
                        if leg_px <= 0:
                            continue
                        leg_req = order_request.model_copy(
                            update={
                                "id": uuid4(),
                                "quantity": leg_qty,
                                "limit_price": leg_px,
                                "metadata": {
                                    **meta,
                                    "ladder_leg": True,
                                    "ladder_dip_pct": str(dip),
                                    "post_only": True,
                                    "venue": venue,
                                },
                            }
                        )
                        last_result = await self.execute(
                            leg_req,
                            order_book=order_book,
                            strategy=strategy or "ladder_buy",
                            order_type=order_type,
                        )
                    self._bump_skip("ladder_buy")
                    if last_result is not None:
                        return last_result
        else:
            live_base = await self._live_free(venue, base)
            if live_base <= 0:
                self._bump_skip("insufficient_live_base")
                return await self._reject_before_live(
                    order_request,
                    reason="INSUFFICIENT_LIVE_BASE",
                    message=f"live {base} free {live_base}",
                )
            if qty > live_base:
                qty = live_base.quantize(Decimal("0.00000001"))
                order_request = order_request.model_copy(update={"quantity": qty})
            # Hard floor: NEVER sell below fee-adjusted cost + profit buffer.
            # Applies to trail exits too — no loss-taking sells.
            be = self._break_even_sell_price(venue, base)
            if be is None:
                self._bump_skip("sell_no_trusted_cost")
                return await self._reject_before_live(
                    order_request,
                    reason="SELL_NO_TRUSTED_COST",
                    message=(
                        f"no trusted cost basis for {venue}:{base}; "
                        "refusing sell until buy fill or trade history is known"
                    ),
                )
            if be > px:
                px = be
                order_request = order_request.model_copy(update={"limit_price": px})
            if order_book is not None:
                try:
                    best_bid = (
                        Decimal(str(order_book.bids[0].price))
                        if order_book.bids
                        else _ZERO
                    )
                except Exception:  # noqa: BLE001
                    best_bid = _ZERO
                # If bid is already above break-even, crossing is a profitable fill.
                # Only block when the bid itself is still below break-even.
                if best_bid > 0 and best_bid < be:
                    self._bump_skip("sell_below_break_even")
                    return await self._reject_before_live(
                        order_request,
                        reason="SELL_BELOW_BREAK_EVEN",
                        message=(
                            f"best bid {best_bid} still below break-even {be}; "
                            "holding for profitable exit"
                        ),
                    )
                if best_bid > 0 and px < best_bid and best_bid >= be:
                    # Lift limit to the bid so the exit actually fills.
                    px = best_bid
                    order_request = order_request.model_copy(update={"limit_price": px})
                    meta = dict(order_request.metadata or {})
                    meta["post_only"] = False
                    order_request = order_request.model_copy(update={"metadata": meta})
            # Even without a book, refuse a sell priced below break-even.
            if px < be:
                self._bump_skip("sell_below_break_even")
                return await self._reject_before_live(
                    order_request,
                    reason="SELL_BELOW_BREAK_EVEN",
                    message=f"limit {px} below break-even {be}",
                )
            notional = qty * px
            if notional < _MIN_LIVE_NOTIONAL:
                self._bump_skip("sell_below_min_notional")
                return await self._reject_before_live(
                    order_request,
                    reason="SELL_BELOW_MIN",
                    message=f"sell notional {notional} below {_MIN_LIVE_NOTIONAL}",
                )

        side = "buy" if side_is_buy else "sell"
        payload = {
            "venue": venue,
            "symbol": symbol,
            "side": side,
            "quantity": str(qty),
            "limit_price": str(px) if px > 0 else None,
            "notional_eur": str(notional.quantize(Decimal("0.01"))),
            "confirm": True,
            "post_only": post_only,
        }
        out = await self._live.submit(payload, confirm=True)
        self._invalidate_bal_cache()
        row = {
            "symbol": symbol,
            "venue": venue,
            "side": side,
            "requested_qty": str(qty),
            "requested_notional": str(notional),
            "source": "live",
            "result": out,
        }
        self.live_trades.append(row)

        if not out.get("executed"):
            self._bump_skip(str(out.get("reason") or "live_not_executed"))
            # Keep pocket honest after rejected/failed attempts.
            await self.reconcile_from_exchange(venue)
            return await self._reject_before_live(
                order_request,
                reason="LIVE_NOT_EXECUTED",
                message=str(out.get("message") or out.get("reason") or "not executed"),
            )

        order_row = out.get("order") or {}
        filled = Decimal(str(order_row.get("filled_quantity") or 0))
        avg = Decimal(str(order_row.get("average_price") or px or 0))
        if filled <= 0:
            filled, avg = await self._poll_fill(
                venue=venue,
                symbol=symbol,
                exchange_order_id=(
                    str(order_row.get("exchange_order_id"))
                    if order_row.get("exchange_order_id")
                    else None
                ),
                fallback_price=px,
            )

        if filled <= 0 or avg <= 0:
            # Resting order accepted — track for fill/cancel; sync locked balances.
            self._bump_skip("live_resting")
            self._track_resting(
                venue=venue,
                symbol=symbol,
                side=side,
                exchange_order_id=(
                    str(order_row.get("exchange_order_id"))
                    if order_row.get("exchange_order_id")
                    else None
                ),
                quantity=qty,
                price=px,
                strategy=strategy,
                opportunity_id=order_request.opportunity_id,
            )
            await self.reconcile_from_exchange(venue)
            return await self._reject_before_live(
                order_request,
                reason="LIVE_RESTING",
                message="order accepted on exchange; waiting for fill (pocket synced)",
            )

        fill_notional = filled * avg
        self._turnover += fill_notional
        return await self._mirror_live_fill(
            order_request,
            filled_qty=filled,
            average_price=avg,
            venue=venue,
            strategy=strategy,
            exchange_order_id=order_row.get("exchange_order_id"),
        )

    async def _reject_before_live(
        self,
        order_request: OrderRequest,
        *,
        reason: str,
        message: str,
    ) -> ExecutionResult:
        side = (
            OrderSide.BUY
            if order_request.side == OpportunitySide.BUY
            else OrderSide.SELL
        )
        order = Order(
            id=order_request.id,
            strategy=str((order_request.metadata or {}).get("strategy") or ""),
            symbol=order_request.symbol,
            side=side,
            order_type=OrderType.LIMIT,
            requested_quantity=order_request.quantity,
            requested_price=order_request.limit_price,
            status=OrderStatus.PENDING,
            exchange="micro_bridge",
            opportunity_id=order_request.opportunity_id,
            client_order_id=order_request.client_order_id,
            metadata={**(order_request.metadata or {}), "executor": self.name},
        )
        self._orders.add(order)
        return self._reject(order, order_request, reason=reason, message=message)

    async def _mirror_live_fill(
        self,
        order_request: OrderRequest,
        *,
        filled_qty: Decimal,
        average_price: Decimal,
        venue: str,
        strategy: str,
        exchange_order_id: Any,
    ) -> ExecutionResult:
        """Keep paper portfolio/risk in sync with the live pocket."""
        side = (
            OrderSide.BUY
            if order_request.side == OpportunitySide.BUY
            else OrderSide.SELL
        )
        order = Order(
            id=order_request.id,
            strategy=strategy or str((order_request.metadata or {}).get("strategy") or ""),
            symbol=order_request.symbol,
            side=side,
            order_type=OrderType.MARKET,
            requested_quantity=filled_qty,
            requested_price=average_price,
            status=OrderStatus.PENDING,
            exchange=venue,
            opportunity_id=order_request.opportunity_id,
            client_order_id=order_request.client_order_id
            or f"micro-mirror-{uuid4().hex[:12]}",
            metadata={
                **(order_request.metadata or {}),
                "executor": self.name,
                "live_mirrored": True,
                "venue": venue,
            },
        )
        self._orders.add(order)
        self._orders.set_status(order.id, OrderStatus.OPEN)

        fee_rate = self._fee_rate_for(order)
        fee = filled_qty * average_price * fee_rate
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=filled_qty,
            price=average_price,
            fee=fee,
            fee_asset=self._quote_asset_for(order),
            slippage=_ZERO,
            exchange=venue,
            metadata={
                "live_mirrored": True,
                "fee_rate": str(fee_rate),
                "exchange_order_id": exchange_order_id,
            },
        )
        self._orders.attach_fill(order.id, fill)
        self._fills.apply(order, fill)
        try:
            self._apply_venue_ledger(order, filled_qty, average_price, fee)
        except Exception:  # noqa: BLE001
            logger.exception("micro_bridge venue ledger sync failed")
        self._portfolio.set_mark_price(order.symbol, average_price)
        self._invalidate_bal_cache()
        self._record_realized_fill(
            side=side,
            symbol=order.symbol,
            qty=filled_qty,
            price=average_price,
            fee=fee,
            venue=venue,
        )
        self.session_live_fill_count += 1
        self.session_live_transaction_count += 1
        self.live_fill_count = self.session_live_fill_count
        self.live_transaction_count = self.session_live_transaction_count

        result = ExecutionResult(
            order_id=order.id,
            opportunity_id=order_request.opportunity_id,
            status=OrderStatus.FILLED,
            filled_quantity=filled_qty,
            average_price=average_price,
            fees_usd=fee,
            message="Live micro fill mirrored into paper pocket",
            metadata={
                "executor": self.name,
                "exchange": venue,
                "real_exchange_order": True,
                "micro_live": True,
                "fee": str(fee),
                "exchange_order_id": exchange_order_id,
            },
        )
        self.history.append(result)
        logger.info(
            "MICRO_LIVE_FILL symbol=%s venue=%s qty=%s px=%s turnover=%s free=%s",
            order.symbol,
            venue,
            filled_qty,
            average_price,
            self._turnover,
            self.budget_remaining,
        )
        return result

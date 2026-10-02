"""Runner/manager for the ignition early-signal sleeve (paper or live OKX)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.live.momentum_ignition import (
    FALLBACK_SNIPER_UNIVERSE,
    IgnitionConfig,
    IgnitionPosition,
    RS_DESK_BASES,
    default_config,
    effective_trail_pct,
    evaluate_ignition,
    fill_px,
    resolve_universe,
    trail_exit,
)
from bot.live.momentum_runner import CandleFeed, LiveGateway, engine_settings_for_desk, parse_venues
from bot.live.momentum_short_weakest import fetch_daily_ohlc, load_alphai_view

logger = logging.getLogger("bot.live.momentum_ignition_runner")

_EQUITY_CURVE_MAX = 2016
_EQUITY_CURVE_MIN_GAP_SEC = 5.0
_MIN_ORDER_EUR = 5.0
_TAKER_CROSS = 0.002
_OHLC_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ign-ohlc")


@dataclass(frozen=True)
class _Fill:
    qty: float
    avg_price: float
    fee_eur: float


def _flag_path(state_path: str) -> Path:
    return Path(state_path).with_name("momentum_ignition_running.json")


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


def config_from_settings(settings: Settings | None = None) -> IgnitionConfig:
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

    hours_raw = str(
        getattr(settings, "momentum_ignition_decision_hours_utc", "0") or "0"
    )
    hours = tuple(
        sorted(
            {
                int(x.strip())
                for x in hours_raw.split(",")
                if x.strip().isdigit() and 0 <= int(x.strip()) <= 23
            }
        )
    ) or (0,)

    mode = str(
        getattr(settings, "momentum_ignition_universe_mode", base.universe_mode)
        or base.universe_mode
    ).strip().lower()
    excl_raw = str(getattr(settings, "momentum_ignition_exclude_bases", "") or "")
    if excl_raw.strip():
        exclude = tuple(
            x.strip().upper() for x in excl_raw.split(",") if x.strip()
        )
    else:
        exclude = RS_DESK_BASES
    univ_raw = str(getattr(settings, "momentum_ignition_universe", "") or "")
    if univ_raw.strip():
        universe = tuple(
            x.strip().upper() for x in univ_raw.split(",") if x.strip()
        )
        if mode != "custom":
            mode = "custom"
    else:
        universe = FALLBACK_SNIPER_UNIVERSE

    return replace(
        base,
        book_eur=_f("momentum_ignition_book_eur", base.book_eur),
        max_positions=_i("momentum_ignition_max_positions", base.max_positions),
        deploy_frac=_f("momentum_ignition_deploy_frac", base.deploy_frac),
        compound_sizing=_b(
            "momentum_ignition_compound_sizing", base.compound_sizing
        ),
        max_book_eur=_f("momentum_ignition_max_book_eur", base.max_book_eur),
        entry_mode=str(
            getattr(settings, "momentum_ignition_entry_mode", base.entry_mode)
            or base.entry_mode
        )
        .strip()
        .lower(),
        trail_pct=_f("momentum_ignition_trail_pct", base.trail_pct),
        trail_ratchet_arm_pct=_f(
            "momentum_ignition_trail_ratchet_arm_pct", base.trail_ratchet_arm_pct
        ),
        trail_ratchet_pct=_f(
            "momentum_ignition_trail_ratchet_pct", base.trail_ratchet_pct
        ),
        time_max_days=_f("momentum_ignition_time_max_days", base.time_max_days),
        quiet_max=_f("momentum_ignition_quiet_max", base.quiet_max),
        day_ret_min=_f("momentum_ignition_day_ret_min", base.day_ret_min),
        vol_mult_min=_f("momentum_ignition_vol_mult_min", base.vol_mult_min),
        min_median_qvol_eur=_f(
            "momentum_ignition_min_median_qvol_eur", base.min_median_qvol_eur
        ),
        min_day_qvol_eur=_f("momentum_ignition_min_day_qvol_eur", base.min_day_qvol_eur),
        require_btc_sma=_b("momentum_ignition_require_btc_sma", base.require_btc_sma),
        min_points=_i("momentum_ignition_min_points", base.min_points),
        coil_entry_enabled=_b(
            "momentum_ignition_coil_entry_enabled", base.coil_entry_enabled
        ),
        coil_breakout_days=_i(
            "momentum_ignition_coil_breakout_days", base.coil_breakout_days
        ),
        coil_day_ret_min=_f(
            "momentum_ignition_coil_day_ret_min", base.coil_day_ret_min
        ),
        coil_vol_mult_min=_f(
            "momentum_ignition_coil_vol_mult_min", base.coil_vol_mult_min
        ),
        coil_quiet_max=_f("momentum_ignition_coil_quiet_max", base.coil_quiet_max),
        coil_min_close_loc=_f(
            "momentum_ignition_coil_min_close_loc", base.coil_min_close_loc
        ),
        coil_compress_ratio=_f(
            "momentum_ignition_coil_compress_ratio", base.coil_compress_ratio
        ),
        coil_trail_pct=_f("momentum_ignition_coil_trail_pct", base.coil_trail_pct),
        universe_mode=mode,
        liquid_top_n=_i("momentum_ignition_liquid_top_n", base.liquid_top_n),
        exclude_bases=exclude,
        universe=universe,
        universe_refresh_sec=_f(
            "momentum_ignition_universe_refresh_sec", base.universe_refresh_sec
        ),
        tick_sec=_f("momentum_ignition_tick_sec", base.tick_sec),
        decision_interval_sec=_f(
            "momentum_ignition_decision_interval_sec", base.decision_interval_sec
        ),
        decision_hours_utc=hours,
        requires_alphai_pick=_b(
            "momentum_ignition_requires_alphai_pick", base.requires_alphai_pick
        ),
        block_alphai_avoid=_b(
            "momentum_ignition_block_alphai_avoid", base.block_alphai_avoid
        ),
    )


class IgnitionPaperRunner:
    """Ignition long book on OKX — paper fills, or live when gateways are armed."""

    def __init__(
        self,
        cfg: IgnitionConfig,
        *,
        state_path: str,
        ledger_path: str,
        venues: tuple[str, ...] = ("okx",),
        allow_live: bool = False,
        gateways: Mapping[str, Any] | None = None,
        alphai_path: str = "./data/alphai/daily_recommendations.json",
        alphai_volatile_path: str = "./data/alphai/volatile_recommendations.json",
    ) -> None:
        self.cfg = cfg
        self.state_path = state_path
        self.ledger_path = ledger_path
        self.alphai_path = str(alphai_path or "")
        self.alphai_volatile_path = str(alphai_volatile_path or "")
        self.venues = parse_venues(venues) or ("okx",)
        self._gws: dict[str, Any] = dict(gateways or {})
        self._allow_live_requested = bool(allow_live)
        live = bool(allow_live) and bool(self._gws)
        self.allow_live = live
        self.dry_run = not live
        self.paper_only = not live
        self.cash_eur = float(cfg.book_eur)
        self.realized_total_eur = 0.0
        self.day_realized_eur = 0.0
        self._day_key = ""
        self.positions: list[IgnitionPosition] = []
        self.last_decision: dict[str, Any] = {}
        self.equity_curve: list[list[float]] = []
        self.marks: dict[str, float] = {}
        self.mark_ts: dict[str, float] = {}
        self._feed = CandleFeed()
        self._decide_lock = asyncio.Lock()
        self._last_curve_save = 0.0
        # Last read of target-venue free quote vs crypto MTM (not paper equity).
        self._venue_truth: dict[str, Any] = {}
        self._venue_truth_ts = 0.0
        self._universe_refreshed_at = 0.0
        self._load_state()
        # Resolve sniper universe once at construct (network refresh in decide).
        self.cfg = replace(
            self.cfg, universe=resolve_universe(self.cfg, volume_by_base=None)
        )

    def _desk(self) -> str:
        return "ignition" if self.allow_live else "ignition_paper"

    def _primary_venue(self) -> str:
        return self.venues[0] if self.venues else "okx"

    def _primary_gw(self) -> Any | None:
        for v in self.venues:
            if v in self._gws:
                return self._gws[v]
        return next(iter(self._gws.values()), None)

    def discard_paper_positions(self) -> int:
        """Drop synthetic lots before arming live venue orders."""
        n = len(self.positions)
        self.positions = []
        return n

    def _rebase_equity_curve(self) -> None:
        eq = round(self._equity_now(), 2)
        now_ms = time.time() * 1000.0
        self.equity_curve = [[round(now_ms), eq]]
        self._last_curve_save = 0.0

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
        self.positions = [
            IgnitionPosition.from_dict(row) for row in (raw.get("positions") or [])
        ]
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

    def _save_state(self) -> None:
        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.state_path).write_text(
            json.dumps(
                {
                    "cash_eur": self.cash_eur,
                    "realized_total_eur": self.realized_total_eur,
                    "day_realized_eur": self.day_realized_eur,
                    "positions": [p.to_dict() for p in self.positions],
                    "last_decision": self.last_decision,
                    "equity_curve": [
                        [round(float(t), 1), round(float(eq), 2)]
                        for t, eq in self.equity_curve[-_EQUITY_CURVE_MAX:]
                    ],
                    "paper_only": self.paper_only,
                    "allow_live": self.allow_live,
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
            mark = float(self.marks.get(p.base) or p.entry_price or 0.0)
            total += p.unrealized_net(mark, self.cfg.fee_rt)
        return total

    def _equity_now(self) -> float:
        return self.cash_eur + self._deployed() + self._unrealized()

    def _sample_equity(self, *, persist: bool = False) -> None:
        eq = round(self._equity_now(), 2)
        now_ms = time.time() * 1000.0
        gap_ms = _EQUITY_CURVE_MIN_GAP_SEC * 1000.0
        if self.equity_curve:
            last_t, _ = self.equity_curve[-1]
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

    async def _refresh_marks(self) -> None:
        bases = {p.base for p in self.positions} | {"BTC"}
        want = (self.last_decision or {}).get("want")
        if want:
            bases.add(str(want).upper())
        for base in bases:
            try:
                px = await self._feed.last_price(base)
            except Exception:  # noqa: BLE001
                px = None
            if px and float(px) > 0:
                self.marks[base] = float(px)
                self.mark_ts[base] = time.time()
        await self._refresh_venue_truth()
        self._sample_equity()

    async def _refresh_venue_truth(self, *, force: bool = False) -> dict[str, Any]:
        """Mark target-venue balances so paper equity is not confused with live powder."""
        now = time.time()
        if not force and self._venue_truth and (now - self._venue_truth_ts) < 20.0:
            return dict(self._venue_truth)
        venue = self._primary_venue()
        truth: dict[str, Any] = {
            "venue": venue,
            "online": False,
            "free_quote_eur": None,
            "inventory_mtm_eur": None,
            "total_value_eur": None,
            "deployable_live_eur": None,
            "top_inventory": [],
            "inventory_advice": "unknown",
            "inventory_advice_nl": "",
            "error": None,
        }
        try:
            from bot.funding.multi_venue import (
                fetch_live_venue_balances,
                fetch_public_eur_prices,
                summarize_venue_snapshot,
            )

            settings = get_settings()
            prices = await fetch_public_eur_prices()
            snaps = await fetch_live_venue_balances(
                settings, [venue], prices_eur=prices or None
            )
            snap = snaps[0] if snaps else None
            if snap is None:
                truth["error"] = "no_snapshot"
            elif not snap.online:
                truth["error"] = snap.error or "offline"
            else:
                quote = (getattr(settings, "paper_quote_asset", None) or "EUR").upper()
                summary = summarize_venue_snapshot(snap, quote=quote, top_n=3)
                free_q = float(summary["free_quote_eur"])
                inv = float(summary["inventory_mtm_eur"])
                total = float(summary["total_value_eur"])
                top = list(summary.get("top_inventory") or [])
                # Ignition live buys need quote. Inventory MTM is not dry powder.
                min_clip = max(25.0, float(self.cfg.min_notional_eur or 25.0))
                if free_q >= min_clip:
                    advice = "powder_ready"
                    mode = "LIVE" if self.allow_live else "paper"
                    advice_nl = (
                        f"{venue.upper()} heeft €{free_q:,.0f} vrije quote — "
                        f"dry powder klaar voor ignition ({mode})."
                    )
                elif inv >= min_clip:
                    top_asset = str((top[0] or {}).get("asset") or "alt") if top else "alt"
                    top_val = float((top[0] or {}).get("value_eur") or inv) if top else inv
                    advice = "sell_inventory_for_powder"
                    advice_nl = (
                        f"{venue.upper()} free quote is slechts €{free_q:.2f}; "
                        f"~€{inv:,.0f} zit in inventory (o.a. {top_asset} ~€{top_val:,.0f}). "
                        "Voor ignition-engine: verkopen naar EUR geeft deployable powder; "
                        "aanhouden past alleen als residual/RS-sleeve die bag bewust houdt."
                    )
                else:
                    advice = "thin_venue"
                    advice_nl = (
                        f"{venue.upper()} heeft nauwelijks quote én weinig gemarkeerde inventory "
                        f"(totaal ~€{total:,.0f})."
                    )
                truth.update(
                    {
                        "online": True,
                        "free_quote_eur": round(free_q, 2),
                        "inventory_mtm_eur": round(inv, 2),
                        "total_value_eur": round(total, 2),
                        "deployable_live_eur": round(free_q, 2),
                        "top_inventory": top,
                        "inventory_advice": advice,
                        "inventory_advice_nl": advice_nl,
                        "unmarked_assets": int(summary.get("unmarked_assets") or 0),
                        "error": None,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ignition venue truth refresh failed: %s", type(exc).__name__)
            truth["error"] = type(exc).__name__
        self._venue_truth = truth
        self._venue_truth_ts = now
        return dict(truth)

    def _venue_caption_suffix(self) -> str:
        truth = self._venue_truth or {}
        venue = str(truth.get("venue") or self._primary_venue()).upper()
        mode = "LIVE" if self.allow_live else "paper"
        if not truth.get("online"):
            return f" Target {venue} ({mode})."
        free_q = truth.get("free_quote_eur")
        inv = truth.get("inventory_mtm_eur")
        advice = str(truth.get("inventory_advice") or "")
        bits = [f"Target {venue} ({mode})"]
        if free_q is not None:
            bits.append(f"live vrij €{float(free_q):.2f}")
        if inv is not None:
            bits.append(f"inventory MTM €{float(inv):,.0f}")
        if advice == "sell_inventory_for_powder":
            bits.append("powder=verkopen inventory→EUR")
        elif advice == "powder_ready":
            bits.append("live powder ok")
        return ". " + " · ".join(bits) + "."

    async def _venue_quote_eur(self) -> float | None:
        gw = self._primary_gw()
        if self.dry_run or gw is None or not hasattr(gw, "quote_balance_eur"):
            truth = self._venue_truth or {}
            if truth.get("free_quote_eur") is not None:
                return float(truth["free_quote_eur"])
            return None
        try:
            raw = await gw.quote_balance_eur()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ignition quote balance failed: %s", exc)
            return None
        if raw is None:
            return None
        return max(0.0, float(raw))

    async def _sync_cash_from_venue(self) -> float | None:
        """Align book cash with free venue EUR when live."""
        if self.dry_run:
            return None
        venue_eur = await self._venue_quote_eur()
        if venue_eur is None:
            await self._refresh_venue_truth(force=True)
            venue_eur = await self._venue_quote_eur()
        if venue_eur is None:
            return None
        prev = float(self.cash_eur)
        synced = max(0.0, float(venue_eur))
        if abs(prev - synced) >= 0.01:
            logger.info("ignition cash sync book %.2f -> venue %.2f", prev, synced)
            self.cash_eur = synced
            if abs(prev - synced) >= 500.0:
                self._rebase_equity_curve()
        return synced

    async def _decision_cash(self) -> float:
        await self._sync_cash_from_venue()
        cash = float(self.cash_eur)
        if self.dry_run:
            return cash
        venue_eur = await self._venue_quote_eur()
        if venue_eur is None:
            return cash
        left = max(0.0, float(venue_eur))
        if left > 0:
            buffer = max(15.0, left * float(self.cfg.fee_rt or 0.003))
            left = max(0.0, left - buffer)
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
            logger.warning("ignition book %s failed: %s", symbol, exc)
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
            logger.warning("ignition %s %s rejected: %s", side, symbol, exc)
            return None
        deadline = time.time() + 20.0
        while getattr(state, "status", "") == "open" and time.time() < deadline:
            await asyncio.sleep(1.0)
            try:
                state = await gw.fetch_order(state.order_id, symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ignition fetch_order %s: %s", symbol, exc)
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
        fee_base = float(getattr(state, "fee_base_qty", 0.0) or 0.0)
        net_qty = max(0.0, filled - fee_base) if side == "buy" else filled
        return _Fill(qty=net_qty, avg_price=float(avg), fee_eur=fee)

    def _fetch_volume_map(self) -> dict[str, float]:
        """Bitvavo 24h EUR quote volume for all markets (sniper ranking)."""
        from bot.live.tape_confirm import fetch_bitvavo_24h

        try:
            rows = fetch_bitvavo_24h(())  # empty → all EUR markets
        except Exception as exc:  # noqa: BLE001
            logger.warning("ignition 24h volume fetch failed: %s", exc)
            return {}
        return {str(b).upper(): float(r.volume_eur) for b, r in rows.items()}

    def _refresh_universe(self, *, force: bool = False) -> tuple[str, ...]:
        mode = str(self.cfg.universe_mode or "ex_desk").lower()
        if mode == "custom":
            return self.cfg.universe
        interval = float(self.cfg.universe_refresh_sec or 0.0)
        now = time.time()
        due = force or self._universe_refreshed_at <= 0
        if not due and interval > 0 and (now - self._universe_refreshed_at) >= interval:
            due = True
        if not due and interval <= 0 and self._universe_refreshed_at > 0:
            return self.cfg.universe
        if not due:
            return self.cfg.universe
        vols = self._fetch_volume_map()
        univ = resolve_universe(self.cfg, volume_by_base=vols or None)
        self.cfg = replace(self.cfg, universe=univ)
        self._universe_refreshed_at = now
        logger.info(
            "ignition universe mode=%s n=%s sample=%s",
            mode,
            len(univ),
            ",".join(univ[:8]),
        )
        return univ

    async def _load_ohlc(self) -> dict[str, list[list[float]]]:
        self._refresh_universe()
        bases = ("BTC", *self.cfg.universe)
        loop = asyncio.get_running_loop()

        def _one(base: str) -> tuple[str, list[list[float]]]:
            try:
                return base, fetch_daily_ohlc(base, days=int(self.cfg.ohlc_days))
            except Exception as exc:  # noqa: BLE001
                logger.warning("ignition ohlc %s failed: %s", base, exc)
                return base, []

        pairs = await asyncio.gather(
            *[loop.run_in_executor(_OHLC_POOL, _one, b) for b in bases]
        )
        return {b: rows for b, rows in pairs}

    async def _open_lot(
        self,
        base: str,
        notional: float,
        px: float,
        reasons: list[str],
        *,
        points: int,
        entry_path: str = "classic",
        trail_pct: float | None = None,
    ) -> IgnitionPosition | None:
        if notional < self.cfg.min_notional_eur or px <= 0:
            return None
        spend = min(float(notional), await self._decision_cash())
        if spend < self.cfg.min_notional_eur:
            return None
        fill = await self._fill(base, "buy", notional_eur=spend)
        if fill is None or fill.qty <= 0 or fill.avg_price <= 0:
            return None
        cost = fill.qty * fill.avg_price + float(fill.fee_eur or 0.0)
        if cost > self.cash_eur + 1.0 and self.dry_run:
            return None
        self.cash_eur = max(0.0, self.cash_eur - cost)
        venue = self._primary_venue()
        path = str(entry_path or "classic")
        if trail_pct is None:
            path_trail = (
                float(self.cfg.coil_trail_pct)
                if path == "coil"
                else float(self.cfg.trail_pct)
            )
        else:
            path_trail = float(trail_pct)
        pos = IgnitionPosition(
            base=base,
            entry_price=float(fill.avg_price),
            notional_eur=float(fill.qty) * float(fill.avg_price),
            qty=float(fill.qty),
            opened_ms=int(time.time() * 1000),
            venue=venue,
            entry_reason=",".join(reasons),
            peak_px=float(fill.avg_price),
            points=points,
            trail_pct=path_trail,
            entry_path=path,
        )
        self.positions.append(pos)
        self.marks[base] = float(fill.avg_price)
        self.mark_ts[base] = time.time()
        self._ledger_append(
            {
                "event": "entry",
                "desk": self._desk(),
                "side": "long",
                "base": base,
                "venue": venue,
                "dry_run": self.dry_run,
                "paper_only": self.paper_only,
                "allow_live": self.allow_live,
                "target_venue": venue,
                "notional_eur": round(pos.notional_eur, 2),
                "quantity": pos.qty,
                "entry_price": pos.entry_price,
                "fee_eur": round(float(fill.fee_eur or 0.0), 4),
                "reason": pos.entry_reason,
                "holding_id": pos.holding_id,
                "points": points,
                "entry_path": path,
                "trail_pct": path_trail,
            }
        )
        if self.allow_live:
            await self._sync_cash_from_venue()
        return pos

    async def _close_lot(self, pos: IgnitionPosition, px: float, reason: str) -> float | None:
        qty = float(pos.qty or 0.0)
        if qty <= 0 and pos.entry_price > 0:
            qty = pos.notional_eur / pos.entry_price
        if qty <= 0:
            return None
        if self.dry_run:
            mark = px if px > 0 else float(self.marks.get(pos.base) or pos.entry_price or 0.0)
            if mark <= 0:
                return None
            fill = _Fill(
                qty=qty,
                avg_price=mark,
                fee_eur=qty * mark * (self.cfg.fee_rt / 2),
            )
        else:
            fill = await self._fill(pos.base, "sell", qty=qty)
            if fill is None:
                return None
        proceeds = fill.qty * fill.avg_price - float(fill.fee_eur or 0.0)
        gross = pos.gross_return(fill.avg_price)
        net = proceeds - pos.notional_eur
        self.cash_eur += proceeds
        self.realized_total_eur += net
        self.day_realized_eur += net
        self.positions = [p for p in self.positions if p.holding_id != pos.holding_id]
        self._ledger_append(
            {
                "event": "exit",
                "desk": self._desk(),
                "side": "long",
                "base": pos.base,
                "venue": pos.venue or self._primary_venue(),
                "dry_run": self.dry_run,
                "paper_only": self.paper_only,
                "allow_live": self.allow_live,
                "target_venue": self._primary_venue(),
                "notional_eur": round(pos.notional_eur, 2),
                "quantity": fill.qty,
                "entry_price": pos.entry_price,
                "exit_price": fill.avg_price,
                "fee_eur": round(float(fill.fee_eur or 0.0), 4),
                "net_eur": round(net, 2),
                "gross_return": round(gross, 4),
                "reason": reason,
                "holding_id": pos.holding_id,
                "points": pos.points,
            }
        )
        if self.allow_live:
            await self._sync_cash_from_venue()
        return net

    async def manage_trail(self) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        for pos in list(self.positions):
            mark = float(self.marks.get(pos.base) or 0.0)
            if mark <= 0:
                continue
            if mark > float(pos.peak_px or 0.0):
                pos.peak_px = mark
            hit = trail_exit(pos, mark, self.cfg)
            if hit is None:
                continue
            net = await self._close_lot(pos, mark, str(hit["reason"]))
            if net is not None:
                applied.append(
                    {
                        "action": "exit",
                        "base": pos.base,
                        "net_eur": round(net, 2),
                        "reason": hit["reason"],
                    }
                )
        if applied:
            if not self.positions:
                ld = dict(self.last_decision or {})
                if ld.get("risk_block") == "slots_full":
                    ld["risk_block"] = ""
                cap = str(ld.get("caption") or "")
                cap = cap.replace(" Block: slots_full.", "").replace("Block: slots_full.", "")
                reasons = ",".join(a.get("reason", "") for a in applied)
                if "trail" in reasons or "time" in reasons:
                    mode = "LIVE" if self.allow_live else "PAPER"
                    em = str(self.cfg.entry_mode or "top_day")
                    if em == "top_day":
                        ld["caption"] = (
                            f"OKX top-day {mode}: trail {self.cfg.trail_pct:.0%}"
                            f" / time {self.cfg.time_max_days:g}d. Slot vrij na exit."
                        )
                    else:
                        coil = (
                            f"; coil-trail {self.cfg.coil_trail_pct:.0%}"
                            if self.cfg.coil_entry_enabled
                            else ""
                        )
                        ld["caption"] = (
                            f"Ignition {mode}: sniper + trail "
                            f"{self.cfg.trail_pct:.0%}{coil}. Slot vrij na exit."
                        )
                elif cap:
                    ld["caption"] = cap
                self.last_decision = ld
            self._save_state()
        return applied

    async def buy(
        self,
        base: str,
        *,
        notional_eur: float | None = None,
        reason: str = "operator_manual",
    ) -> dict[str, Any]:
        """Manual open (paper or live OKX). Generic base, never coin-hardcoded."""
        base_u = str(base or "").strip().upper()
        if not base_u:
            return {"ok": False, "reason": "missing_base"}
        if any(p.base == base_u for p in self.positions):
            return {"ok": False, "reason": "already_held", "base": base_u}
        if len(self.positions) >= int(self.cfg.max_positions):
            return {"ok": False, "reason": "slots_full", "base": base_u}
        try:
            px_raw = await self._feed.last_price(base_u)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"mark_failed:{exc}", "base": base_u}
        if not px_raw or float(px_raw) <= 0:
            return {"ok": False, "reason": "no_mark", "base": base_u}
        mark = float(px_raw)
        self.marks[base_u] = mark
        self.mark_ts[base_u] = time.time()
        px = fill_px(mark, "buy", slip=self.cfg.slip)
        cash = await self._decision_cash()
        if notional_eur is not None:
            notion = float(notional_eur)
        else:
            powder = float(cash)
            if not self.cfg.compound_sizing:
                powder = min(powder, float(self.cfg.book_eur))
            if float(self.cfg.max_book_eur or 0.0) > 0:
                powder = min(powder, float(self.cfg.max_book_eur))
            notion = powder * float(self.cfg.deploy_frac)
        reasons = [str(reason or "operator_manual"), "manual"]
        pos = await self._open_lot(base_u, notion, px, reasons, points=0)
        if pos is None:
            return {"ok": False, "reason": "open_failed", "base": base_u}
        mode = "LIVE" if self.allow_live else "PAPER"
        self.last_decision = {
            **(self.last_decision or {}),
            "want": base_u,
            "caption": (
                f"Ignition {mode}: handmatige entry {base_u} "
                f"({pos.notional_eur:.0f} EUR @ {pos.entry_price:.6g})."
            ),
            "manual_entry": {
                "base": base_u,
                "notional_eur": pos.notional_eur,
                "entry_price": pos.entry_price,
                "reason": ",".join(reasons),
            },
            "at": datetime.now(UTC).isoformat(),
        }
        self._save_state()
        return {
            "ok": True,
            "base": base_u,
            "holding_id": pos.holding_id,
            "notional_eur": round(pos.notional_eur, 2),
            "entry_price": pos.entry_price,
            "paper_only": self.paper_only,
            "allow_live": self.allow_live,
            "status": self.status(),
        }

    async def sell(self, holding_id: str) -> dict[str, Any]:
        """Manual close (paper or live OKX)."""
        hid = str(holding_id or "").strip()
        pos = next((p for p in self.positions if p.holding_id == hid), None)
        if pos is None:
            return {"ok": False, "reason": "unknown_holding_id"}
        await self._refresh_marks()
        mark = float(self.marks.get(pos.base) or pos.entry_price or 0.0)
        px = fill_px(mark, "sell", slip=self.cfg.slip) if mark > 0 else 0.0
        net = await self._close_lot(pos, px, "manual_sell")
        if net is None:
            return {"ok": False, "reason": "close_failed", "base": pos.base}
        self._save_state()
        return {
            "ok": True,
            "base": pos.base,
            "holding_id": hid,
            "net_eur": round(net, 2),
            "allow_live": self.allow_live,
            "status": self.status(),
        }

    async def sell_all(self) -> dict[str, Any]:
        await self._refresh_marks()
        closed: list[dict[str, Any]] = []
        failed: list[str] = []
        for pos in list(self.positions):
            mark = float(self.marks.get(pos.base) or pos.entry_price or 0.0)
            px = fill_px(mark, "sell", slip=self.cfg.slip) if mark > 0 else 0.0
            net = await self._close_lot(pos, px, "manual_sell_all")
            if net is None:
                failed.append(pos.holding_id)
            else:
                closed.append({"base": pos.base, "holding_id": pos.holding_id, "net_eur": round(net, 2)})
        self._save_state()
        return {
            "ok": True,
            "closed": len(closed),
            "lots": closed,
            "failed": failed,
            "status": self.status(),
        }

    def _load_alphai_gate(self) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, Any]]:
        """Union daily + volatile AlphaI picks (ex-desk midcaps live in volatile)."""
        daily, daily_meta = load_alphai_view(self.alphai_path)
        volatile, vol_meta = load_alphai_view(self.alphai_volatile_path)
        picks = tuple(sorted(set(daily.picks) | set(volatile.picks)))
        avoid = tuple(sorted(set(daily.avoid) | set(volatile.avoid)))
        meta = {
            "daily_ok": daily_meta.get("ok"),
            "volatile_ok": vol_meta.get("ok"),
            "picks": list(picks),
            "avoid": list(avoid),
            "macro_caution": bool(daily.macro_caution or volatile.macro_caution),
        }
        return picks, avoid, meta

    def _entry_candidates(
        self, decision: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Rows to attempt live. top_day walks eligible so venue misses can fall through."""
        mode = str(
            decision.get("entry_mode") or self.cfg.entry_mode or "top_day"
        ).strip().lower()
        entries = list(decision.get("entries") or [])
        if mode != "top_day":
            return entries
        # Walk AlphaI-eligible only (never raw ranked — that bypasses the pick gate).
        pool = list(decision.get("eligible") or [])
        if not pool:
            return entries
        out: list[dict[str, Any]] = []
        for top in pool:
            base = str(top.get("base") or "").upper()
            if not base:
                continue
            reasons = [
                "top_day",
                f"day={float(top.get('day_ret') or 0.0):+.1%}",
                f"volx={float(top.get('vol_x') or 0.0):.1f}",
                f"trail={self.cfg.trail_pct:.0%}",
                f"time≤{self.cfg.time_max_days:g}d",
            ]
            if top.get("alphai_pick"):
                reasons.append("alphai_pick")
            out.append(
                {
                    "base": base,
                    "notional_eur": 0.0,  # sized at open time from remaining powder
                    "entry_path": "top_day",
                    "trail_pct": float(
                        top.get("trail_pct")
                        if top.get("trail_pct") is not None
                        else self.cfg.trail_pct
                    ),
                    "reasons": reasons,
                    "score": top,
                }
            )
        return out

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        async with self._decide_lock:
            now = datetime.now(UTC)
            self._roll_day(now)
            cash = await self._decision_cash()
            ohlc = await self._load_ohlc()
            held = [p.base for p in self.positions]
            picks, avoid, alphai_meta = self._load_alphai_gate()
            decision = evaluate_ignition(
                ohlc,
                self.cfg,
                held=held,
                cash_eur=cash,
                now=now,
                alphai_picks=picks,
                alphai_avoid=avoid,
            )
            decision = {**decision, "alphai": alphai_meta}
            applied: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []
            if execute and decision.get("ok") and not decision.get("risk_block"):
                held_set = {str(b).upper() for b in held}
                free = max(0, int(self.cfg.max_positions) - len(held_set))
                candidates = self._entry_candidates(decision)
                for row in candidates:
                    if free <= 0:
                        break
                    base = str(row["base"]).upper()
                    if base in held_set:
                        continue
                    # Enter at next-open proxy: last completed close * (1+slip).
                    rows = ohlc.get(base) or []
                    if not rows:
                        skipped.append({"base": base, "reason": "no_ohlc"})
                        continue
                    close = float(rows[-1][4])
                    # Prefer live mark when fresher.
                    mark = float(self.marks.get(base) or 0.0)
                    px_src = mark if mark > 0 else close
                    px = fill_px(px_src, "buy", slip=self.cfg.slip)
                    score = row.get("score") or {}
                    path = str(row.get("entry_path") or "classic")
                    path_trail = float(
                        row.get("trail_pct")
                        if row.get("trail_pct") is not None
                        else (
                            self.cfg.coil_trail_pct
                            if path == "coil"
                            else self.cfg.trail_pct
                        )
                    )
                    powder = float(await self._decision_cash())
                    if not self.cfg.compound_sizing:
                        powder = min(powder, float(self.cfg.book_eur))
                    if float(self.cfg.max_book_eur or 0.0) > 0:
                        powder = min(powder, float(self.cfg.max_book_eur))
                    notional = float(row.get("notional_eur") or 0.0)
                    if notional <= 0:
                        notional = (powder * float(self.cfg.deploy_frac)) / free
                    pos = await self._open_lot(
                        base,
                        notional,
                        px,
                        list(row.get("reasons") or []),
                        points=int(score.get("points") or 0),
                        entry_path=path,
                        trail_pct=path_trail,
                    )
                    if pos:
                        applied.append(
                            {
                                "action": "entry",
                                "base": pos.base,
                                "notional_eur": pos.notional_eur,
                                "points": pos.points,
                                "entry_path": pos.entry_path,
                                "trail_pct": pos.trail_pct,
                            }
                        )
                        held_set.add(base)
                        free -= 1
                    else:
                        skipped.append({"base": base, "reason": "fill_failed"})
                        logger.info(
                            "ignition skip %s (venue fill failed); trying next ranked",
                            base,
                        )
            self.last_decision = {
                **decision,
                "applied": applied,
                "skipped": skipped,
                "execute": bool(execute),
                "at": now.isoformat(),
            }
            await self._refresh_marks()
            self._save_state()
            return {"ok": True, "decision": self.last_decision, "status": self.status()}

    def next_decision(self) -> str:
        from datetime import timedelta

        now = datetime.now(UTC)
        interval = float(self.cfg.decision_interval_sec or 0.0)
        if interval > 0.0:
            slot = max(1, int(interval))
            # Next aligned UTC slot boundary.
            epoch = int(now.timestamp())
            nxt_ts = ((epoch // slot) + 1) * slot
            return datetime.fromtimestamp(nxt_ts, tz=UTC).isoformat()
        hours = sorted(int(h) for h in self.cfg.decision_hours_utc)
        for h in hours:
            if now.hour < h or (now.hour == h and now.minute < 5):
                return now.replace(hour=h, minute=5, second=0, microsecond=0).isoformat()
        nxt = now.replace(hour=hours[0], minute=5, second=0, microsecond=0)
        return (nxt + timedelta(days=1)).isoformat()

    def _decision_slot_due(self, now: datetime, last_slot: int | None) -> int | None:
        """Return a new decision slot id when a scan is due, else None."""
        interval = float(self.cfg.decision_interval_sec or 0.0)
        if interval > 0.0:
            slot = max(1, int(interval))
            cur = int(now.timestamp()) // slot * slot
            if last_slot is not None and cur <= int(last_slot):
                return None
            return cur
        hours = set(int(h) for h in self.cfg.decision_hours_utc)
        if now.hour not in hours or now.minute >= 8:
            return None
        # One fire per calendar hour in the sparse schedule.
        cur = int(now.timestamp()) // 3600 * 3600
        if last_slot is not None and cur <= int(last_slot):
            return None
        return cur

    def status(self) -> dict[str, Any]:
        now = time.time()
        positions = []
        for p in self.positions:
            mark = float(self.marks.get(p.base) or p.entry_price or 0.0)
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
                    "effective_trail_pct": effective_trail_pct(p, self.cfg),
                }
            )
        last = dict(self.last_decision or {})
        # Stale decide while a lot was open can leave slots_full after exit.
        if not positions and last.get("risk_block") == "slots_full":
            last["risk_block"] = ""
            cap = str(last.get("caption") or "")
            last["caption"] = (
                cap.replace(" Block: slots_full.", "")
                .replace("Block: slots_full.", "")
                .strip()
            )
        venue = self._primary_venue()
        truth = dict(self._venue_truth or {})
        caption = str(last.get("caption") or "Ignition PAPER")
        # Drop prior venue-truth suffixes so we never stack them.
        for sep in (" Target ", " — "):
            if sep in caption:
                caption = caption.split(sep, 1)[0]
        caption = caption.rstrip(".") + self._venue_caption_suffix()
        if truth.get("inventory_advice_nl"):
            caption = caption.rstrip(".") + " — " + str(truth["inventory_advice_nl"])
        return {
            "desk": self._desk(),
            "mode": "ignition_live" if self.allow_live else "ignition_paper",
            "paper_only": self.paper_only,
            "allow_live": self.allow_live,
            "allow_live_requested": bool(self._allow_live_requested),
            "dry_run": self.dry_run,
            "venues": list(self.venues),
            "venue": venue,
            "target_venue": venue,
            "book_eur": float(self.cfg.book_eur),
            "cash_eur": round(self.cash_eur, 2),
            "deployed_eur": round(self._deployed(), 2),
            "equity_eur": round(self._equity_now(), 2),
            "unrealized_net_eur": round(self._unrealized(), 2),
            "realized_total_eur": round(self.realized_total_eur, 2),
            "day_realized_eur": round(self.day_realized_eur, 2),
            "venue_cash_eur": truth.get("free_quote_eur"),
            "venue_inventory_eur": truth.get("inventory_mtm_eur"),
            "venue_total_eur": truth.get("total_value_eur"),
            "deployable_live_eur": truth.get("deployable_live_eur"),
            "venue_online": bool(truth.get("online")),
            "venue_top_inventory": list(truth.get("top_inventory") or []),
            "inventory_advice": truth.get("inventory_advice"),
            "inventory_advice_nl": truth.get("inventory_advice_nl") or "",
            "equity_curve": [
                [round(float(t), 1), round(float(eq), 2)] for t, eq in self.equity_curve
            ],
            "positions": positions,
            "last_decision": last,
            "live_caption": caption,
            "risk_on": bool(last.get("risk_on")),
            "want": last.get("want"),
            "next_decision": self.next_decision(),
            "config": {
                "trail_pct": self.cfg.trail_pct,
                "trail_ratchet_arm_pct": self.cfg.trail_ratchet_arm_pct,
                "trail_ratchet_pct": self.cfg.trail_ratchet_pct,
                "quiet_max": self.cfg.quiet_max,
                "day_ret_min": self.cfg.day_ret_min,
                "vol_mult_min": self.cfg.vol_mult_min,
                "min_points": self.cfg.min_points,
                "max_positions": self.cfg.max_positions,
                "book_eur": self.cfg.book_eur,
                "coil_entry_enabled": self.cfg.coil_entry_enabled,
                "coil_trail_pct": self.cfg.coil_trail_pct,
                "coil_day_ret_min": self.cfg.coil_day_ret_min,
                "coil_vol_mult_min": self.cfg.coil_vol_mult_min,
                "coil_quiet_max": self.cfg.coil_quiet_max,
                "compound_sizing": self.cfg.compound_sizing,
                "max_book_eur": self.cfg.max_book_eur,
                "entry_mode": self.cfg.entry_mode,
                "time_max_days": self.cfg.time_max_days,
                "requires_alphai_pick": self.cfg.requires_alphai_pick,
                "block_alphai_avoid": self.cfg.block_alphai_avoid,
                "signal": (
                    "top_day(liquid day_ret+alphai)"
                    if str(self.cfg.entry_mode) == "top_day"
                    else "classic(quiet+brk20+r1_6+vol2)|coil(compress+brk5)"
                ),
                "universe_mode": self.cfg.universe_mode,
                "universe_n": len(self.cfg.universe),
                "exclude_n": len(self.cfg.exclude_bases),
                "liquid_top_n": self.cfg.liquid_top_n,
                "venues": list(self.venues),
                "target_venue": venue,
                "allow_live": self.allow_live,
                "decision_interval_sec": float(self.cfg.decision_interval_sec or 0.0),
                "decision_hours_utc": list(self.cfg.decision_hours_utc),
            },
        }

    async def run(self, should_stop) -> None:  # noqa: ANN001
        last_slot: int | None = None
        await self._refresh_marks()
        now = datetime.now(UTC)
        kick_slot = self._decision_slot_due(now, last_slot=None)
        try:
            # Always evaluate on start; only execute buys when a slot is due.
            await self.decide(execute=kick_slot is not None)
        except Exception:  # noqa: BLE001
            logger.exception("ignition kick decide failed")
        if kick_slot is not None:
            last_slot = kick_slot
        while not should_stop():
            try:
                await self._refresh_marks()
                async with self._decide_lock:
                    await self.manage_trail()
                now = datetime.now(UTC)
                slot = self._decision_slot_due(now, last_slot=last_slot)
                if slot is not None:
                    await self.decide(execute=True)
                    last_slot = slot
            except Exception:  # noqa: BLE001
                logger.exception("ignition tick failed")
            await asyncio.sleep(float(self.cfg.tick_sec))
        self._save_state()


class IgnitionDeskManager:
    """Singleton for the ignition sleeve (paper or live OKX)."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._runner: IgnitionPaperRunner | None = None
        self._stop = False
        self._engine: Any = None

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        enabled = bool(getattr(settings, "momentum_ignition_enabled", False))
        venues = parse_venues(
            getattr(settings, "momentum_ignition_venues", "okx") or "okx"
        ) or ("okx",)
        allow_req = bool(getattr(settings, "momentum_ignition_allow_live", False))
        live_now = bool(self._runner and self._runner.allow_live)
        base: dict[str, Any] = {
            "running": self.running(),
            "enabled_setting": enabled,
            "desk": "ignition" if live_now else "ignition_paper",
            "mode": "ignition_live" if live_now else "ignition_paper",
            "dry_run": not live_now,
            "paper_only": not live_now,
            "allow_live": live_now,
            "allow_live_requested": allow_req,
            "venues": list(venues),
            "venue": venues[0],
            "target_venue": venues[0],
        }
        if self._runner is not None:
            base.update(self._runner.status())
            return base
        if enabled:
            cfg = config_from_settings(settings)
            state_path = str(
                getattr(
                    settings,
                    "momentum_ignition_state_path",
                    "./data/momentum_ignition_state.json",
                )
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
            mode = "LIVE" if allow_req else "paper"
            caption = str(last.get("caption") or "")
            if caption and "OKX" not in caption.upper():
                caption = caption.rstrip(".") + f". Target {venues[0].upper()} ({mode})."
            base.update(
                {
                    "book_eur": cfg.book_eur,
                    "cash_eur": round(cash, 2),
                    "deployed_eur": round(
                        sum(float(p.get("notional_eur") or 0) for p in positions), 2
                    ),
                    "equity_eur": round(cash, 2),
                    "realized_total_eur": round(realized, 2),
                    "positions": positions,
                    "last_decision": last,
                    "live_caption": caption,
                    "config": {
                        "trail_pct": cfg.trail_pct,
                        "coil_entry_enabled": cfg.coil_entry_enabled,
                        "coil_trail_pct": cfg.coil_trail_pct,
                        "signal": "classic(quiet+brk20+r1_6+vol2)|coil(compress+brk5)",
                        "book_eur": cfg.book_eur,
                        "venues": list(venues),
                        "target_venue": venues[0],
                        "allow_live": allow_req,
                    },
                }
            )
        return base

    async def start(self, settings: Settings | None = None) -> dict[str, Any]:
        settings = settings or get_settings()
        if self.running():
            # Hot-upgrade paper → live without requiring a full process restart.
            allow_live = bool(getattr(settings, "momentum_ignition_allow_live", False))
            if allow_live and self._runner and self._runner.dry_run:
                await self.stop()
            else:
                return {
                    "ok": True,
                    "started": False,
                    "reason": "already_running",
                    "status": self.status(),
                }
        if not bool(getattr(settings, "momentum_ignition_enabled", False)):
            return {
                "ok": False,
                "started": False,
                "reason": "momentum_ignition_enabled_false",
                "hint": "Set MOMENTUM_IGNITION_ENABLED=true",
            }
        allow_live = bool(getattr(settings, "momentum_ignition_allow_live", False))
        venues = parse_venues(
            getattr(settings, "momentum_ignition_venues", "okx") or "okx"
        ) or ("okx",)
        cfg = config_from_settings(settings)
        state_path = str(
            getattr(settings, "momentum_ignition_state_path", "./data/momentum_ignition_state.json")
        )
        ledger_path = str(
            getattr(
                settings, "momentum_ignition_ledger_path", "./data/momentum_ignition_ledger.jsonl"
            )
        )
        gateways: dict[str, Any] = {}
        dry_run = not allow_live
        if allow_live:
            from bot.live.micro_engine import LiveMicroEngine
            from bot.live.momentum_desk import DeskConfig

            book = float(cfg.book_eur)
            desk_cfg = DeskConfig(
                clip_eur=max(book, 500.0),
                max_positions=int(cfg.max_positions),
                day_loss_limit_eur=max(book * 0.2, 400.0),
            )
            engine = LiveMicroEngine(engine_settings_for_desk(settings, desk_cfg, venues))
            armed = engine.arm()
            if not armed.get("armed"):
                logger.error("ignition live arm failed: %s", armed)
                return {"ok": False, "started": False, "reason": "arm_failed", "detail": armed}
            for v in venues:
                if engine._registry.get_client(v, enable_trading=True) is None:  # noqa: SLF001
                    logger.warning("ignition: no trading credentials for %s; skipped", v)
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
        self._stop = False
        self._runner = IgnitionPaperRunner(
            cfg,
            state_path=state_path,
            ledger_path=ledger_path,
            venues=venues,
            allow_live=not dry_run,
            gateways=gateways,
            alphai_path=str(
                getattr(settings, "alphai_daily_recommendations_path", None)
                or "./data/alphai/daily_recommendations.json"
            ),
            alphai_volatile_path=str(
                getattr(settings, "alphai_volatile_recommendations_path", None)
                or "./data/alphai/volatile_recommendations.json"
            ),
        )
        if not dry_run:
            dropped = self._runner.discard_paper_positions()
            logger.info("ignition live: dropped %s paper lots before venue orders", dropped)
            synced = await self._runner._sync_cash_from_venue()  # noqa: SLF001
            logger.info("ignition live: venue cash sync -> %s", synced)
            ld = dict(self._runner.last_decision or {})
            cap = str(ld.get("caption") or "")
            if "PAPER" in cap.upper() or not cap:
                coil = (
                    f"; coil-trail {cfg.coil_trail_pct:.0%}"
                    if cfg.coil_entry_enabled
                    else ""
                )
                ld["caption"] = (
                    f"Ignition LIVE: desk classic+coil + trail "
                    f"{cfg.trail_pct:.0%}{coil}. OKX fills armed."
                )
                self._runner.last_decision = ld
                self._runner._save_state()  # noqa: SLF001
        self._task = asyncio.create_task(
            self._runner.run(lambda: self._stop), name="momentum-ignition"
        )
        _write_flag(
            state_path,
            running=True,
            dry_run=dry_run,
            paper_only=dry_run,
            allow_live=not dry_run,
            venues=list(venues),
            target_venue=venues[0],
        )
        return {
            "ok": True,
            "started": True,
            "paper_only": dry_run,
            "allow_live": not dry_run,
            "venues": list(venues),
            "target_venue": venues[0],
            "status": self.status(),
        }

    async def stop(self) -> dict[str, Any]:
        self._stop = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=8.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        path = str(
            getattr(
                get_settings(),
                "momentum_ignition_state_path",
                "./data/momentum_ignition_state.json",
            )
        )
        dry = True if self._runner is None else self._runner.dry_run
        _write_flag(path, running=False, dry_run=dry, paper_only=dry, allow_live=not dry)
        self._task = None
        st = self.status()
        self._runner = None
        self._engine = None
        return {"ok": True, "stopped": True, "status": st}

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        if self._runner is None:
            return {"ok": False, "reason": "not_running"}
        return await self._runner.decide(execute=execute)

    async def buy(
        self,
        base: str,
        *,
        notional_eur: float | None = None,
        reason: str = "operator_paper",
    ) -> dict[str, Any]:
        if self._runner is None:
            return {"ok": False, "reason": "not_running"}
        return await self._runner.buy(
            base, notional_eur=notional_eur, reason=reason
        )

    async def sell(self, holding_id: str) -> dict[str, Any]:
        if self._runner is None:
            return {"ok": False, "reason": "not_running"}
        return await self._runner.sell(holding_id)

    async def sell_all(self) -> dict[str, Any]:
        if self._runner is None:
            return {"ok": False, "reason": "not_running"}
        return await self._runner.sell_all()

    async def resume_if_flagged(self) -> dict[str, Any] | None:
        settings = get_settings()
        if not bool(getattr(settings, "momentum_ignition_enabled", False)):
            return None
        path = str(
            getattr(settings, "momentum_ignition_state_path", "./data/momentum_ignition_state.json")
        )
        flag = _read_flag(path)
        if flag and flag.get("running") is False:
            return {"started": False, "reason": "flagged_stopped"}
        if self.running():
            return {"started": False, "reason": "already_running"}
        return await self.start(settings=settings)


_MANAGER: IgnitionDeskManager | None = None


def get_ignition_desk_manager() -> IgnitionDeskManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = IgnitionDeskManager()
    return _MANAGER


def reset_ignition_desk_manager() -> None:
    global _MANAGER
    _MANAGER = None

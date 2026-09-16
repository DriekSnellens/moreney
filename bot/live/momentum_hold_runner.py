"""Spot buy&hold sleeve — soft book reserved away from the momentum desk.

12w research: full-book BTC buy&hold beat the WR+survival desk on both PnL and
drawdown (quality beater). This sleeve implements that allocation as a separate
soft book with configurable hold bases (default BTC) — no per-coin hardcodes in
momentum rank/exit logic.

Behaviour:
  - On start / periodic top-up: buy configured bases until deployed ≈ book_eur.
  - No trail / time / RS exits (manual sell only; optional disaster stop).
  - Soft book clamp so the sleeve cannot spend the whole venue balance.
  - Core desk reserves undeployed hold book via ``hold_reserved_eur``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.live.momentum_hold_baseline import baseline_path, mtm_snapshot
from bot.live.momentum_desk import BAR_MS, DeskConfig, ExitDecision
from bot.live.momentum_runner import (
    Gateway,
    LiveGateway,
    MomentumDeskRunner,
    RunnerOptions,
    _DISASTER_STOP_MULT,
    _MIN_ORDER_EUR,
    engine_settings_for_desk,
    parse_venues,
)

logger = logging.getLogger("bot.live.momentum_hold_runner")


def _hold_flag_path(state_path: str) -> Path:
    return Path(state_path).with_name("momentum_hold_running.json")


def _write_hold_flag(state_path: str, **payload: Any) -> None:
    path = _hold_flag_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"updated_at": datetime.now(UTC).isoformat(), **payload}),
        encoding="utf-8",
    )


def _read_hold_flag(state_path: str) -> dict[str, Any] | None:
    path = _hold_flag_path(state_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def parse_hold_bases(raw: str | Sequence[str] | None) -> tuple[str, ...]:
    if raw is None:
        return ("BTC",)
    if isinstance(raw, str):
        parts = [p.strip().upper() for p in raw.replace(";", ",").split(",")]
    else:
        parts = [str(p).strip().upper() for p in raw]
    out = tuple(dict.fromkeys(p for p in parts if p))
    return out or ("BTC",)


@dataclass(frozen=True)
class HoldSleeveConfig:
    """Soft-book buy&hold allocation (coin list is config, not strategy logic)."""

    book_eur: float = 20_000.0
    bases: tuple[str, ...] = ("BTC",)
    venues: tuple[str, ...] = ("bitvavo",)
    # Top-up when deployed falls below this fraction of book (fees / drift).
    fill_threshold: float = 0.97
    # Equal-weight across configured bases (1 base → 100%).
    equal_weight: bool = True
    # Optional crash guard (0 disables). Multiplier on DeskConfig.hard_stop_pct.
    disaster_stop: bool = True
    rebalance_interval_sec: float = 3600.0
    fee_rt: float = 0.003
    hard_stop_pct: float = 0.25  # only used if disaster_stop; 25% crash guard


def hold_config_from_settings(settings: Settings | None = None) -> HoldSleeveConfig:
    settings = settings or get_settings()
    book = float(getattr(settings, "momentum_hold_book_eur", 20_000.0) or 20_000.0)
    fraction = float(getattr(settings, "momentum_hold_fraction", 0.0) or 0.0)
    desk_book = float(getattr(settings, "momentum_desk_book_eur", 0.0) or 0.0)
    if fraction > 0.0:
        total = book + desk_book if desk_book > 0 else book
        # If only fraction set with a total book hint via hold_book as total:
        total_hint = float(
            getattr(settings, "momentum_total_book_eur", 0.0) or 0.0
        )
        if total_hint > 0:
            book = total_hint * min(1.0, fraction)
        elif desk_book > 0:
            book = (desk_book / max(1e-9, 1.0 - fraction)) * fraction
    bases = parse_hold_bases(getattr(settings, "momentum_hold_bases", "BTC"))
    venues = parse_venues(
        getattr(settings, "momentum_hold_venues", None) or "bitvavo"
    )
    return HoldSleeveConfig(
        book_eur=max(0.0, book),
        bases=bases,
        venues=venues,
        fill_threshold=float(
            getattr(settings, "momentum_hold_fill_threshold", 0.97) or 0.97
        ),
        equal_weight=bool(getattr(settings, "momentum_hold_equal_weight", True)),
        disaster_stop=bool(getattr(settings, "momentum_hold_disaster_stop", True)),
        rebalance_interval_sec=float(
            getattr(settings, "momentum_hold_rebalance_sec", 3600.0) or 3600.0
        ),
        fee_rt=float(getattr(settings, "momentum_desk_fee_rt", 0.003) or 0.003),
        hard_stop_pct=float(
            getattr(settings, "momentum_hold_disaster_pct", 0.25) or 0.25
        ),
    )


def hold_desk_config(hold: HoldSleeveConfig) -> DeskConfig:
    """Minimal DeskConfig so the shared runner can buy/sell/mark."""
    return DeskConfig(
        decision_hours_utc=(),  # hold never uses schedule entries
        clip_eur=float(hold.book_eur),
        max_positions=max(1, len(hold.bases)),
        top_n=max(1, len(hold.bases)),
        book_eur=float(hold.book_eur),
        fee_rt=float(hold.fee_rt),
        hard_stop_pct=float(hold.hard_stop_pct),
        trail_pct=0.99,  # effectively never trail
        trail_tight_after=0.0,
        trail_tight_pct=0.0,
        time_exit_hours=0.0,
        midflat_hours=0.0,
        green_deadline_hours=0.0,
        fade_eta_sec=0.0,
        soft_regime_on_weak_tape=False,
        skip_weekend_entries=False,
        refill_on_exit=False,
        day_loss_limit_eur=1e12,
        week_loss_limit_eur=1e12,
        max_entries_per_base_per_day=8,  # multi-venue top-ups same day
        universe=hold.bases,
        outcome_size_enabled=False,
    )


def hold_reserved_eur(
    settings: Settings | None = None,
    *,
    deployed_eur: float | None = None,
) -> float:
    """EUR the core desk must not spend (undeployed hold soft book)."""
    settings = settings or get_settings()
    if not bool(getattr(settings, "momentum_hold_enabled", False)):
        return 0.0
    hold = hold_config_from_settings(settings)
    book = float(hold.book_eur or 0.0)
    if book <= 0:
        return 0.0
    if deployed_eur is None:
        # Prefer live runner status when available.
        try:
            mgr = get_hold_desk_manager()
            if mgr.running() and mgr._runner is not None:  # noqa: SLF001
                deployed_eur = mgr._runner._deployed_eur()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            deployed_eur = None
    deployed = max(0.0, float(deployed_eur or 0.0))
    return max(0.0, book - deployed)


class HoldDeskRunner(MomentumDeskRunner):
    """Buy configured bases to fill the soft book; hold without momentum exits."""

    def __init__(
        self,
        cfg: DeskConfig,
        gateway: Gateway | None,
        *,
        hold: HoldSleeveConfig,
        options: RunnerOptions | None = None,
        feed: Any = None,
        clock: Any = None,
        sleep: Any = None,
        gateways: Mapping[str, Gateway] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"options": options, "gateways": gateways}
        if feed is not None:
            kwargs["feed"] = feed
        if clock is not None:
            kwargs["clock"] = clock
        if sleep is not None:
            kwargs["sleep"] = sleep
        super().__init__(cfg, gateway, **kwargs)
        self.hold = hold
        self._last_rebalance_ts = 0.0
        self._btc_mtm: dict[str, Any] = {}
        self._baseline_path = baseline_path(get_settings())

    def status(self) -> dict[str, Any]:
        out = super().status()
        out["desk"] = "momentum_hold"
        out["book_eur"] = float(self.hold.book_eur)
        out["deployed_eur"] = round(self._deployed_eur(), 2)
        out["book_left_eur"] = round(
            max(0.0, float(self.hold.book_eur) - self._deployed_eur()), 2
        )
        out["hold_bases"] = list(self.hold.bases)
        out["mode"] = "hold_live"
        if self._btc_mtm:
            out["btc_hold"] = dict(self._btc_mtm)
        out["config"] = {
            **(out.get("config") or {}),
            "max_positions": len(self.hold.bases),
            "book_eur": float(self.hold.book_eur),
            "decision_hours_utc": [],
            "hold_bases": list(self.hold.bases),
        }
        return out

    def _deployed_eur(self) -> float:
        return sum(max(0.0, float(h.pos.notional_eur)) for h in self.holdings)

    async def refresh_btc_inventory(self) -> dict[str, Any]:
        """Sum free BTC across hold venues and MTM vs today's origin baseline."""
        by_venue: dict[str, float] = {}
        for venue in self.opt.venues:
            free = await self._available_base("BTC", str(venue))
            if free is None:
                continue
            if free > 1e-10:
                by_venue[str(venue)] = float(free)
        # Fall back to booked holdings if balance fetch missed a venue.
        for h in self.holdings:
            if str(h.pos.base).upper() != "BTC":
                continue
            v = str(h.pos.venue or "")
            booked = float(h.pos.quantity or 0.0)
            if booked <= 0:
                continue
            if v not in by_venue or by_venue[v] + 1e-12 < booked:
                by_venue[v] = max(by_venue.get(v, 0.0), booked)
        qty = sum(by_venue.values())
        mark = self.marks.get("BTC")
        if mark is None and qty > 0:
            for h in self.holdings:
                if str(h.pos.base).upper() == "BTC" and h.pos.entry_price:
                    mark = float(h.pos.entry_price)
                    break
        value = float(qty) * float(mark) if mark and qty > 0 else 0.0
        snap = mtm_snapshot(
            value_eur=value,
            qty_btc=qty,
            mark_eur=float(mark) if mark else None,
            by_venue=by_venue,
            path=self._baseline_path,
        )
        self._btc_mtm = snap
        return snap

    def _route_entry(self, clip_eur: float) -> tuple[str, float] | None:
        """Venue pick + own soft book only (do not reserve hold cash against self)."""
        venues = self.opt.venues
        book = float(self.hold.book_eur or 0.0)
        free_book = max(0.0, book - self._deployed_eur()) if book > 0 else clip_eur
        min_ok = max(_MIN_ORDER_EUR, float(self.opt.min_residual_clip_eur))
        if free_book < min_ok:
            return None
        want = min(clip_eur, free_book)
        if not self._gws or not self.cash_by_venue:
            return venues[0], round(want, 2)
        need = want * 1.005
        for venue in venues:
            cash = self.cash_by_venue.get(venue)
            if cash is not None and cash >= need and venue in self._gws:
                return venue, round(want, 2)
        best = max(
            ((v, self.cash_by_venue.get(v, 0.0)) for v in venues if v in self._gws),
            key=lambda item: item[1],
            default=None,
        )
        if best is None:
            return None
        venue, cash = best
        reduced = min(float(cash) / 1.005, want)
        if reduced < min_ok:
            return None
        return venue, round(reduced, 2)

    async def tick(self) -> None:
        """Mark + optional disaster stop + top-up toward the hold book."""
        await self._refresh_cash()
        await self.refresh_marks()
        try:
            await self.refresh_btc_inventory()
        except Exception as exc:  # noqa: BLE001
            logger.warning("hold sleeve: BTC inventory refresh failed: %s", exc)
        now_ms = int(self._clock() * 1000)
        if self.hold.disaster_stop:
            await self._disaster_only_exits(now_ms)
        await self._maybe_rebalance(now_ms)
        self._save_state()

    async def _disaster_only_exits(self, now_ms: int) -> None:
        if not self.holdings:
            return
        for h in list(self.holdings):
            if h.exiting:
                continue
            live_px = self.marks.get(h.pos.base)
            if live_px is None:
                rows = await self._feed.candles(h.pos.base, 4)
                if not rows:
                    continue
                live_px = float(rows[-1][4])
                self.marks[h.pos.base] = live_px
            if live_px <= h.pos.entry_price * (
                1 - _DISASTER_STOP_MULT * self.cfg.hard_stop_pct
            ):
                await self._exit(
                    h,
                    ExitDecision(
                        "disaster_stop", h.pos.gross_return(float(live_px)), True
                    ),
                )

    async def _maybe_rebalance(self, now_ms: int) -> None:
        now = float(self._clock())
        if now - self._last_rebalance_ts < float(self.hold.rebalance_interval_sec):
            # Always allow first fill right after start.
            if self._last_rebalance_ts > 0 and self._deployed_eur() > 0:
                return
        self._last_rebalance_ts = now
        await self.fill_to_book(now_ms)

    async def fill_to_book(self, now_ms: int | None = None) -> dict[str, Any]:
        """Buy underweight hold bases until soft book is filled."""
        now_ms = now_ms or int(self._clock() * 1000)
        book = float(self.hold.book_eur or 0.0)
        if book <= 0:
            return {"ok": False, "reason": "book_zero"}
        deployed = self._deployed_eur()
        if deployed >= book * float(self.hold.fill_threshold):
            return {
                "ok": True,
                "filled": True,
                "deployed_eur": round(deployed, 2),
                "book_eur": book,
            }
        await self._refresh_cash()
        bases = list(self.hold.bases)
        n = max(1, len(bases))
        target_each = book / n if self.hold.equal_weight else book
        planned: list[dict[str, Any]] = []
        # Multi-venue: keep topping up until book is filled or a pass makes no progress.
        for _pass in range(6):
            held = {h.pos.base.upper(): h for h in self.holdings}
            made = False
            for base in bases:
                cur = 0.0
                h = held.get(base)
                if h is not None:
                    cur = float(h.pos.notional_eur)
                need = max(0.0, target_each - cur)
                min_ok = max(_MIN_ORDER_EUR, float(self.opt.min_residual_clip_eur))
                if need < min_ok:
                    continue
                free_book = max(0.0, book - self._deployed_eur())
                clip = min(need, free_book)
                if clip < min_ok:
                    continue
                before = self._deployed_eur()
                planned.append({"base": base, "clip_eur": round(clip, 2), "pass": _pass})
                await self._enter(
                    base,
                    clip,
                    f"hold_fill,target={target_each:.0f},book={book:.0f}",
                    now_ms,
                    entry_ctx={"sleeve": "hold", "regime_label": "hold"},
                )
                await self._refresh_cash()
                if self._deployed_eur() > before + 1.0:
                    made = True
            if not made:
                break
            if self._deployed_eur() >= book * float(self.hold.fill_threshold):
                break
        summary = {
            "ok": True,
            "planned": planned,
            "deployed_eur": round(self._deployed_eur(), 2),
            "book_eur": book,
            "bases": bases,
        }
        self._ledger_append({"event": "hold_rebalance", **summary})
        self._save_state()
        return summary

    async def decide_now(self, *, execute: bool = True, **_kwargs: Any) -> dict[str, Any]:
        """Operator 'decide' = top-up hold book (preview or execute)."""
        if not execute:
            book = float(self.hold.book_eur)
            deployed = self._deployed_eur()
            need = max(0.0, book - deployed)
            return {
                "ok": True,
                "preview": True,
                "book_eur": book,
                "deployed_eur": round(deployed, 2),
                "need_eur": round(need, 2),
                "bases": list(self.hold.bases),
            }
        return await self.fill_to_book()


class HoldDeskManager:
    """Start/stop singleton for the spot buy&hold sleeve."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._runner: HoldDeskRunner | None = None
        self._stop = False
        self._engine: Any = None
        self._sell_task: asyncio.Task[None] | None = None
        self._manual_exit: dict[str, Any] = {}

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        allow_live = bool(getattr(settings, "momentum_hold_allow_live", False))
        dry = bool(getattr(self._runner.opt, "dry_run", True)) if self._runner else True
        base: dict[str, Any] = {
            "running": self.running(),
            "enabled_setting": bool(getattr(settings, "momentum_hold_enabled", False)),
            "allow_live": allow_live,
            "desk": "momentum_hold",
            "mode": "hold_paper" if dry or not allow_live else "hold_live",
        }
        if self._runner is not None:
            base.update(self._runner.status())
            dry = bool(getattr(self._runner.opt, "dry_run", True))
            base["dry_run"] = dry
            base["mode"] = "hold_paper" if dry else "hold_live"
        if self._manual_exit:
            base["manual_exit"] = dict(self._manual_exit)
        if self._task is not None and self._task.done() and self._task.exception():
            base["task_error"] = repr(self._task.exception())
        return base

    async def status_fresh(self) -> dict[str, Any]:
        """Status after refreshing BTC marks + cross-venue inventory."""
        if self._runner is not None:
            try:
                await self._runner.refresh_marks()
            except Exception as exc:  # noqa: BLE001
                logger.warning("hold sleeve: mark refresh failed: %s", exc)
            try:
                await self._runner.refresh_btc_inventory()
            except Exception as exc:  # noqa: BLE001
                logger.warning("hold sleeve: inventory refresh failed: %s", exc)
        return self.status()

    async def start(
        self,
        *,
        settings: Settings | None = None,
        dry_run: bool = True,
        venue: str | Sequence[str] | None = None,
    ) -> dict[str, Any]:
        if self.running():
            return {
                "ok": False,
                "started": False,
                "reason": "already_running",
                "status": self.status(),
            }
        settings = settings or get_settings()
        if not bool(getattr(settings, "momentum_hold_enabled", False)):
            return {
                "ok": False,
                "started": False,
                "reason": "momentum_hold_enabled_false",
                "hint": "Set MOMENTUM_HOLD_ENABLED=true to arm the buy&hold sleeve",
            }
        allow_live = bool(getattr(settings, "momentum_hold_allow_live", False))
        if not dry_run and not allow_live:
            return {
                "ok": False,
                "started": False,
                "reason": "live_orders_disabled",
                "hint": (
                    "MOMENTUM_HOLD_ALLOW_LIVE=false — only paper/dry_run is allowed. "
                    "Set ALLOW_LIVE=true to buy spot for real."
                ),
                "status": self.status(),
            }
        hold = hold_config_from_settings(settings)
        cfg = hold_desk_config(hold)
        raw_venues = venue
        if raw_venues is None:
            raw_venues = getattr(settings, "momentum_hold_venues", None) or "bitvavo"
        venues = parse_venues(raw_venues if isinstance(raw_venues, str) else list(raw_venues))
        gateways: dict[str, Gateway] = {}
        if not dry_run:
            from bot.live.micro_engine import LiveMicroEngine

            engine = LiveMicroEngine(engine_settings_for_desk(settings, cfg, venues))
            armed = engine.arm()
            if not armed.get("armed"):
                return {"started": False, "reason": "arm_failed", "detail": armed}
            for v in venues:
                if engine._registry.get_client(v, enable_trading=True) is None:  # noqa: SLF001
                    logger.warning("hold sleeve: no trading credentials for %s; skipped", v)
                    continue
                gateways[v] = LiveGateway(engine, v)
            if not gateways:
                return {
                    "started": False,
                    "reason": "no_venue_credentials",
                    "venues": list(venues),
                }
            venues = tuple(v for v in venues if v in gateways)
            self._engine = engine

        state_path = str(
            getattr(settings, "momentum_hold_state_path", "./data/momentum_hold_state.json")
        )
        ledger_path = str(
            getattr(
                settings,
                "momentum_hold_ledger_path",
                "./data/momentum_hold_ledger.jsonl",
            )
        )
        options = RunnerOptions(
            venues=venues,
            dry_run=bool(dry_run),
            state_path=state_path,
            ledger_path=ledger_path,
            alphai_recommendations_path="",
        )
        self._runner = HoldDeskRunner(
            cfg, None, hold=hold, options=options, gateways=gateways
        )
        self._stop = False
        self._task = asyncio.create_task(
            self._runner.run(lambda: self._stop), name="momentum-hold"
        )
        Path(options.state_path).parent.mkdir(parents=True, exist_ok=True)
        _write_hold_flag(
            options.state_path,
            running=True,
            dry_run=bool(dry_run),
            venue=venues[0],
            venues=list(venues),
        )
        # Immediate top-up attempt after start.
        try:
            await self._runner.fill_to_book()
        except Exception as exc:  # noqa: BLE001
            logger.warning("hold sleeve: initial fill failed: %s", exc)
        try:
            await self._runner.refresh_marks()
            await self._runner.refresh_btc_inventory()
        except Exception as exc:  # noqa: BLE001
            logger.warning("hold sleeve: initial BTC MTM failed: %s", exc)
        logger.info(
            "hold sleeve started dry_run=%s venues=%s book=%.0f bases=%s",
            dry_run,
            venues,
            hold.book_eur,
            hold.bases,
        )
        return {"ok": True, "started": True, "status": self.status()}

    async def decide(self, *, execute: bool) -> dict[str, Any]:
        if self._runner is None or not self.running():
            return {"ok": False, "reason": "not_running"}
        summary = await self._runner.decide_now(execute=execute)
        return {"ok": True, "decision": summary, "status": self.status()}

    def sell(self, holding_id: str, *, urgent: bool = False) -> dict[str, Any]:
        if self._runner is None or not self.running():
            return {"ok": False, "reason": "not_running"}
        if self._sell_task is not None and not self._sell_task.done():
            return {"ok": False, "reason": "sell_in_progress"}
        runner = self._runner
        self._manual_exit = {
            "started_at": datetime.now(UTC).isoformat(),
            "holding_id": holding_id,
            "urgent": bool(urgent),
            "done": False,
        }

        async def _run() -> None:
            try:
                self._manual_exit["result"] = await runner.sell_now(
                    holding_id, urgent=urgent
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("hold sleeve: sell failed")
                self._manual_exit["result"] = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._manual_exit["done"] = True
                self._manual_exit["finished_at"] = datetime.now(UTC).isoformat()

        self._sell_task = asyncio.create_task(_run(), name="hold-sell")
        return {"ok": True, "manual_exit": dict(self._manual_exit)}

    def sell_all(self, *, urgent: bool = False) -> dict[str, Any]:
        if self._runner is None or not self.running():
            return {"ok": False, "reason": "not_running"}
        if self._sell_task is not None and not self._sell_task.done():
            return {"ok": False, "reason": "sell_in_progress"}
        runner = self._runner
        self._manual_exit = {
            "started_at": datetime.now(UTC).isoformat(),
            "sell_all": True,
            "urgent": bool(urgent),
            "done": False,
        }

        async def _run() -> None:
            try:
                self._manual_exit["result"] = await runner.sell_all_now(urgent=urgent)
            except Exception as exc:  # noqa: BLE001
                logger.exception("hold sleeve: sell_all failed")
                self._manual_exit["result"] = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._manual_exit["done"] = True
                self._manual_exit["finished_at"] = datetime.now(UTC).isoformat()

        self._sell_task = asyncio.create_task(_run(), name="hold-sell-all")
        return {"ok": True, "manual_exit": dict(self._manual_exit)}

    async def stop(self) -> dict[str, Any]:
        self._stop = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=15.0)
            except Exception:  # noqa: BLE001
                self._task.cancel()
        if self._runner is not None:
            _write_hold_flag(self._runner.opt.state_path, running=False)
        self._task = None
        return {"ok": True, "stopped": True, "status": self.status()}

    async def resume_if_flagged(self) -> dict[str, Any]:
        settings = get_settings()
        if not bool(getattr(settings, "momentum_hold_enabled", False)):
            return {"resumed": False, "reason": "disabled"}
        state_path = str(
            getattr(settings, "momentum_hold_state_path", "./data/momentum_hold_state.json")
        )
        flag = _read_hold_flag(state_path)
        if not flag or not flag.get("running"):
            return {"resumed": False, "reason": "not_flagged"}
        dry = bool(flag.get("dry_run", True))
        allow_live = bool(getattr(settings, "momentum_hold_allow_live", False))
        if not dry and not allow_live:
            dry = True
        venues = flag.get("venues") or flag.get("venue") or settings.momentum_hold_venues
        return await self.start(settings=settings, dry_run=dry, venue=venues)


_HOLD_MANAGER: HoldDeskManager | None = None


def get_hold_desk_manager() -> HoldDeskManager:
    global _HOLD_MANAGER
    if _HOLD_MANAGER is None:
        _HOLD_MANAGER = HoldDeskManager()
    return _HOLD_MANAGER


def reset_hold_desk_manager() -> None:
    global _HOLD_MANAGER
    _HOLD_MANAGER = None


def hold_desk_flagged_running(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    state_path = str(
        getattr(settings, "momentum_hold_state_path", "./data/momentum_hold_state.json")
    )
    flag = _read_hold_flag(state_path)
    return bool(flag and flag.get("running"))

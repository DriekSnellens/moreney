"""Live volatile midcap sleeve — AlphaI-gated, separate from core Momentum Desk.

Reuses ``MomentumDeskRunner`` maker-taker execution with its own state/ledger,
soft book EUR, and concentrated sizing. Strategy logic lives in
``momentum_volatile_shadow`` (no per-coin hardcodes). Paper GET
``/live/momentum/volatile`` stays research-only.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.live.momentum_desk import (
    BAR_MS,
    AlphaIView,
    DeskConfig,
    Entry,
    ExitDecision,
    bar_stats,
    universe_stats,
)
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
from bot.live.momentum_volatile_shadow import (
    VolatileShadowConfig,
    _evaluate_volatile_exit,
    _rank_volatile,
    _select_volatile,
    load_shadow_alphai,
    shadow_config,
    volatile_universe,
)

logger = logging.getLogger("bot.live.momentum_volatile_runner")


def _volatile_flag_path(state_path: str) -> Path:
    """Own flag file — never collide with core ``momentum_desk_running.json``."""
    return Path(state_path).with_name("momentum_volatile_running.json")


def _write_volatile_flag(state_path: str, **payload: Any) -> None:
    import json

    path = _volatile_flag_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"updated_at": datetime.now(UTC).isoformat(), **payload}),
        encoding="utf-8",
    )


def _read_volatile_flag(state_path: str) -> dict[str, Any] | None:
    import json

    path = _volatile_flag_path(state_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def volatile_shadow_from_settings(settings: Settings | None = None) -> VolatileShadowConfig:
    """Smart shadow defaults + env book/clip/risk overrides."""
    settings = settings or get_settings()
    base = shadow_config()
    return replace(
        base,
        clip_eur=float(
            getattr(settings, "momentum_volatile_clip_eur", base.clip_eur) or base.clip_eur
        ),
        max_positions=int(
            getattr(settings, "momentum_volatile_max_positions", base.max_positions)
            or base.max_positions
        ),
        book_eur=float(
            getattr(settings, "momentum_volatile_book_eur", base.book_eur) or base.book_eur
        ),
        day_loss_limit_eur=float(
            getattr(settings, "momentum_volatile_day_loss_limit_eur", base.day_loss_limit_eur)
            or base.day_loss_limit_eur
        ),
        week_loss_limit_eur=float(
            getattr(settings, "momentum_volatile_week_loss_limit_eur", base.week_loss_limit_eur)
            or base.week_loss_limit_eur
        ),
    )


def volatile_desk_config(shadow: VolatileShadowConfig) -> DeskConfig:
    """DeskConfig for risk ledger / schedule / disaster stop (volatile params)."""
    return DeskConfig(
        decision_hours_utc=shadow.decision_hours_utc,
        clip_eur=shadow.clip_eur,
        alphai_clip_mult=shadow.alphai_clip_mult,
        max_positions=shadow.max_positions,
        top_n=shadow.top_n,
        min_volume_eur=shadow.min_volume_eur,
        max_from_high=shadow.max_from_high,
        trail_pct=shadow.trail_pct,
        trail_tight_after=shadow.trail_tight_after,
        trail_tight_pct=shadow.trail_tight_pct,
        hard_stop_pct=shadow.hard_stop_pct,
        time_exit_hours=shadow.time_exit_hours,
        fee_rt=shadow.fee_rt,
        day_loss_limit_eur=shadow.day_loss_limit_eur,
        week_loss_limit_eur=shadow.week_loss_limit_eur,
        pause_hours_after_week_limit=shadow.pause_hours_after_week_limit,
        max_entries_per_base_per_day=shadow.max_entries_per_base_per_day,
        skip_weekend_entries=shadow.skip_weekend_entries,
        alphai_avoid_tightens_trail=True,
        book_eur=shadow.book_eur,
        universe=shadow.universe or volatile_universe(),
        clusters=dict(shadow.clusters),
        macro_caution_mode="reduce",
        macro_caution_clip_mult=shadow.macro_caution_clip_mult,
    )


class VolatileDeskRunner(MomentumDeskRunner):
    """Same tick/execution loop; AlphaI-volatile decide + adaptive exits."""

    def __init__(
        self,
        cfg: DeskConfig,
        gateway: Gateway | None,
        *,
        shadow: VolatileShadowConfig,
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
        self.shadow = shadow

    def status(self) -> dict[str, Any]:
        out = super().status()
        out["desk"] = "momentum_volatile"
        out["book_eur"] = float(self.shadow.book_eur)
        out["deployed_eur"] = round(self._deployed_eur(), 2)
        out["book_left_eur"] = round(
            max(0.0, float(self.shadow.book_eur) - self._deployed_eur()), 2
        )
        out["mode"] = "volatile_live"
        return out

    def _deployed_eur(self) -> float:
        return sum(max(0.0, float(h.pos.notional_eur)) for h in self.holdings)

    def _route_entry(self, clip_eur: float) -> tuple[str, float] | None:
        """Venue cash + soft sleeve book (do not spend the whole venue balance)."""
        route = super()._route_entry(clip_eur)
        if route is None:
            return None
        venue, clip = route
        book = float(self.shadow.book_eur or 0.0)
        if book <= 0:
            return venue, clip
        free_book = max(0.0, book - self._deployed_eur())
        min_ok = max(_MIN_ORDER_EUR, float(clip_eur) * float(self.opt.min_clip_fraction))
        if free_book < min_ok:
            return None
        return venue, round(min(clip, free_book), 2)

    def _alphai_view(self) -> AlphaIView:
        path = self.opt.alphai_recommendations_path
        view, meta = load_shadow_alphai(path)
        scores = dict(meta.get("scores") or {})
        self.shadow = replace(self.shadow, alphai_scores=scores)
        return view

    async def _manage_exits(self, now_ms: int) -> None:
        if not self.holdings:
            return
        last_closed = (now_ms // BAR_MS) * BAR_MS - BAR_MS
        bar_ready = now_ms >= last_closed + BAR_MS + int(self.opt.bar_close_grace_sec * 1000)
        alphai = self._alphai_view() if bar_ready else None
        for h in list(self.holdings):
            if h.exiting:
                continue
            rows = await self._feed.candles(h.pos.base, 4)
            if not rows:
                continue
            live_px = float(rows[-1][4])
            self.marks[h.pos.base] = live_px
            if live_px <= h.pos.entry_price * (1 - _DISASTER_STOP_MULT * self.cfg.hard_stop_pct):
                await self._exit(
                    h, ExitDecision("disaster_stop", h.pos.gross_return(live_px), True)
                )
                continue
            if not bar_ready or h.last_bar_ms >= last_closed or alphai is None:
                continue
            bar = next((r for r in rows if int(r[0]) == last_closed), None)
            if bar is None:
                continue
            h.last_bar_ms = last_closed
            decision = _evaluate_volatile_exit(h.pos, bar, self.shadow, alphai)
            if decision is not None:
                await self._exit(h, decision)

    async def _decide(
        self,
        t_ms: int,
        now_ms: int,
        *,
        execute: bool,
        trigger: str,
        expect_bases: set[str] | None = None,
    ) -> dict[str, Any]:
        alphai = self._alphai_view()
        held = {h.pos.base for h in self.holdings}
        need = sorted(set(alphai.picks) | held | {"BTC"})
        candles: dict[str, Any] = {}
        for base in need:
            try:
                candles[base] = await self._feed.candles(base, 110)
            except Exception as exc:  # noqa: BLE001
                logger.warning("volatile desk: candles failed for %s: %s", base, exc)

        scan_cfg = replace(
            self.cfg,
            universe=tuple(b for b in need if b != "BTC" and b in candles),
        )
        stats = universe_stats(candles, t_ms, scan_cfg) if scan_cfg.universe else {}
        btc = bar_stats("BTC", candles["BTC"], t_ms) if candles.get("BTC") else None
        btc_ret = float(btc.ret_24h) if btc is not None else 0.0
        cands, rejected = _rank_volatile(stats, btc_ret, self.shadow, alphai)
        allowed, why = self.ledger.entries_allowed(now_ms)
        entries: list[Entry] = []
        if allowed:
            if alphai.macro_caution and not alphai.picks:
                allowed, why = False, "macro_caution_no_picks"
            elif self.shadow.require_alphai_green and not alphai.picks:
                allowed, why = False, "no_alphai_volatile_picks"
            else:
                planned_rows = _select_volatile(
                    cands,
                    self.shadow,
                    held=held,
                    blocked=self.ledger.blocked_bases(
                        now_ms, self.shadow.max_entries_per_base_per_day
                    ),
                    alphai=alphai,
                )
                for row in planned_rows:
                    clip = float(row["clip_eur"])
                    free_book = max(0.0, float(self.shadow.book_eur) - self._deployed_eur())
                    clip = min(clip, free_book)
                    if clip < max(_MIN_ORDER_EUR, float(self.shadow.clip_eur) * 0.5):
                        continue
                    entries.append(
                        Entry(
                            base=str(row["base"]),
                            clip_eur=round(clip, 2),
                            score=float(row.get("score") or 0.0),
                            reasons=tuple(str(x) for x in (row.get("reasons") or [])),
                        )
                    )

        summary: dict[str, Any] = {
            "at": datetime.fromtimestamp(t_ms / 1000, UTC).isoformat(),
            "trigger": trigger,
            "executed": execute,
            "ok": bool(allowed),
            "desk": "momentum_volatile",
            "btc_ret": round(btc_ret, 4),
            "reasons": [],
            "candidates": [
                {
                    "base": c.base,
                    "score": round(c.score, 2),
                    "alphai_score": c.alphai_score,
                    "excess": round(c.excess, 4),
                    "from_high": round(c.from_high, 4),
                }
                for c in cands[:6]
            ],
            "rejected": rejected[:8],
            "entries": [e.base for e in entries],
            "planned": [self._plan_row(e, stats) for e in entries],
            "risk_block": "" if allowed else why,
            "book_left_eur": round(
                max(0.0, float(self.shadow.book_eur) - self._deployed_eur()), 2
            ),
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
            await self._enter(entry.base, entry.clip_eur, ",".join(entry.reasons), now_ms)
        self._save_state()
        return summary


class VolatileDeskManager:
    """Start/stop singleton for the live volatile sleeve."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._runner: VolatileDeskRunner | None = None
        self._stop = False
        self._engine: Any = None
        self._commit_task: asyncio.Task[None] | None = None
        self._commit: dict[str, Any] = {}
        self._sell_task: asyncio.Task[None] | None = None
        self._manual_exit: dict[str, Any] = {}

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        allow_live = bool(getattr(settings, "momentum_volatile_allow_live", False))
        dry = bool(getattr(self._runner.opt, "dry_run", True)) if self._runner is not None else True
        base: dict[str, Any] = {
            "running": self.running(),
            "enabled_setting": bool(getattr(settings, "momentum_volatile_enabled", False)),
            "allow_live": allow_live,
            "desk": "momentum_volatile",
            "mode": "volatile_paper" if dry or not allow_live else "volatile_live",
        }
        if self._runner is not None:
            base.update(self._runner.status())
            dry = bool(getattr(self._runner.opt, "dry_run", True))
            base["dry_run"] = dry
            base["mode"] = "volatile_paper" if dry else "volatile_live"
        if self._commit:
            base["commit"] = dict(self._commit)
        if self._manual_exit:
            base["manual_exit"] = dict(self._manual_exit)
        if self._task is not None and self._task.done() and self._task.exception():
            base["task_error"] = repr(self._task.exception())
        return base

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
        if not bool(getattr(settings, "momentum_volatile_enabled", False)):
            return {
                "ok": False,
                "started": False,
                "reason": "momentum_volatile_enabled_false",
                "hint": "Set MOMENTUM_VOLATILE_ENABLED=true to arm the sleeve",
            }
        allow_live = bool(getattr(settings, "momentum_volatile_allow_live", False))
        if not dry_run and not allow_live:
            return {
                "ok": False,
                "started": False,
                "reason": "live_orders_disabled",
                "hint": (
                    "MOMENTUM_VOLATILE_ALLOW_LIVE=false — only paper/dry_run is allowed. "
                    "Set ALLOW_LIVE=true to re-arm real orders."
                ),
                "status": self.status(),
            }
        shadow = volatile_shadow_from_settings(settings)
        cfg = volatile_desk_config(shadow)
        raw_venues = venue
        if raw_venues is None:
            raw_venues = getattr(settings, "momentum_volatile_venues", None) or "bitvavo"
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
                    logger.warning("volatile desk: no trading credentials for %s; skipped", v)
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
            getattr(settings, "momentum_volatile_state_path", "./data/momentum_volatile_state.json")
        )
        ledger_path = str(
            getattr(
                settings,
                "momentum_volatile_ledger_path",
                "./data/momentum_volatile_ledger.jsonl",
            )
        )
        alphai_path = str(
            getattr(settings, "alphai_volatile_recommendations_path", None)
            or "data/alphai/volatile_recommendations.json"
        )
        options = RunnerOptions(
            venues=venues,
            dry_run=bool(dry_run),
            state_path=state_path,
            ledger_path=ledger_path,
            alphai_recommendations_path=alphai_path,
        )
        self._runner = VolatileDeskRunner(
            cfg, None, shadow=shadow, options=options, gateways=gateways
        )
        self._stop = False
        self._task = asyncio.create_task(
            self._runner.run(lambda: self._stop), name="momentum-volatile"
        )
        Path(options.state_path).parent.mkdir(parents=True, exist_ok=True)
        _write_volatile_flag(
            options.state_path,
            running=True,
            dry_run=bool(dry_run),
            venue=venues[0],
            venues=list(venues),
        )
        logger.info(
            "volatile sleeve started dry_run=%s venues=%s book=%.0f clip=%.0f",
            dry_run,
            venues,
            shadow.book_eur,
            shadow.clip_eur,
        )
        return {"ok": True, "started": True, "status": self.status()}

    async def decide(self, *, execute: bool) -> dict[str, Any]:
        if self._runner is None or not self.running():
            return {"ok": False, "reason": "not_running"}
        summary = await self._runner.decide_now(execute=execute)
        return {"ok": True, "decision": summary, "status": self.status()}

    def commit(self, bases: Sequence[str]) -> dict[str, Any]:
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
                res = await runner.decide_now(execute=True, expect_bases=set(expect))
                self._commit["result"] = {
                    "entries": res.get("entries"),
                    "mismatch": bool(res.get("mismatch")),
                    "planned": [p.get("base") for p in res.get("planned") or []],
                    "risk_block": res.get("risk_block"),
                    "ok": res.get("ok"),
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception("volatile desk: commit failed")
                self._commit["result"] = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._commit["done"] = True
                self._commit["finished_at"] = datetime.now(UTC).isoformat()

        self._commit_task = asyncio.create_task(_run(), name="volatile-commit")
        return {"ok": True, "commit": dict(self._commit)}

    def sell(self, holding_id: str, *, urgent: bool = False) -> dict[str, Any]:
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
                logger.exception("volatile desk: manual sell failed")
                self._manual_exit["result"] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            finally:
                self._manual_exit["done"] = True
                self._manual_exit["finished_at"] = datetime.now(UTC).isoformat()

        self._sell_task = asyncio.create_task(_run(), name="volatile-sell")
        return {"ok": True, "manual_exit": dict(self._manual_exit)}

    def sell_all(self, *, urgent: bool = False) -> dict[str, Any]:
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
                logger.exception("volatile desk: sell-all failed")
                self._manual_exit["result"] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            finally:
                self._manual_exit["done"] = True
                self._manual_exit["finished_at"] = datetime.now(UTC).isoformat()

        self._sell_task = asyncio.create_task(_run(), name="volatile-sell-all")
        return {"ok": True, "manual_exit": dict(self._manual_exit)}

    async def stop(self) -> dict[str, Any]:
        self._stop = True
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=45.0)
            except TimeoutError:
                task.cancel()
        if self._runner is not None:
            _write_volatile_flag(self._runner.opt.state_path, running=False)
        return {"stopped": True, "status": self.status()}

    async def resume_if_flagged(self, settings: Settings | None = None) -> dict[str, Any] | None:
        settings = settings or get_settings()
        if not bool(getattr(settings, "momentum_volatile_enabled", False)):
            return None
        state_path = str(
            getattr(settings, "momentum_volatile_state_path", "./data/momentum_volatile_state.json")
        )
        flag = _read_volatile_flag(state_path)
        if not flag or not flag.get("running"):
            return None
        allow_live = bool(getattr(settings, "momentum_volatile_allow_live", False))
        dry = True if not allow_live else bool(flag.get("dry_run", True))
        if not allow_live and not bool(flag.get("dry_run", True)):
            logger.warning(
                "volatile desk: flag had live orders; forcing paper/dry_run "
                "(MOMENTUM_VOLATILE_ALLOW_LIVE=false)"
            )
        logger.warning("volatile desk: resuming after restart (dry_run=%s)", dry)
        return await self.start(
            settings=settings,
            dry_run=dry,
            venue=flag.get("venues") or str(flag.get("venue") or "bitvavo"),
        )


def volatile_desk_flagged_running(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    state_path = str(
        getattr(settings, "momentum_volatile_state_path", "./data/momentum_volatile_state.json")
    )
    flag = _read_volatile_flag(state_path)
    return bool(flag and flag.get("running"))


_volatile_manager: VolatileDeskManager | None = None


def get_volatile_desk_manager() -> VolatileDeskManager:
    global _volatile_manager
    if _volatile_manager is None:
        _volatile_manager = VolatileDeskManager()
    return _volatile_manager


def reset_volatile_desk_manager() -> None:
    global _volatile_manager
    _volatile_manager = None


__all__ = [
    "VolatileDeskManager",
    "VolatileDeskRunner",
    "get_volatile_desk_manager",
    "reset_volatile_desk_manager",
    "volatile_desk_config",
    "volatile_desk_flagged_running",
    "volatile_shadow_from_settings",
]

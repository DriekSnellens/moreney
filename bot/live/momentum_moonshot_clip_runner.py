"""MoonShot — fixed-book daily-green sleeve beside residual/clip.

Pack ``daily_brk20_day`` (walk-forward dual IS+OOS winner):
  20d breakout on the ~80-name liquid pool, trail 12%, hard-stop 5%,
  time≤5d, sizing capped at book_eur. Dashboard title is MoonShot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from dataclasses import replace

from bot.live.momentum_btc_rs_clip import daily_green_config
from bot.live.momentum_btc_rs_clip_runner import (
    BtcRsClipPaperRunner,
    config_from_settings,
    load_side_desk_reserved_qty,
    reserved_quote_eur_from_settings,
)
from bot.live.momentum_desk import DeskConfig
from bot.live.momentum_runner import LiveGateway, engine_settings_for_desk, parse_venues

logger = logging.getLogger("bot.live.momentum_moonshot_clip_runner")

_EQUITY_CURVE_MAX = 2_000


def _flag_path(state_path: str) -> Path:
    return Path(state_path).with_name("momentum_moonshot_clip_running.json")


def _write_flag(state_path: str, **payload: Any) -> None:
    path = _flag_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"updated_at": time.time(), **payload}
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")


def moonshot_config_from_settings(settings: Settings | None = None):
    settings = settings or get_settings()
    book = float(getattr(settings, "momentum_moonshot_clip_book_eur", 2_000.0) or 2_000.0)
    base = config_from_settings(settings)
    cfg = daily_green_config(replace(base, book_eur=book))
    # Buy the first pierce of the 20d high. A day that is already +8%,
    # or a price more than 3% through that high, is the move itself.
    return replace(
        cfg,
        entry_mode="brk20_now",
        entry_scan_sec=300.0,
        max_entry_day_ret=0.08,
        max_break_extension=0.03,
    )


class MoonshotClipDeskManager:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = False
        self._runner: BtcRsClipPaperRunner | None = None
        self._engine: Any = None
        self._last_reconcile_mono = 0.0

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        enabled = bool(getattr(settings, "momentum_moonshot_clip_enabled", False))
        allow_live = bool(getattr(settings, "momentum_moonshot_clip_allow_live", False))
        base: dict[str, Any] = {
            "running": self.running(),
            "enabled_setting": enabled,
            "desk": "moonshot_clip" if allow_live else "moonshot_clip_paper",
            "mode": "moonshot_clip_live" if allow_live else "moonshot_clip_paper",
            "dry_run": not allow_live,
            "paper_only": not allow_live,
            "allow_live": allow_live,
            "pack": "brk20_now",
            "title": "MoonShot",
        }
        if self._runner is not None:
            st = self._runner.status()
            st.update(base)
            st["allow_live"] = allow_live and not self._runner.dry_run
            st["paper_only"] = self._runner.dry_run
            st["pack"] = "brk20_now"
            return st
        if enabled:
            cfg = moonshot_config_from_settings(settings)
            state_path = str(
                getattr(
                    settings,
                    "momentum_moonshot_clip_state_path",
                    "./data/momentum_moonshot_clip_state.json",
                )
            )
            cash = cfg.book_eur
            positions: list[dict[str, Any]] = []
            last: dict[str, Any] = {}
            last_rebalance_ms = 0
            curve: list[list[float]] = []
            realized = 0.0
            try:
                raw = json.loads(Path(state_path).read_text(encoding="utf-8"))
                cash = float(raw.get("cash_eur", cash))
                realized = float(raw.get("realized_total_eur") or 0.0)
                positions = list(raw.get("positions") or [])
                last = dict(raw.get("last_decision") or {})
                last_rebalance_ms = int(raw.get("last_rebalance_ms") or 0)
                saved_board = raw.get("alphai_board")
                if isinstance(saved_board, dict):
                    base["alphai_board"] = dict(saved_board)
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
            unrealized = 0.0
            for pos in positions:
                try:
                    unrealized += float(pos.get("unrealized_net_eur") or 0.0)
                except (TypeError, ValueError):
                    continue
            base.update(
                {
                    "book_eur": cfg.book_eur,
                    "cash_eur": round(cash, 2),
                    "equity_eur": round(cash + deployed, 2),
                    "deployed_eur": round(deployed, 2),
                    "positions": positions,
                    "realized_total_eur": round(realized, 2),
                    "unrealized_net_eur": round(unrealized, 2),
                    "equity_curve": curve[-_EQUITY_CURVE_MAX:],
                    "last_decision": last,
                    "live_caption": str(
                        last.get("caption")
                        or "MoonShot sleeve klaar (niet gestart)."
                    ),
                    "config": {
                        "book_eur": cfg.book_eur,
                        "excess_floor": cfg.excess_floor,
                        "min_r3_pct": cfg.min_r3_pct,
                        "require_trend": cfg.require_trend,
                        "trail_pct": cfg.alt_trail_pct,
                        "size_to_book": cfg.size_to_book,
                    },
                }
            )
            from datetime import UTC, datetime

            from bot.live.momentum_btc_rs_clip import breakout_board_headline

            board = dict(base.get("alphai_board") or {})
            board.update(
                breakout_board_headline(last, cfg, last_rebalance_ms, datetime.now(UTC))
            )
            board.setdefault("rows", [])
            board.setdefault("avoid", [])
            board.setdefault("cadence", "elk kwartier")
            base["alphai_board"] = board
        return base

    async def start(self, *, settings: Settings | None = None) -> dict[str, Any]:
        settings = settings or get_settings()
        allow_live = bool(getattr(settings, "momentum_moonshot_clip_allow_live", False))
        if self.running():
            return {
                "ok": False,
                "started": False,
                "reason": "already_running",
                "status": self.status(),
            }
        if not bool(getattr(settings, "momentum_moonshot_clip_enabled", False)):
            return {
                "ok": False,
                "started": False,
                "reason": "momentum_moonshot_clip_enabled_false",
                "hint": "Set MOMENTUM_MOONSHOT_CLIP_ENABLED=true",
            }
        cfg = moonshot_config_from_settings(settings)
        state_path = str(
            getattr(
                settings,
                "momentum_moonshot_clip_state_path",
                "./data/momentum_moonshot_clip_state.json",
            )
        )
        ledger_path = str(
            getattr(
                settings,
                "momentum_moonshot_clip_ledger_path",
                "./data/momentum_moonshot_clip_ledger.jsonl",
            )
        )
        venues = parse_venues(
            str(getattr(settings, "momentum_moonshot_clip_venues", "bitvavo") or "bitvavo")
        )
        gateways: dict[str, Any] = {}
        dry_run = not allow_live
        # Keep the 15m desk's EUR aside. Do not also reserve the owner clip's
        # configured book: that pile is the same Bitvavo balance, and MoonShot
        # already caps its own ticket at book_eur.
        reserved_quote = reserved_quote_eur_from_settings(settings)
        reserved_qty = load_side_desk_reserved_qty(settings, venues[0] if venues else "bitvavo")
        if allow_live:
            from bot.live.micro_engine import LiveMicroEngine

            book = float(cfg.book_eur)
            desk_cfg = DeskConfig(
                clip_eur=max(book, 500.0),
                max_positions=1,
                day_loss_limit_eur=max(book * 0.25, 400.0),
            )
            engine = LiveMicroEngine(engine_settings_for_desk(settings, desk_cfg, venues))
            armed = engine.arm()
            if not armed.get("armed"):
                return {"ok": False, "started": False, "reason": "arm_failed", "detail": armed}
            for v in venues:
                if engine._registry.get_client(v, enable_trading=True) is None:  # noqa: SLF001
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
            pending_pack="",
        )
        self._runner.pack_mode = "brk20_now"
        self._runner.pin_cash_to_book = True
        # Fresh sleeve: seed cash to fixed book if empty state.
        if not self._runner.positions and self._runner.cash_eur <= 0:
            self._runner.cash_eur = float(cfg.book_eur)
        elif not Path(state_path).exists():
            self._runner.cash_eur = float(cfg.book_eur)
        if not dry_run:
            dropped = self._runner.discard_paper_positions()
            logger.info("moonshot live: dropped %s paper lots", dropped)
            # After discard, re-seed fixed book cash for live start.
            if not self._runner.positions:
                self._runner.cash_eur = float(cfg.book_eur)
        self._stop = False
        self._task = asyncio.create_task(
            self._runner.run(lambda: self._stop), name="momentum-moonshot-clip"
        )
        _write_flag(
            state_path,
            running=True,
            dry_run=dry_run,
            paper_only=dry_run,
            allow_live=not dry_run,
            pack="brk20_now",
            book_eur=cfg.book_eur,
        )
        return {"ok": True, "started": True, "status": self.status()}

    async def stop(self) -> dict[str, Any]:
        self._stop = True
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=20.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
        self._task = None
        self._runner = None
        settings = get_settings()
        state_path = str(
            getattr(
                settings,
                "momentum_moonshot_clip_state_path",
                "./data/momentum_moonshot_clip_state.json",
            )
        )
        _write_flag(state_path, running=False, pack="brk20_now")
        return {"ok": True, "stopped": True, "status": self.status()}

    async def resume_if_flagged(self) -> dict[str, Any] | None:
        settings = get_settings()
        if not bool(getattr(settings, "momentum_moonshot_clip_enabled", False)):
            return None
        state_path = str(
            getattr(
                settings,
                "momentum_moonshot_clip_state_path",
                "./data/momentum_moonshot_clip_state.json",
            )
        )
        flag = _flag_path(state_path)
        if not flag.exists():
            return await self.start(settings=settings)
        try:
            raw = json.loads(flag.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raw = {}
        if not raw.get("running", True):
            return None
        return await self.start(settings=settings)

    async def refresh_live(self) -> dict[str, Any]:
        """Bitvavo marks, inventory, and the pinned book on every dashboard poll."""
        if self._runner is not None:
            try:
                await self._runner._refresh_marks()
            except Exception:  # noqa: BLE001
                logger.exception("moonshot: mark refresh failed")
            try:
                await self._runner.reconcile_external_inventory()
            except Exception:  # noqa: BLE001
                logger.exception("moonshot: reconcile failed")
            try:
                await self._runner._decision_cash()
            except Exception:  # noqa: BLE001
                logger.exception("moonshot: venue cash sync failed")
            try:
                self._runner._sample_equity()
            except Exception:  # noqa: BLE001
                logger.exception("moonshot: equity sample failed")
        return self.status()

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        if self._runner is None:
            return {"ok": False, "reason": "not_running"}
        return await self._runner.decide(execute=execute)


_MANAGER: MoonshotClipDeskManager | None = None


def get_moonshot_clip_desk_manager() -> MoonshotClipDeskManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = MoonshotClipDeskManager()
    return _MANAGER

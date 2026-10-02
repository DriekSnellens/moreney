"""Paper runner/manager for the ignition early-signal sleeve (never live orders)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.live.momentum_ignition import (
    IgnitionConfig,
    IgnitionPosition,
    default_config,
    evaluate_ignition,
    fill_px,
    trail_exit,
)
from bot.live.momentum_runner import CandleFeed, parse_venues
from bot.live.momentum_short_weakest import fetch_daily_ohlc

logger = logging.getLogger("bot.live.momentum_ignition_runner")

_EQUITY_CURVE_MAX = 2016
_EQUITY_CURVE_MIN_GAP_SEC = 5.0
_OHLC_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ign-ohlc")


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

    return replace(
        base,
        book_eur=_f("momentum_ignition_book_eur", base.book_eur),
        max_positions=_i("momentum_ignition_max_positions", base.max_positions),
        deploy_frac=_f("momentum_ignition_deploy_frac", base.deploy_frac),
        trail_pct=_f("momentum_ignition_trail_pct", base.trail_pct),
        quiet_max=_f("momentum_ignition_quiet_max", base.quiet_max),
        day_ret_min=_f("momentum_ignition_day_ret_min", base.day_ret_min),
        vol_mult_min=_f("momentum_ignition_vol_mult_min", base.vol_mult_min),
        min_median_qvol_eur=_f(
            "momentum_ignition_min_median_qvol_eur", base.min_median_qvol_eur
        ),
        min_day_qvol_eur=_f("momentum_ignition_min_day_qvol_eur", base.min_day_qvol_eur),
        require_btc_sma=_b("momentum_ignition_require_btc_sma", base.require_btc_sma),
        min_points=_i("momentum_ignition_min_points", base.min_points),
        tick_sec=_f("momentum_ignition_tick_sec", base.tick_sec),
    )


class IgnitionPaperRunner:
    """Paper long book targeting OKX for future live fills.

    Marks come from the public ticker today. Orders stay synthetic until
    ``momentum_ignition_allow_live`` is armed and an OKX gateway is wired.
    """

    def __init__(
        self,
        cfg: IgnitionConfig,
        *,
        state_path: str,
        ledger_path: str,
        venues: tuple[str, ...] = ("okx",),
        allow_live: bool = False,
    ) -> None:
        self.cfg = cfg
        self.state_path = state_path
        self.ledger_path = ledger_path
        self.venues = parse_venues(venues) or ("okx",)
        # Live OKX path is not wired yet — always paper regardless of flag.
        self.allow_live = False
        self._allow_live_requested = bool(allow_live)
        self.dry_run = True
        self.paper_only = True
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
        self._load_state()

    def _desk(self) -> str:
        return "ignition_paper"

    def _primary_venue(self) -> str:
        return self.venues[0] if self.venues else "okx"

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
                    "paper_only": True,
                    "allow_live": False,
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
        self._sample_equity()

    async def _load_ohlc(self) -> dict[str, list[list[float]]]:
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

    def _open_lot(
        self, base: str, notional: float, px: float, reasons: list[str], *, points: int
    ) -> IgnitionPosition | None:
        if notional < self.cfg.min_notional_eur or px <= 0:
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
        venue = self._primary_venue()
        pos = IgnitionPosition(
            base=base,
            entry_price=px,
            notional_eur=notional,
            qty=notional / px,
            opened_ms=int(time.time() * 1000),
            venue=venue,
            entry_reason=",".join(reasons),
            peak_px=px,
            points=points,
        )
        self.positions.append(pos)
        self.marks[base] = px
        self.mark_ts[base] = time.time()
        self._ledger_append(
            {
                "event": "entry",
                "desk": self._desk(),
                "side": "long",
                "base": base,
                "venue": venue,
                "dry_run": True,
                "paper_only": True,
                "target_venue": venue,
                "notional_eur": round(notional, 2),
                "quantity": pos.qty,
                "entry_price": px,
                "fee_eur": round(fee, 4),
                "reason": pos.entry_reason,
                "holding_id": pos.holding_id,
                "points": points,
            }
        )
        return pos

    async def _close_lot(self, pos: IgnitionPosition, px: float, reason: str) -> float | None:
        if px <= 0:
            return None
        qty = float(pos.qty or 0.0)
        if qty <= 0 and pos.entry_price > 0:
            qty = pos.notional_eur / pos.entry_price
        if qty <= 0:
            return None
        fee = qty * px * (self.cfg.fee_rt / 2)
        proceeds = qty * px - fee
        gross = pos.gross_return(px)
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
                "dry_run": True,
                "paper_only": True,
                "target_venue": self._primary_venue(),
                "notional_eur": round(pos.notional_eur, 2),
                "quantity": qty,
                "entry_price": pos.entry_price,
                "exit_price": px,
                "fee_eur": round(fee, 4),
                "net_eur": round(net, 2),
                "gross_return": round(gross, 4),
                "reason": reason,
                "holding_id": pos.holding_id,
                "points": pos.points,
            }
        )
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
                if "trail" in ",".join(a.get("reason", "") for a in applied):
                    ld["caption"] = (
                        f"Ignition PAPER: desk-universe early-signal + trail "
                        f"{self.cfg.trail_pct:.0%}. Slot vrij na exit."
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
        reason: str = "operator_paper",
    ) -> dict[str, Any]:
        """Manual paper open — no venue orders. Generic base, never coin-hardcoded."""
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
        notion = float(notional_eur) if notional_eur is not None else (
            min(float(self.cash_eur), float(self.cfg.book_eur)) * float(self.cfg.deploy_frac)
        )
        reasons = [str(reason or "operator_paper"), "manual"]
        pos = self._open_lot(base_u, notion, px, reasons, points=0)
        if pos is None:
            return {"ok": False, "reason": "open_failed", "base": base_u}
        self.last_decision = {
            **(self.last_decision or {}),
            "want": base_u,
            "caption": (
                f"Ignition PAPER: handmatige paper-entry {base_u} "
                f"({pos.notional_eur:.0f} EUR @ {px:.6g})."
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
            "paper_only": True,
            "status": self.status(),
        }

    async def sell(self, holding_id: str) -> dict[str, Any]:
        """Manual paper close — no venue orders."""
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

    async def decide(self, *, execute: bool = True) -> dict[str, Any]:
        async with self._decide_lock:
            now = datetime.now(UTC)
            self._roll_day(now)
            ohlc = await self._load_ohlc()
            held = [p.base for p in self.positions]
            decision = evaluate_ignition(
                ohlc,
                self.cfg,
                held=held,
                cash_eur=self.cash_eur,
                now=now,
            )
            applied: list[dict[str, Any]] = []
            if execute and decision.get("ok") and not decision.get("risk_block"):
                for row in decision.get("entries") or []:
                    base = str(row["base"])
                    # Enter at next-open proxy: last completed close * (1+slip).
                    rows = ohlc.get(base) or []
                    if not rows:
                        continue
                    close = float(rows[-1][4])
                    # Prefer live mark when fresher.
                    mark = float(self.marks.get(base) or 0.0)
                    px_src = mark if mark > 0 else close
                    px = fill_px(px_src, "buy", slip=self.cfg.slip)
                    score = row.get("score") or {}
                    pos = self._open_lot(
                        base,
                        float(row["notional_eur"]),
                        px,
                        list(row.get("reasons") or []),
                        points=int(score.get("points") or 0),
                    )
                    if pos:
                        applied.append(
                            {
                                "action": "entry",
                                "base": pos.base,
                                "notional_eur": pos.notional_eur,
                                "points": pos.points,
                            }
                        )
            self.last_decision = {
                **decision,
                "applied": applied,
                "execute": bool(execute),
                "at": now.isoformat(),
            }
            await self._refresh_marks()
            self._save_state()
            return {"ok": True, "decision": self.last_decision, "status": self.status()}

    def next_decision(self) -> str:
        now = datetime.now(UTC)
        hours = sorted(int(h) for h in self.cfg.decision_hours_utc)
        for h in hours:
            if now.hour < h or (now.hour == h and now.minute < 5):
                return now.replace(hour=h, minute=5, second=0, microsecond=0).isoformat()
        # next day first hour
        nxt = now.replace(hour=hours[0], minute=5, second=0, microsecond=0)
        from datetime import timedelta

        return (nxt + timedelta(days=1)).isoformat()

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
        caption = str(last.get("caption") or "")
        if caption and "OKX" not in caption.upper():
            caption = caption.rstrip(".") + f". Target {venue.upper()} (paper)."
        return {
            "desk": self._desk(),
            "mode": "ignition_paper",
            "paper_only": True,
            "allow_live": False,
            "allow_live_requested": bool(self._allow_live_requested),
            "dry_run": True,
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
                "quiet_max": self.cfg.quiet_max,
                "day_ret_min": self.cfg.day_ret_min,
                "vol_mult_min": self.cfg.vol_mult_min,
                "min_points": self.cfg.min_points,
                "max_positions": self.cfg.max_positions,
                "book_eur": self.cfg.book_eur,
                "signal": "quiet+brk20+r1_6+vol2",
                "universe_n": len(self.cfg.universe),
                "venues": list(self.venues),
                "target_venue": venue,
                "allow_live": False,
            },
        }

    async def run(self, should_stop) -> None:  # noqa: ANN001
        hours = set(int(h) for h in self.cfg.decision_hours_utc)
        last_hour_fire: set[str] = set()
        await self._refresh_marks()
        now = datetime.now(UTC)
        in_window = now.hour in hours and now.minute < 8
        try:
            await self.decide(execute=in_window)
        except Exception:  # noqa: BLE001
            logger.exception("ignition kick decide failed")
        if in_window:
            last_hour_fire.add(f"{now.date()}-{now.hour}")
        while not should_stop():
            try:
                await self._refresh_marks()
                async with self._decide_lock:
                    await self.manage_trail()
                now = datetime.now(UTC)
                key = f"{now.date()}-{now.hour}"
                if now.hour in hours and now.minute < 8 and key not in last_hour_fire:
                    await self.decide(execute=True)
                    last_hour_fire.add(key)
                if len(last_hour_fire) > 48:
                    last_hour_fire = {key}
            except Exception:  # noqa: BLE001
                logger.exception("ignition tick failed")
            await asyncio.sleep(float(self.cfg.tick_sec))
        self._save_state()


class IgnitionDeskManager:
    """Paper-only singleton for the ignition sleeve."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._runner: IgnitionPaperRunner | None = None
        self._stop = False

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        enabled = bool(getattr(settings, "momentum_ignition_enabled", False))
        venues = parse_venues(
            getattr(settings, "momentum_ignition_venues", "okx") or "okx"
        ) or ("okx",)
        allow_req = bool(getattr(settings, "momentum_ignition_allow_live", False))
        base: dict[str, Any] = {
            "running": self.running(),
            "enabled_setting": enabled,
            "desk": "ignition_paper",
            "mode": "ignition_paper",
            "dry_run": True,
            "paper_only": True,
            "allow_live": False,
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
            caption = str(last.get("caption") or "")
            if caption and "OKX" not in caption.upper():
                caption = caption.rstrip(".") + f". Target {venues[0].upper()} (paper)."
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
                        "signal": "quiet+brk20+r1_6+vol2",
                        "book_eur": cfg.book_eur,
                        "venues": list(venues),
                        "target_venue": venues[0],
                        "allow_live": False,
                    },
                }
            )
        return base

    async def start(self, settings: Settings | None = None) -> dict[str, Any]:
        settings = settings or get_settings()
        if self.running():
            return {"ok": True, "started": False, "reason": "already_running", "status": self.status()}
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
        if allow_live:
            # Refuse live until an OKX gateway fill path exists — stay paper.
            logger.warning(
                "ignition allow_live requested on %s but live OKX path is not wired; "
                "starting paper-only",
                ",".join(venues),
            )
        cfg = config_from_settings(settings)
        state_path = str(
            getattr(settings, "momentum_ignition_state_path", "./data/momentum_ignition_state.json")
        )
        ledger_path = str(
            getattr(
                settings, "momentum_ignition_ledger_path", "./data/momentum_ignition_ledger.jsonl"
            )
        )
        self._stop = False
        self._runner = IgnitionPaperRunner(
            cfg,
            state_path=state_path,
            ledger_path=ledger_path,
            venues=venues,
            allow_live=allow_live,
        )
        self._task = asyncio.create_task(
            self._runner.run(lambda: self._stop), name="momentum-ignition"
        )
        _write_flag(
            state_path,
            running=True,
            dry_run=True,
            paper_only=True,
            allow_live=False,
            venues=list(venues),
            target_venue=venues[0],
        )
        return {
            "ok": True,
            "started": True,
            "paper_only": True,
            "allow_live": False,
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
        _write_flag(path, running=False, dry_run=True, paper_only=True)
        self._task = None
        st = self.status()
        self._runner = None
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

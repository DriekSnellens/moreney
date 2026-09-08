"""Candle loader + deterministic simulator for the momentum desk."""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import (
    BAR_MS,
    AlphaIView,
    Candle,
    DeskConfig,
    Position,
    RiskLedger,
    bar_stats,
    classify_regime,
    evaluate_exit,
    is_decision_time,
    net_pnl_eur,
    rank_candidates,
    select_entries,
    universe_stats,
)

logger = logging.getLogger(__name__)

BITVAVO_CANDLES = "https://api.bitvavo.com/v2/{market}/candles"
_MAX_LIMIT = 1440


def _fetch_range(base: str, start_ms: int, end_ms: int) -> list[list[float]]:
    url = (
        f"{BITVAVO_CANDLES.format(market=f'{base}-EUR')}"
        f"?interval=15m&start={start_ms}&end={end_ms}&limit={_MAX_LIMIT}"
    )
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        rows = json.load(resp)
    out = []
    for r in rows:
        out.append([int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
    out.sort(key=lambda r: r[0])
    return out


def fetch_candles(
    base: str, start_ms: int, end_ms: int, *, pause_sec: float = 0.25
) -> list[list[float]]:
    """Fetch 15m candles across the range in ≤1440-bar pages."""
    rows: dict[int, list[float]] = {}
    cursor = start_ms
    page_ms = _MAX_LIMIT * BAR_MS
    while cursor < end_ms:
        page_end = min(end_ms, cursor + page_ms)
        for r in _fetch_range(base, cursor, page_end):
            rows[r[0]] = r
        cursor = page_end
        time.sleep(pause_sec)
    return [rows[k] for k in sorted(rows)]


def load_candles(
    bases: Sequence[str],
    *,
    days: int,
    end_ms: int | None = None,
    cache_dir: str | Path = "./data/momentum_candles",
    refresh: bool = False,
) -> dict[str, list[list[float]]]:
    """Load candles with a per-base JSON cache (refetches the tail when stale)."""
    end_ms = end_ms or int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - (days + 2) * 86_400_000
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[list[float]]] = {}
    for base in bases:
        path = cache / f"{base}-EUR-15m.json"
        rows: dict[int, list[float]] = {}
        if path.exists() and not refresh:
            try:
                for r in json.loads(path.read_text()):
                    rows[int(r[0])] = r
            except Exception:  # noqa: BLE001
                rows = {}
        have_from = min(rows) if rows else None
        have_to = max(rows) if rows else None
        try:
            if have_from is None or have_from > start_ms + BAR_MS:
                for r in fetch_candles(base, start_ms, have_from or end_ms):
                    rows[r[0]] = r
            if have_to is None or have_to < end_ms - BAR_MS:
                for r in fetch_candles(base, (have_to or start_ms) - BAR_MS, end_ms):
                    rows[r[0]] = r
        except Exception as exc:  # noqa: BLE001
            logger.warning("candle fetch failed for %s: %s", base, exc)
        if not rows:
            continue
        ordered = [rows[k] for k in sorted(rows)]
        path.write_text(json.dumps(ordered))
        out[base] = [r for r in ordered if start_ms <= r[0] <= end_ms]
    return out


@dataclass
class ClosedTrade:
    base: str
    opened_ms: int
    closed_ms: int
    entry_price: float
    exit_price: float
    notional_eur: float
    gross_return: float
    peak_return: float
    net_eur: float
    reason: str
    entry_reason: str

    def as_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["opened"] = _fmt(self.opened_ms)
        d["closed"] = _fmt(self.closed_ms)
        d["hold_h"] = round((self.closed_ms - self.opened_ms) / 3_600_000, 2)
        return d


@dataclass
class DecisionLog:
    t_ms: int
    regime_ok: bool
    btc_ret: float | None
    breadth: float
    reasons: tuple[str, ...]
    candidates: list[str]
    entries: list[str]
    risk_block: str


@dataclass
class BacktestResult:
    start_ms: int
    end_ms: int
    cfg: DeskConfig
    closed: list[ClosedTrade] = field(default_factory=list)
    open_mtm: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[DecisionLog] = field(default_factory=list)

    @property
    def realized_eur(self) -> float:
        return sum(t.net_eur for t in self.closed)

    @property
    def open_eur(self) -> float:
        return sum(float(m["net_eur"]) for m in self.open_mtm)

    @property
    def total_eur(self) -> float:
        return self.realized_eur + self.open_eur

    def summary(self) -> dict[str, Any]:
        wins = sum(1 for t in self.closed if t.net_eur > 0)
        n = len(self.closed)
        days_on = sum(1 for d in self.decisions if d.regime_ok)
        by_reason: dict[str, dict[str, float]] = {}
        for t in self.closed:
            slot = by_reason.setdefault(t.reason, {"n": 0, "net_eur": 0.0})
            slot["n"] += 1
            slot["net_eur"] += t.net_eur
        for slot in by_reason.values():
            slot["net_eur"] = round(slot["net_eur"], 2)
        return {
            "window": f"{_fmt(self.start_ms)} -> {_fmt(self.end_ms)}",
            "trades": n,
            "open": len(self.open_mtm),
            "win_rate": round(wins / n, 3) if n else None,
            "realized_eur": round(self.realized_eur, 2),
            "open_mtm_eur": round(self.open_eur, 2),
            "total_eur": round(self.total_eur, 2),
            "fees_eur": round(sum(t.notional_eur for t in self.closed) * self.cfg.fee_rt, 2),
            "avg_net_per_trade_eur": round(self.realized_eur / n, 2) if n else None,
            "max_drawdown_eur": round(_max_drawdown([t.net_eur for t in self.closed]), 2),
            "decision_points": len(self.decisions),
            "regime_on_points": days_on,
            "by_reason": by_reason,
        }


def _fmt(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M")


def _max_drawdown(series: Sequence[float]) -> float:
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for x in series:
        cum += x
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return worst


def simulate(
    candles_by_base: Mapping[str, Sequence[Candle]],
    cfg: DeskConfig,
    *,
    start_ms: int,
    end_ms: int,
    alphai: AlphaIView | None = None,
) -> BacktestResult:
    """Bar-by-bar replay. Fills at the close of the decision bar's predecessor.

    Entry price = last closed bar close (the live runner posts a maker bid at
    the book; this is the conservative mid-of-book assumption). Exits fill at
    the bar close that triggered the rule.
    """
    idx = {b: {int(r[0]): r for r in rows} for b, rows in candles_by_base.items()}
    ledger = RiskLedger(
        day_loss_limit_eur=cfg.day_loss_limit_eur,
        week_loss_limit_eur=cfg.week_loss_limit_eur,
        pause_hours=cfg.pause_hours_after_week_limit,
    )
    res = BacktestResult(start_ms=start_ms, end_ms=end_ms, cfg=cfg)
    positions: list[Position] = []
    t = start_ms // BAR_MS * BAR_MS
    while t <= end_ms:
        # 1) Exits on the bar that just closed (ts = t - BAR_MS).
        closed_ts = t - BAR_MS
        for pos in list(positions):
            bar = idx.get(pos.base, {}).get(closed_ts)
            if bar is None:
                continue
            decision = evaluate_exit(pos, bar, cfg)
            if decision is None:
                continue
            exit_price = decision.price if decision.price is not None else float(bar[4])
            net = net_pnl_eur(pos, exit_price, None, cfg)
            res.closed.append(
                ClosedTrade(
                    base=pos.base,
                    opened_ms=pos.opened_ms,
                    closed_ms=t,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    notional_eur=pos.notional_eur,
                    gross_return=decision.gross_return,
                    peak_return=pos.peak / pos.entry_price - 1.0,
                    net_eur=net,
                    reason=decision.reason,
                    entry_reason=pos.entry_reason,
                )
            )
            ledger.note_close(net, t)
            positions.remove(pos)
        # 2) Entries at decision hours.
        if is_decision_time(t, cfg):
            stats = universe_stats(candles_by_base, t, cfg)
            btc_rows = candles_by_base.get("BTC")
            btc = bar_stats("BTC", btc_rows, t) if btc_rows else None
            regime = classify_regime(btc, stats, cfg, alphai=alphai)
            cands = (
                rank_candidates(stats, regime.btc_ret or 0.0, cfg, alphai=alphai)
                if regime.ok
                else []
            )
            allowed, why = ledger.entries_allowed(t)
            entries = []
            if allowed:
                entries = select_entries(
                    cands,
                    regime,
                    cfg,
                    held_bases=[p.base for p in positions],
                    blocked_bases=ledger.blocked_bases(t, cfg.max_entries_per_base_per_day),
                    alphai=alphai,
                )
            for e in entries:
                price = stats[e.base].price
                qty = e.clip_eur / price
                positions.append(
                    Position(
                        base=e.base,
                        entry_price=price,
                        quantity=qty,
                        notional_eur=e.clip_eur,
                        opened_ms=t,
                        peak=price,
                        entry_reason=",".join(e.reasons),
                    )
                )
                ledger.note_entry(e.base, t)
            res.decisions.append(
                DecisionLog(
                    t_ms=t,
                    regime_ok=regime.ok,
                    btc_ret=regime.btc_ret,
                    breadth=round(regime.breadth, 3),
                    reasons=regime.reasons,
                    candidates=[c.base for c in cands[:6]],
                    entries=[f"{e.base}@{e.clip_eur:.0f}" for e in entries],
                    risk_block="" if allowed else why,
                )
            )
        t += BAR_MS
    # Mark open positions at the last close inside the window.
    for pos in positions:
        rows = [r for r in (candles_by_base.get(pos.base) or []) if int(r[0]) < end_ms]
        last = rows[-1] if rows else None
        px = float(last[4]) if last else pos.entry_price
        res.open_mtm.append(
            {
                "base": pos.base,
                "opened": _fmt(pos.opened_ms),
                "entry_price": pos.entry_price,
                "mark": px,
                "gross_return": round(pos.gross_return(px), 4),
                "peak_return": round(pos.peak / pos.entry_price - 1.0, 4),
                "net_eur": round(net_pnl_eur(pos, px, None, cfg), 2),
            }
        )
    return res


def walk_forward(
    candles_by_base: Mapping[str, Sequence[Candle]],
    cfg: DeskConfig,
    *,
    start_ms: int,
    end_ms: int,
    window_days: int = 14,
    alphai: AlphaIView | None = None,
) -> list[BacktestResult]:
    """Independent consecutive windows (positions flat at each boundary)."""
    out: list[BacktestResult] = []
    step = window_days * 86_400_000
    cursor = start_ms
    while cursor < end_ms:
        w_end = min(end_ms, cursor + step)
        out.append(simulate(candles_by_base, cfg, start_ms=cursor, end_ms=w_end, alphai=alphai))
        cursor = w_end
    return out

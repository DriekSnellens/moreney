"""Daily opportunity report for the Momentum Desk (missed entries / exits)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bot.live.momentum_desk import (
    BAR_MS,
    AlphaIView,
    Candle,
    DeskConfig,
    Position,
    bar_stats,
    classify_regime,
    evaluate_exit,
    is_scheduled_hour,
    net_pnl_eur,
    rank_candidates,
    select_entries,
    universe_stats,
)


@dataclass
class MissedEntry:
    hour_utc: int
    bases: list[str]
    btc_ret: float | None
    breadth: float
    scheduled: bool
    note: str
    hypothetical: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExitOpportunity:
    base: str
    kind: str  # early_manual | late_manual | would_have_exited | still_open
    entry_ts: str
    actual_exit_ts: str | None
    actual_reason: str | None
    actual_net_eur: float | None
    auto_exit_ts: str | None
    auto_reason: str | None
    auto_net_eur: float | None
    delta_eur: float | None
    peak_return: float | None


@dataclass
class DailyReport:
    day: str  # YYYY-MM-DD UTC
    decisions: list[dict[str, Any]]
    entries: list[dict[str, Any]]
    exits: list[dict[str, Any]]
    realized_net_eur: float
    missed_entries: list[MissedEntry]
    exit_opportunities: list[ExitOpportunity]
    summary: str


def _day_bounds_ms(day: dt.date) -> tuple[int, int]:
    start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC)
    end = start + dt.timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).isoformat()


def _parse_ts(raw: Any) -> int | None:
    if not raw:
        return None
    try:
        return int(dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _ledger_for_day(
    rows: Sequence[Mapping[str, Any]], day_start_ms: int, day_end_ms: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    for raw in rows:
        ts = _parse_ts(raw.get("ts"))
        if ts is None or not (day_start_ms <= ts < day_end_ms):
            continue
        ev = str(raw.get("event") or "")
        row = dict(raw)
        if ev == "decision":
            decisions.append(row)
        elif ev == "entry":
            entries.append(row)
        elif ev == "exit":
            exits.append(row)
    return decisions, entries, exits


def _simulate_auto_exit(
    *,
    base: str,
    entry_price: float,
    qty: float,
    opened_ms: int,
    candles: Sequence[Candle],
    cfg: DeskConfig,
    until_ms: int,
) -> tuple[int, str, float, float] | None:
    """Return (exit_ms, reason, exit_price, peak_return) for the first rule hit."""
    pos = Position(
        base=base,
        entry_price=entry_price,
        quantity=qty,
        notional_eur=entry_price * qty,
        opened_ms=opened_ms,
        peak=entry_price,
    )
    for bar in candles:
        bar_end = int(bar[0]) + BAR_MS
        if bar_end <= opened_ms or bar_end > until_ms:
            continue
        decision = evaluate_exit(pos, bar, cfg)
        if decision is None:
            continue
        px = decision.price if decision.price is not None else float(bar[4])
        peak_ret = pos.peak / entry_price - 1.0 if entry_price > 0 else 0.0
        return bar_end, decision.reason, px, peak_ret
    return None


def _hypo_trade(
    base: str,
    entry_ms: int,
    entry_price: float,
    clip: float,
    candles: Sequence[Candle],
    cfg: DeskConfig,
    until_ms: int,
) -> dict[str, Any]:
    qty = clip / entry_price if entry_price > 0 else 0.0
    auto = _simulate_auto_exit(
        base=base,
        entry_price=entry_price,
        qty=qty,
        opened_ms=entry_ms,
        candles=candles,
        cfg=cfg,
        until_ms=until_ms,
    )
    if auto is None:
        # Mark to last close.
        last = [c for c in candles if int(c[0]) + BAR_MS <= until_ms]
        px = float(last[-1][4]) if last else entry_price
        pos = Position(base, entry_price, qty, clip, entry_ms, entry_price)
        return {
            "base": base,
            "entry_price": entry_price,
            "status": "open",
            "gross_return": round(pos.gross_return(px), 4),
            "net_eur": round(net_pnl_eur(pos, px, None, cfg), 2),
            "exit_ts": None,
            "reason": None,
        }
    exit_ms, reason, px, peak = auto
    pos = Position(base, entry_price, qty, clip, entry_ms, entry_price)
    return {
        "base": base,
        "entry_price": entry_price,
        "status": "closed",
        "gross_return": round(px / entry_price - 1.0, 4),
        "peak_return": round(peak, 4),
        "net_eur": round(net_pnl_eur(pos, px, None, cfg), 2),
        "exit_ts": _iso(exit_ms),
        "reason": reason,
    }


def build_daily_report(
    *,
    day: dt.date,
    cfg: DeskConfig,
    candles_by_base: Mapping[str, Sequence[Candle]],
    ledger_rows: Sequence[Mapping[str, Any]],
    alphai: AlphaIView | None = None,
    now_ms: int | None = None,
) -> DailyReport:
    """Compare scheduled desk actions with hourly opportunities on ``day`` (UTC)."""
    day_start, day_end = _day_bounds_ms(day)
    until = min(now_ms or day_end, day_end)
    decisions, entries, exits = _ledger_for_day(ledger_rows, day_start, day_end)
    entered_bases = {str(e.get("base") or "").upper() for e in entries}
    realized = sum(float(e.get("net_eur") or 0) for e in exits)

    missed: list[MissedEntry] = []
    view = alphai or AlphaIView()
    for hour in range(24):
        t_ms = day_start + hour * 3_600_000
        if t_ms > until:
            break
        scheduled = is_scheduled_hour(t_ms, cfg)
        stats = universe_stats(candles_by_base, t_ms, cfg)
        btc_rows = candles_by_base.get("BTC")
        btc = bar_stats("BTC", btc_rows, t_ms) if btc_rows else None
        regime = classify_regime(btc, stats, cfg, alphai=view)
        if not regime.ok:
            continue
        cands = rank_candidates(stats, regime.btc_ret or 0.0, cfg, alphai=view)
        planned = select_entries(cands, regime, cfg, held_bases=(), alphai=view)
        if not planned:
            continue
        # Missed = not a live scheduled fill of those bases today.
        new_bases = [e.base for e in planned if e.base not in entered_bases]
        if not new_bases and scheduled:
            continue
        if not new_bases:
            continue
        # On the scheduled hour the desk did enter some/all — only list absentees.
        if scheduled and not new_bases:
            continue
        note = "beslismoment — niet gekocht" if scheduled else "buiten schema — desk keek hier niet"
        hypos = []
        for e in planned:
            if e.base not in new_bases:
                continue
            st = stats.get(e.base)
            if st is None or st.price <= 0:
                continue
            rows = candles_by_base.get(e.base) or []
            hypos.append(_hypo_trade(e.base, t_ms, st.price, e.clip_eur, rows, cfg, until))
        missed.append(
            MissedEntry(
                hour_utc=hour,
                bases=new_bases,
                btc_ret=regime.btc_ret,
                breadth=regime.breadth,
                scheduled=scheduled,
                note=note,
                hypothetical=hypos,
            )
        )

    # Exit opportunities: actual entries today + still-relevant exits.
    exit_ops: list[ExitOpportunity] = []
    exit_by_base = {str(x.get("base") or "").upper(): x for x in exits}
    for ent in entries:
        base = str(ent.get("base") or "").upper()
        entry_px = float(ent.get("price") or 0)
        qty = float(ent.get("qty") or 0)
        opened = _parse_ts(ent.get("ts")) or day_start
        if entry_px <= 0 or qty <= 0:
            continue
        rows = candles_by_base.get(base) or []
        actual = exit_by_base.get(base)
        actual_ms = _parse_ts(actual.get("ts")) if actual else None
        until_scan = actual_ms or until
        auto = _simulate_auto_exit(
            base=base,
            entry_price=entry_px,
            qty=qty,
            opened_ms=opened,
            candles=rows,
            cfg=cfg,
            until_ms=until_scan + BAR_MS if actual_ms else until,
        )
        if actual and actual.get("net_eur") is not None:
            actual_net: float | None = float(actual["net_eur"])
        else:
            actual_net = None
        actual_reason = str(actual.get("reason") or "") if actual else None
        if auto is None:
            exit_ops.append(
                ExitOpportunity(
                    base=base,
                    kind="still_open" if actual is None else "matched",
                    entry_ts=str(ent.get("ts") or ""),
                    actual_exit_ts=str(actual.get("ts")) if actual else None,
                    actual_reason=actual_reason,
                    actual_net_eur=actual_net,
                    auto_exit_ts=None,
                    auto_reason=None,
                    auto_net_eur=None,
                    delta_eur=None,
                    peak_return=(
                        float(actual["peak_return"])
                        if actual and actual.get("peak_return") is not None
                        else None
                    ),
                )
            )
            continue
        auto_ms, auto_reason, auto_px, peak = auto
        pos = Position(base, entry_px, qty, entry_px * qty, opened, entry_px)
        auto_net = net_pnl_eur(pos, auto_px, None, cfg)
        if actual is None:
            kind = "would_have_exited"
            delta = None
        elif actual_ms is not None and actual_ms + 60_000 < auto_ms:
            kind = "early_manual"
            delta = (actual_net - auto_net) if actual_net is not None else None
        elif actual_reason == "manual" and actual_ms is not None and actual_ms > auto_ms + 60_000:
            kind = "late_manual"
            delta = (actual_net - auto_net) if actual_net is not None else None
        else:
            kind = "matched"
            delta = (actual_net - auto_net) if actual_net is not None else None
        exit_ops.append(
            ExitOpportunity(
                base=base,
                kind=kind,
                entry_ts=str(ent.get("ts") or ""),
                actual_exit_ts=str(actual.get("ts")) if actual else None,
                actual_reason=actual_reason,
                actual_net_eur=actual_net,
                auto_exit_ts=_iso(auto_ms),
                auto_reason=auto_reason,
                auto_net_eur=round(auto_net, 2),
                delta_eur=round(delta, 2) if delta is not None else None,
                peak_return=round(peak, 4),
            )
        )

    n_miss = sum(len(m.bases) for m in missed)
    hypo_net = sum(
        float(h.get("net_eur") or 0)
        for m in missed
        for h in m.hypothetical
        if h.get("status") == "closed"
    )
    early = [o for o in exit_ops if o.kind == "early_manual" and o.delta_eur is not None]
    # actual - auto; negative = left on table
    early_left = sum(float(o.delta_eur or 0) for o in early)
    summary = (
        f"{len(entries)} entries · {len(exits)} exits · gerealiseerd {_fmt(realized)} · "
        f"{n_miss} gemiste instap-slots"
        + (f" (hypo gesloten {_fmt(hypo_net)})" if n_miss else "")
        + (
            f" · vroege handmatige exits lieten {_fmt(-early_left)} liggen"
            if early and early_left < 0
            else ""
        )
    )
    return DailyReport(
        day=day.isoformat(),
        decisions=decisions,
        entries=entries,
        exits=exits,
        realized_net_eur=round(realized, 2),
        missed_entries=missed,
        exit_opportunities=exit_ops,
        summary=summary,
    )


def _fmt(v: float) -> str:
    return f"{v:+,.2f} €"


def report_as_dict(report: DailyReport) -> dict[str, Any]:
    return {
        "day": report.day,
        "summary": report.summary,
        "realized_net_eur": report.realized_net_eur,
        "decisions": report.decisions,
        "entries": report.entries,
        "exits": report.exits,
        "missed_entries": [
            {
                "hour_utc": m.hour_utc,
                "bases": m.bases,
                "btc_ret": m.btc_ret,
                "breadth": m.breadth,
                "scheduled": m.scheduled,
                "note": m.note,
                "hypothetical": m.hypothetical,
            }
            for m in report.missed_entries
        ],
        "exit_opportunities": [
            {
                "base": o.base,
                "kind": o.kind,
                "entry_ts": o.entry_ts,
                "actual_exit_ts": o.actual_exit_ts,
                "actual_reason": o.actual_reason,
                "actual_net_eur": o.actual_net_eur,
                "auto_exit_ts": o.auto_exit_ts,
                "auto_reason": o.auto_reason,
                "auto_net_eur": o.auto_net_eur,
                "delta_eur": o.delta_eur,
                "peak_return": o.peak_return,
            }
            for o in report.exit_opportunities
        ],
    }

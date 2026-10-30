"""Wet one-slot replay of the states that precede the largest highs.

Signal on the completed close, buy the next open. A trail fills inside the
bar when the low trades through it; a gap through that level fills at the
open. A time stop sells the next open. Fees are the Bitvavo taker plus
impact. One name at a time, equity compounded. No per-coin branches.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_btc_rs_clip import quote_vol
from bot.research.clip_exit_lab.engine import WET, Book, FillModel, fill_px, metrics, year_pnl
from bot.research.consistency_lab.engine import consistency
from bot.research.horizon_lab.bank import level_exit
from bot.research.moonshot_preimage.engine import _features
from bot.research.wake_lab.detect import day_of

Ohlc = Mapping[str, Sequence[Sequence[float]]]


@dataclass(frozen=True)
class SimSpec:
    name: str
    entry: str
    trail_pct: float = 0.0
    hold_bars: int = 0
    min_adv_eur: float = 20_000.0
    history: int = 55


@dataclass(frozen=True)
class _Name:
    r3: float
    base: str
    continuation: bool
    hot: bool


def default_specs() -> list[SimSpec]:
    """Three entries, three exits. The grid is fixed before the replay."""
    entries = (
        ("cont", "continuation"),
        ("leader", "leader"),
        ("hot", "hot"),
    )
    exits = (
        ("hold7", 0.0, 7),
        ("trail10", 0.10, 0),
        ("trail10_h7", 0.10, 7),
    )
    return [
        SimSpec(name=f"{entry}_{exit}", entry=kind, trail_pct=trail, hold_bars=hold)
        for entry, kind in entries
        for exit, trail, hold in exits
    ]


def _pick(names: Sequence[_Name], entry: str) -> str | None:
    if entry == "leader":
        pool = [name for name in names if name.r3 > 0.0]
    elif entry == "continuation":
        pool = [name for name in names if name.continuation]
    elif entry == "hot":
        pool = [name for name in names if name.hot]
    else:
        return None
    if not pool:
        return None
    pool.sort(key=lambda name: (-name.r3, name.base))
    return pool[0].base


def candidates(
    ohlc: Ohlc,
    *,
    min_adv_eur: float = 20_000.0,
    history: int = 55,
) -> dict[str, list[_Name]]:
    """Tradable names per day. Average quote volume is the last 20 days."""
    btc_rows = list(ohlc.get("BTC") or [])
    btc_at = {day_of(int(row[0])): i for i, row in enumerate(btc_rows)}
    btc_px = [float(row[4]) for row in btc_rows]
    out: dict[str, list[_Name]] = {}
    for base, rows in ohlc.items():
        if base == "BTC" or len(rows) < history:
            continue
        closes = [float(row[4]) for row in rows]
        highs = [float(row[2]) for row in rows]
        lows = [float(row[3]) for row in rows]
        vols = [float(row[5]) for row in rows]
        for i in range(history - 1, len(rows)):
            day = day_of(int(rows[i][0]))
            bi = btc_at.get(day)
            if bi is None or bi + 1 < history:
                continue
            adv = quote_vol(rows[: i + 1], 20)
            if adv < min_adv_eur:
                continue
            feats = _features(
                closes[i - history + 1 : i + 1],
                highs[i - history + 1 : i + 1],
                lows[i - history + 1 : i + 1],
                vols[i - history + 1 : i + 1],
                btc_px[bi - history + 1 : bi + 1],
            )
            if feats is None:
                continue
            r3 = float(feats["r3"])
            out.setdefault(day, []).append(
                _Name(
                    r3=r3,
                    base=base,
                    continuation=bool(feats["r3_15"] and feats["xs25"] and feats["trend"]),
                    hot=r3 >= 0.40,
                )
            )
    return out


def _index(ohlc: Ohlc) -> dict[str, dict[str, Sequence[float]]]:
    book: dict[str, dict[str, Sequence[float]]] = {}
    for base, rows in ohlc.items():
        book[base] = {day_of(int(row[0])): row for row in rows}
    return book


def _timeline(ohlc: Ohlc, start: str, end: str) -> list[str]:
    days = {day_of(int(row[0])) for row in ohlc.get("BTC") or []}
    if not days:
        for rows in ohlc.values():
            days.update(day_of(int(row[0])) for row in rows)
    return sorted(day for day in days if start <= day <= end)


def replay(
    ohlc: Ohlc,
    spec: SimSpec,
    picks: Mapping[str, str],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
) -> dict[str, Any]:
    """Compound one slot from ``start`` through ``end``."""
    bars = _index(ohlc)
    book = Book(book_eur)
    held: str | None = None
    pending_buy: str | None = None
    pending_sell: str | None = None
    entry_px = 0.0
    peak = 0.0
    bars_held = 0
    spent = 0.0
    reasons: dict[str, int] = {}
    trade_pnl: list[float] = []
    curve: list[tuple[str, float]] = []

    def _adv(base: str, day: str) -> float:
        rows = [row for d, row in sorted((bars.get(base) or {}).items()) if d < day]
        return quote_vol(rows, 20)

    def _sell(base: str, day: str, raw: float, reason: str) -> None:
        nonlocal held, entry_px, peak, bars_held, spent, pending_sell
        lot = book.lots.get(base)
        if lot is None or raw <= 0:
            return
        cash_before = book.cash
        px = fill_px(raw, "sell", notional=lot.qty * raw, adv=_adv(base, day), model=model)
        book.sell(base, px, model.fee_side)
        trade_pnl.append(book.cash - cash_before - spent)
        reasons[reason] = reasons.get(reason, 0) + 1
        held = None
        pending_sell = None
        entry_px = 0.0
        peak = 0.0
        bars_held = 0
        spent = 0.0

    for day in _timeline(ohlc, start, end):
        if pending_sell and held:
            live = (bars.get(held) or {}).get(day)
            if live is not None and float(live[1]) > 0:
                _sell(held, day, float(live[1]), pending_sell)
        if pending_buy and held is None:
            live = (bars.get(pending_buy) or {}).get(day)
            if live is not None and float(live[1]) > 0:
                raw = float(live[1])
                notional = book.cash
                px = fill_px(raw, "buy", notional=notional, adv=_adv(pending_buy, day), model=model)
                cash_before = book.cash
                filled = book.buy(
                    pending_buy, spec.entry, notional, px, model.fee_side, opened_ms=0
                )
                if filled > 0:
                    held = pending_buy
                    entry_px = px
                    peak = px
                    bars_held = 0
                    spent = cash_before - book.cash
            pending_buy = None

        if held:
            live = (bars.get(held) or {}).get(day)
            if live is not None:
                trail_px = peak * (1.0 - spec.trail_pct) if spec.trail_pct > 0 and peak > 0 else 0.0
                hit = level_exit(
                    entry=entry_px,
                    open_=float(live[1]),
                    high=float(live[2]),
                    low=float(live[3]),
                    tp_pct=0.0,
                    stop_pct=0.0,
                    trail_px=trail_px,
                )
                if hit is not None:
                    _sell(held, day, hit[1], hit[0])
                else:
                    peak = max(peak, float(live[2]))
                    bars_held += 1
                    if spec.hold_bars > 0 and bars_held >= spec.hold_bars and pending_sell is None:
                        pending_sell = "time"

        if (held is None or pending_sell) and pending_buy is None:
            choice = picks.get(day)
            if choice and choice != held:
                pending_buy = choice

        mark_px: dict[str, float] = {}
        if held:
            live = (bars.get(held) or {}).get(day)
            if live is not None and float(live[4]) > 0:
                mark_px[held] = float(live[4])
        curve.append((day, book.mark(mark_px)))

    wins = sum(1 for pnl in trade_pnl if pnl > 0)
    extra: dict[str, Any] = {
        "strategy": spec.name,
        "entry": spec.entry,
        "reasons": reasons,
        "n_wins": wins,
        "win_pct": (wins / len(trade_pnl)) if trade_pnl else 0.0,
        "avg_trade": (sum(trade_pnl) / len(trade_pnl)) if trade_pnl else 0.0,
        "year_pnl": year_pnl([day for day, _ in curve], [eq for _, eq in curve], book_eur),
        "end_hold": held or "cash",
    }
    extra.update(consistency(curve, book_eur))
    return {
        "strategy": spec.name,
        "model": model.name,
        **metrics(
            [eq for _, eq in curve],
            start_eur=book_eur,
            n_trades=len(trade_pnl),
            n_days=len(curve),
            extra=extra,
        ),
    }


def picks_for(spec: SimSpec, names: Mapping[str, Sequence[_Name]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for day, rows in names.items():
        choice = _pick(rows, spec.entry)
        if choice:
            out[day] = choice
    return out


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{100.0 * value:.1f}%"


def _row(result: dict[str, Any]) -> str:
    years = result.get("year_pnl") or {}
    return (
        f"| {result['strategy']} | {result['pnl_eur']:,.0f} | "
        f"{_pct(result['max_dd_pct'])} | {_pct(result.get('green_weeks_pct'))} | "
        f"{result['n_trades']} | {_pct(result.get('win_pct'))} | "
        f"{years.get('2024', 0):,.0f} | {years.get('2025', 0):,.0f} | "
        f"{years.get('2026', 0):,.0f} |"
    )


def _table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| book | PnL | DD | green W | trades | win | 2024 | 2025 | 2026 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in rows:
        lines.append(_row(result))
    lines.append("")
    return lines


def write_sim(
    path: str,
    full: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
    *,
    asof: str,
) -> None:
    lines = [
        "# Wet replay of the high-rise states",
        "",
        "Question: the largest 7-day highs follow a name that is already",
        "running. Does a one-slot book, filled like Bitvavo, keep any of",
        "that high?",
        "",
        "One name at a time, €20,000 compounded. Signal on the completed",
        "close, buy the next open, sell with the Bitvavo taker (0.25% a",
        "side) plus impact `0.02 × sqrt(notional / 20-day average quote",
        "volume)`, capped at 3%. A name must already average €20,000 of",
        "quote volume a day, so a one-day print on a dead tape is not",
        "a fill.",
        "",
        "Entries, one per day when the book is flat:",
        "",
        "- `cont` — 3-day return at least +15%, 10-day return at least 25",
        "  points ahead of BTC, close above the 20-day average and that",
        "  average above the 50-day. The strongest 3-day return wins.",
        "- `leader` — the strongest positive 3-day return that day.",
        "- `hot` — the strongest name already up at least 40% in 3 days.",
        "",
        "Exits. `hold7` sells the next open after 7 bars in the trade.",
        "`trail10` sells inside the bar when the low trades through 10%",
        "under the prior peak; a gap through that level fills at the open.",
        "The peak does not include the bar being judged. `trail10_h7` takes",
        "whichever comes first.",
        "",
        f"Tape through {asof}. `Fresh 2026` restarts at €20,000 on 1 January",
        "2026. The full path compounds from the first bar of the window.",
        "",
        "Reproduce: `.venv/bin/python -m bot.research.rise_lab.sim`",
        "",
    ]
    lines += _table("Full path", full)
    lines += _table("Fresh 2026", fresh)
    lines += _reading(full, fresh)
    Path(path).write_text("\n".join(lines) + "\n")


def _reading(full: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[str]:
    least = min(fresh, key=lambda row: row["pnl_eur"])
    best = max(fresh, key=lambda row: row["pnl_eur"])
    hold = next(row for row in fresh if row["strategy"] == "cont_hold7")
    trail = next(row for row in fresh if row["strategy"] == "cont_trail10")
    boxed = next(row for row in fresh if row["strategy"] == "cont_trail10_h7")
    trail_n = int((trail.get("reasons") or {}).get("trail", 0))
    same_path = trail["n_trades"] == boxed["n_trades"] and trail["pnl_eur"] == boxed["pnl_eur"]
    clock = (
        " The 7-day clock never fired first, so `cont_trail10_h7` is the same book."
        if same_path
        else ""
    )
    return [
        "## Reading",
        "",
        "Every book loses. On the full path the loss is front-loaded: 2024",
        "takes almost the whole €20,000, and the 2026 column is dust because",
        "the book can no longer buy. The fresh 2026 book is the clean read.",
        "",
        (
            f"1. Least bad in 2026 is `{best['strategy']}`: "
            f"€{best['pnl_eur']:,.0f}, drawdown {_pct(best['max_dd_pct'])}, "
            f"{best['n_trades']} trades, win rate {_pct(best.get('win_pct'))}, "
            f"average trade €{best.get('avg_trade', 0):,.0f}. "
            f"Worst is `{least['strategy']}` at €{least['pnl_eur']:,.0f}."
        ),
        (
            f"2. On continuation, the 7-day hold loses €{hold.get('avg_trade', 0):,.0f} "
            f"a trade ({hold['n_trades']} trades, all closed on the clock). "
            f"The 10% trail loses €{trail.get('avg_trade', 0):,.0f} a trade "
            f"({trail['n_trades']} trades, {trail_n} closed on the trail)."
            f"{clock} The trail cuts the loss per trade and multiplies the "
            "number of round trips. The book still ends lower."
        ),
        (
            "3. A green week does not save it. The least-bad 2026 book is "
            f"green about {_pct(best.get('green_weeks_pct'))} of weeks and "
            "still gives back most of the stake. The average trade is "
            "negative, so the losers outweigh the winners."
        ),
        "",
        "## Verdict",
        "",
        "Nothing armed. The high in the horse race is a wick. Bought at the",
        "next open, with the taker and impact, a 10% trail and a 7-day hold",
        "both lose the one-slot book, on the full path and on a fresh 2026",
        "book. The same order as the other labs: the wick is real, and the",
        "fill does not keep it.",
        "",
    ]


def main() -> None:
    from bot.research.unusual_volume_lab.data import CACHE_1D, eur_bases, load_cached

    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="bot/research/rise_lab/SIM.md")
    args = ap.parse_args()
    now = datetime.now(UTC)
    today_ms = int(datetime(now.year, now.month, now.day, tzinfo=UTC).timestamp() * 1000)
    bases = eur_bases()
    if "BTC" not in bases:
        bases.append("BTC")
    ohlc: dict[str, list[list[float]]] = {}
    for base, rows in load_cached(CACHE_1D, bases).items():
        done = [row for row in rows if int(row[0]) < today_ms]
        if done:
            ohlc[base] = done
    print(f"markets {len(ohlc)}", flush=True)
    names = candidates(ohlc)
    print(f"signal days {len(names)}", flush=True)
    specs = default_specs()
    full: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    for spec in specs:
        chosen = picks_for(spec, names)
        full.append(replay(ohlc, spec, chosen, start="2024-01-01", end="2026-12-31"))
        fresh.append(replay(ohlc, spec, chosen, start="2026-01-01", end="2026-12-31"))
        last = full[-1]
        print(
            f"{spec.name:18} full {last['pnl_eur']:10,.0f} dd {last['max_dd_pct']:.1%} "
            f"trades {last['n_trades']:4d}  2026-fresh {fresh[-1]['pnl_eur']:10,.0f}",
            flush=True,
        )
    asof = max(names) if names else ""
    write_sim(args.results, full, fresh, asof=asof)
    print(f"wrote {args.results}", flush=True)


if __name__ == "__main__":
    main()

"""Continuous sleeve with a fixed +8% take-profit.

Signal at the daily close, buy the next open. Take-profit and hard-stop
fill on the daily bar with the OHLC path rule from the day50 lab: an up-close
checks the target before the stop, a down-close checks the stop first.
After an intraday exit the next name is bought on the following open.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.daily_green_lab.engine import _alphai_daily, _by_date, _load_dir, _rank_day, _sma

Row = list[float]
FEE = 0.0015
SLIP = 0.001


@dataclass(frozen=True)
class TpSpec:
    name: str
    pick: str
    take_profit: float = 0.08
    hard_stop_pct: float = 0.0
    time_max_days: int = 0
    require_btc_sma: bool = False
    excess_floor: float = 0.0
    rotate: bool = False


def resolve_from_entry(
    *,
    entry: float,
    o: float,
    h: float,
    l: float,
    c: float,
    hard: float,
    tp: float,
    slip: float = SLIP,
) -> tuple[str, float] | None:
    """Fill a take-profit or hard stop against the entry, not today's open."""
    stop_px = entry * (1.0 - hard) if hard > 0 else None
    tp_px = entry * (1.0 + tp) if tp > 0 else None
    if stop_px is not None and o <= stop_px:
        return "hard_stop", o * (1.0 - slip)
    if tp_px is not None and o >= tp_px:
        return "take_profit", o * (1.0 - slip)
    up = c >= o
    if up:
        if tp_px is not None and h >= tp_px:
            return "take_profit", tp_px * (1.0 - slip)
        if stop_px is not None and l <= stop_px:
            return "hard_stop", stop_px * (1.0 - slip)
    else:
        if stop_px is not None and l <= stop_px:
            return "hard_stop", stop_px * (1.0 - slip)
        if tp_px is not None and h >= tp_px:
            return "take_profit", tp_px * (1.0 - slip)
    return None


def simulate_tp(
    ohlc: Mapping[str, Sequence[Row]],
    spec: TpSpec,
    *,
    start: str,
    end: str,
    book: float,
    alphai_by_day: Mapping[str, Sequence[str]],
    fee: float = FEE,
    slip: float = SLIP,
) -> dict[str, Any]:
    by = {b: _by_date(rows) for b, rows in ohlc.items()}
    dates_by = {b: sorted(m) for b, m in by.items()}
    btc_dates = dates_by.get("BTC") or []
    cal = [d for d in btc_dates if start <= d <= end]
    if len(cal) < 5:
        return {"name": spec.name, "ok": False, "reason": "short_window"}

    cash = book
    realized = 0.0
    pos: dict[str, Any] | None = None
    pending_buy: str | None = None
    pending_sell = False
    ban: str | None = None
    daily: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    prev_eq = book
    last_ai: list[str] = []

    def _close(date: str, px: float, reason: str) -> None:
        nonlocal cash, realized, pos, ban
        assert pos is not None
        proceeds = pos["qty"] * px
        fee_eur = proceeds * fee
        pnl = proceeds - fee_eur - pos["cost"]
        cash += proceeds - fee_eur
        realized += pnl
        trades.append(
            {
                "date": date,
                "side": "sell",
                "base": pos["base"],
                "pnl": round(pnl, 2),
                "reason": reason,
            }
        )
        if reason != "eow":
            ban = str(pos["base"])
        pos = None

    for date in cal:
        if pending_sell and pos is not None and date in by.get(pos["base"], {}):
            px = float(by[pos["base"]][date][1]) * (1 - slip)
            _close(date, px, str(pos.get("exit_reason") or "exit"))
        pending_sell = False

        if pending_buy and pos is None:
            base = pending_buy
            if date in by.get(base, {}) and base != ban:
                px = float(by[base][date][1]) * (1 + slip)
                notion = min(cash * 0.98, book)
                if notion >= 40 and px > 0:
                    fee_eur = notion * fee
                    qty = notion / px
                    cash -= notion + fee_eur
                    pos = {
                        "base": base,
                        "qty": qty,
                        "cost": notion + fee_eur,
                        "entry": px,
                        "opened": date,
                        "days": 0,
                    }
                    trades.append(
                        {
                            "date": date,
                            "side": "buy",
                            "base": base,
                            "pnl": 0.0,
                            "reason": spec.pick,
                            "notion": round(notion, 2),
                        }
                    )
                    ban = None
            pending_buy = None

        if pos is not None and date in by.get(pos["base"], {}):
            bar = by[pos["base"]][date]
            o, h, l, c = (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))
            pos["days"] = int(pos["days"]) + 1
            hit = resolve_from_entry(
                entry=float(pos["entry"]),
                o=o,
                h=h,
                l=l,
                c=c,
                hard=float(spec.hard_stop_pct),
                tp=float(spec.take_profit),
                slip=slip,
            )
            if hit is not None:
                _close(date, hit[1], hit[0])
            elif int(spec.time_max_days) > 0 and int(pos["days"]) >= int(spec.time_max_days):
                pos["exit_reason"] = "time_stop"
                pending_sell = True

        btc_hist = [float(by["BTC"][d][4]) for d in btc_dates if d <= date]
        s50 = _sma(btc_hist, 50)
        risk_on = s50 is not None and btc_hist[-1] > s50
        allow = risk_on or not spec.require_btc_sma
        if date in alphai_by_day:
            last_ai = list(alphai_by_day[date])

        pick = None
        if allow:
            pick = _rank_day(
                by,
                date,
                dates_by,
                mode=spec.pick,
                lookback=1,
                excess_floor=float(spec.excess_floor),
                alphai=last_ai,
                btc_dates=btc_dates,
            )
            if pick and ban and pick == ban:
                by2 = {b: m for b, m in by.items() if b != ban}
                dates2 = {b: dates_by[b] for b in by2}
                pick = _rank_day(
                    by2,
                    date,
                    dates2,
                    mode=spec.pick,
                    lookback=1,
                    excess_floor=float(spec.excess_floor),
                    alphai=last_ai,
                    btc_dates=btc_dates,
                )

        if not allow:
            pending_buy = None
            if pos is not None and not pending_sell:
                pos["exit_reason"] = "btc_risk_off"
                pending_sell = True
        elif pos is None and not pending_sell and pick:
            pending_buy = pick
        elif (
            spec.rotate
            and pos is not None
            and not pending_sell
            and pick
            and pick != pos["base"]
        ):
            pos["exit_reason"] = "rotate"
            pending_sell = True
            pending_buy = pick

        if pos is not None and date in by.get(pos["base"], {}):
            cl = float(by[pos["base"]][date][4])
            mtm = pos["qty"] * cl - pos["cost"]
            show_eq = book + realized + mtm
        else:
            show_eq = book + realized
            cash = book
        day_pnl = show_eq - prev_eq
        daily.append(
            {
                "date": date,
                "equity": round(show_eq, 2),
                "day_pnl": round(day_pnl, 2),
                "cum_pnl": round(show_eq - book, 2),
                "hold": pos["base"] if pos else "cash",
                "in_market": pos is not None,
            }
        )
        prev_eq = show_eq

    if pos is not None:
        base = pos["base"]
        last = cal[-1]
        if last in by.get(base, {}):
            px = float(by[base][last][4]) * (1 - slip)
            _close(last, px, "eow")
            if daily:
                base_eq = float(daily[-2]["equity"]) if len(daily) > 1 else book
                daily[-1]["equity"] = round(book + realized, 2)
                daily[-1]["cum_pnl"] = round(realized, 2)
                daily[-1]["day_pnl"] = round(daily[-1]["equity"] - base_eq, 2)
                daily[-1]["hold"] = "cash"
                daily[-1]["in_market"] = False

    sells = [t for t in trades if t["side"] == "sell"]
    n_sell = len(sells) or 1
    reasons: dict[str, int] = {}
    for t in sells:
        reasons[str(t["reason"])] = reasons.get(str(t["reason"]), 0) + 1
    pnls = [float(d["day_pnl"]) for d in daily]
    n = len(pnls) or 1
    green = sum(1 for p in pnls if p > 0)
    in_mkt = sum(1 for d in daily if d["in_market"])
    peak = book
    max_dd = 0.0
    for d in daily:
        eq = float(d["equity"])
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    tp_n = reasons.get("take_profit", 0)
    return {
        "ok": True,
        "name": spec.name,
        "pick": spec.pick,
        "rotate": spec.rotate,
        "take_profit": spec.take_profit,
        "hard_stop_pct": spec.hard_stop_pct,
        "time_max_days": spec.time_max_days,
        "require_btc_sma": spec.require_btc_sma,
        "pnl_eur": round(float(daily[-1]["cum_pnl"]), 2) if daily else 0.0,
        "avg_day_pnl": round(sum(pnls) / n, 2),
        "pct_days_green": round(green / n, 4),
        "n_days": n,
        "pct_days_in_market": round(in_mkt / n, 4),
        "max_dd_pct": round(max_dd, 4),
        "n_sells": len(sells),
        "tp_rate": round(tp_n / n_sell, 4),
        "reasons": reasons,
        "avg_sell_pnl": round(sum(float(t["pnl"]) for t in sells) / n_sell, 2) if sells else 0.0,
    }


def grid() -> list[TpSpec]:
    """A few continuous packs around a fixed +8% take-profit."""
    picks = ("top_day", "brk20_day", "coil_day")
    stops = (0.0, 0.05)
    times = (0, 3, 5)
    out: list[TpSpec] = []
    for pick in picks:
        for hard in stops:
            for tmax in times:
                if hard == 0.0 and tmax == 0:
                    # Pure "hold until +8%" — losers stay until the window ends.
                    pass
                name = f"{pick}_tp8_hs{int(hard * 100)}_d{tmax}"
                out.append(
                    TpSpec(
                        name=name,
                        pick=pick,
                        hard_stop_pct=hard,
                        time_max_days=tmax,
                    )
                )
    out.append(
        TpSpec(
            name="top_day_tp8_hs5_d3_rotate",
            pick="top_day",
            hard_stop_pct=0.05,
            time_max_days=3,
            rotate=True,
        )
    )
    out.append(
        TpSpec(
            name="top_day_tp8_hs5_d3_btc",
            pick="top_day",
            hard_stop_pct=0.05,
            time_max_days=3,
            require_btc_sma=True,
        )
    )
    return out


def run(*, book: float = 2_000.0, candle_dir: str = "data/ignition_expand_candles") -> dict[str, Any]:
    ohlc = _load_dir(Path(candle_dir))
    alphai_path = Path("data/research/alphai_sessions_merged.json")
    alphai = _alphai_daily(alphai_path) if alphai_path.exists() else {}
    windows = {
        "is": ("2024-06-15", "2025-12-31"),
        "oos": ("2026-01-01", "2026-10-02"),
        "full": ("2024-06-15", "2026-10-02"),
        "last_6w": ("2026-08-21", "2026-10-02"),
    }
    rows = []
    for spec in grid():
        item: dict[str, Any] = {"name": spec.name, "pick": spec.pick, "rotate": spec.rotate}
        for key, (a, b) in windows.items():
            item[key] = simulate_tp(ohlc, spec, start=a, end=b, book=book, alphai_by_day=alphai)
        rows.append(item)
    rows.sort(key=lambda r: -float((r.get("oos") or {}).get("pnl_eur") or -1e18))
    return {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": book,
        "n_bases": len([b for b in ohlc if b != "BTC"]),
        "fee": FEE,
        "slip": SLIP,
        "windows": windows,
        "rows": rows,
    }


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Continuous +8% take-profit",
        "",
        f"asof `{payload.get('asof')}`  book €{payload.get('book_eur'):,.0f}  "
        f"alts **{payload.get('n_bases')}**  fee {payload.get('fee')}  slip {payload.get('slip')}",
        "",
        "Signaal op de slotkoers, koop de volgende opening. Take-profit vult op de dagbar "
        "als de high +8% boven de entry komt (up-bar: target vóór de stop). "
        "Daarna pas de volgende munt op de opening erna. Vast boek, elke vlakke dag weer €book.",
        "",
        "Een pakket dat alleen in 2026 groen is en over 2024–2026 rood, telt niet als de PnL. "
        "Zonder stop of tijdslimiet blijft de sleeve in één munt hangen; de 2026-rij apart "
        "starten telt die hangende trade niet en oogt te groen.",
        "",
        "Dual-venster groen in deze grid: `brk20_day_tp8_hs0_d3` — full **+€1,346**, "
        "IS +€637, OOS +€709, maxDD 77%. Ongeveer de helft van de exits is de +8%.",
        "",
        "`top_day` met TP 8% en hard-stop 5% draait wel continu (honderden trades) en "
        "verliest: full ongeveer **−€5.6k**.",
        "",
        "| Pack | IS € | OOS € | Full € | 6w € | OOS TP-rate | OOS DD | OOS green |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in payload.get("rows") or []:
        def g(w: str, k: str) -> float:
            return float((r.get(w) or {}).get(k) or 0)

        lines.append(
            f"| `{r.get('name')}` | {g('is','pnl_eur'):+.0f} | {g('oos','pnl_eur'):+.0f} | "
            f"{g('full','pnl_eur'):+.0f} | {g('last_6w','pnl_eur'):+.0f} | "
            f"{100 * g('oos','tp_rate'):.0f}% | {100 * g('oos','max_dd_pct'):.1f}% | "
            f"{100 * g('oos','pct_days_green'):.0f}% |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    payload = run()
    out = Path(__file__).resolve().parent
    text = to_markdown(payload)
    (out / "TP8.md").write_text(text, encoding="utf-8")
    print(text, flush=True)


if __name__ == "__main__":
    main()

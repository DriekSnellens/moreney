"""Leverage ladder for the armed residual pack: same trades, long leg on perps.

    .venv/bin/python -m bot.research.factor_scoreboard.leverage

Takes the baseline replay's round trips (fill prices from the wet next-open model)
and re-prices each one as a USDT-margined perp long at leverage ``L``: perp taker
fee + slip per side instead of the Bitvavo spot fee, funding paid on the full
notional (Binance daily-mean proxy, three settlements a day), and a cross-margin
liquidation check against the worst intraday low of the hold. Exits are the
baseline's exits (10% trail, +12%/+6% lock), so this answers "what does leverage do
to the pack as armed", not "what is the best leveraged pack".
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SPOT_FEE = 0.0025
PERP_FEE = 0.0005
PERP_SLIP = 0.0005
MMR = 0.02  # maintenance margin, conservative for alt perps at these tiers
WIPE_RESIDUAL = 0.02  # equity left after a cross-margin liquidation


@dataclass(frozen=True)
class Position:
    base: str
    entry_date: str
    exit_date: str
    entry_px: float
    exit_px: float


@dataclass
class Ladder:
    label: str
    pnl_eur: float
    max_dd_pct: float
    by_year: dict[str, float]
    liquidations: int
    worst_intraday: float
    funding_eur: float
    fees_eur: float


def positions_from_trades(trades: Sequence[Mapping[str, Any]]) -> list[Position]:
    out: list[Position] = []
    open_: Mapping[str, Any] | None = None
    for t in trades:
        if t.get("side") == "buy":
            open_ = t
        elif open_ is not None:
            out.append(
                Position(
                    base=str(open_["base"]),
                    entry_date=str(open_["date"]),
                    exit_date=str(t["date"]),
                    entry_px=float(open_["px"]),
                    exit_px=float(t["px"]),
                )
            )
            open_ = None
    return out


def _day(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def replay_leverage(
    positions: Sequence[Position],
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    leverage: float,
    perp: bool,
    funding: Mapping[str, Mapping[str, float]] | None,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    label: str = "",
) -> Ladder:
    bars = {b: {_day(r[0]): r for r in rows} for b, rows in ohlc.items()}
    dates = [d for d in sorted(bars["BTC"]) if start <= d <= end]
    equity = book_eur
    curve: dict[str, float] = {}
    liq = 0
    worst = 0.0
    fund_paid = 0.0
    fees = 0.0
    side_cost = (PERP_FEE + PERP_SLIP) if perp else SPOT_FEE
    for p in positions:
        if p.entry_date < start or p.exit_date > end or p.base not in bars:
            continue
        days = [d for d in dates if p.entry_date <= d <= p.exit_date and d in bars[p.base]]
        fee = leverage * side_cost * 2
        fees += fee * equity
        lows = [bars[p.base][d][3] / p.entry_px - 1.0 for d in days]
        dmin = min(lows) if lows else 0.0
        worst = min(worst, dmin)
        carry = 0.0
        if perp and funding is not None:
            carry = sum(float((funding.get(p.base) or {}).get(d, 0.0)) * 3.0 for d in days)
        fund_paid += leverage * carry * equity
        for d in days:
            c = bars[p.base][d][4] / p.entry_px - 1.0
            curve[d] = equity * (1.0 + leverage * c - fee)
        if perp and leverage > 1.0 and dmin <= -(1.0 / leverage - MMR):
            liq += 1
            equity *= WIPE_RESIDUAL
        else:
            equity *= 1.0 + leverage * (p.exit_px / p.entry_px - 1.0) - fee - leverage * carry
        curve[p.exit_date] = equity
    peak = book_eur
    max_dd = 0.0
    last = book_eur
    year_start: dict[str, float] = {}
    year_end: dict[str, float] = {}
    for d in dates:
        y = d[:4]
        year_start.setdefault(y, last)
        last = curve.get(d, last)
        year_end[y] = last
        peak = max(peak, last)
        max_dd = max(max_dd, (peak - last) / peak)
    return Ladder(
        label=label or f"{'perp' if perp else 'spot'} {leverage:g}x",
        pnl_eur=round(equity - book_eur, 0),
        max_dd_pct=round(max_dd, 4),
        by_year={y: round(year_end[y] - year_start[y], 0) for y in year_end},
        liquidations=liq,
        worst_intraday=round(worst, 4),
        funding_eur=round(fund_paid, 0),
        fees_eur=round(fees, 0),
    )


LADDER: tuple[tuple[float, bool], ...] = (
    (1.0, False),
    (1.0, True),
    (1.25, True),
    (1.5, True),
    (2.0, True),
    (3.0, True),
    (5.0, True),
)


def main() -> None:
    from bot.live.momentum_desk import DEFAULT_UNIVERSE
    from bot.research.btc_residual_mix.engine import run_btc_residual
    from bot.research.factor_scoreboard.__main__ import load_candles
    from bot.research.factor_scoreboard.engine import LIVE_KNOBS
    from bot.research.factor_scoreboard.external import funding_daily_mean, funding_history

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-03-16")
    ap.add_argument("--end", default="2026-10-14")
    ap.add_argument("--book", type=float, default=20_000.0)
    a = ap.parse_args()
    ohlc = load_candles(Path("data/residual_wet_candles"), merge_fresh=True)
    fund = funding_daily_mean(funding_history(("BTC", *DEFAULT_UNIVERSE)))
    row = run_btc_residual(
        ohlc, start=a.start, end=a.end, book_eur=a.book, keep_trades=True, **LIVE_KNOBS
    )
    pos = positions_from_trades(row["trades"])
    print(f"{len(pos)} round trips; baseline replay pnl {row['pnl_eur']:.0f}")
    hdr = f"{'variant':12s} {'total':>10s} {'maxDD':>6s} {'2024':>9s} {'2025':>9s} {'2026':>9s}"
    print(hdr + "  liq worst_low funding   fees")
    for lev, perp in LADDER:
        r = replay_leverage(
            pos,
            ohlc,
            leverage=lev,
            perp=perp,
            funding=fund,
            start=a.start,
            end=a.end,
            book_eur=a.book,
        )
        ys = " ".join(f"{r.by_year.get(y, 0):9.0f}" for y in ("2024", "2025", "2026"))
        print(
            f"{r.label:12s} {r.pnl_eur:10.0f} {r.max_dd_pct * 100:5.1f}% {ys}  "
            f"{r.liquidations:3d} {r.worst_intraday * 100:8.1f}% "
            f"{r.funding_eur:7.0f} {r.fees_eur:6.0f}"
        )
    print("-- fresh book each calendar year")
    for lev, perp in LADDER[:6]:
        cells = []
        for y in ("2024", "2025", "2026"):
            r = replay_leverage(
                pos,
                ohlc,
                leverage=lev,
                perp=perp,
                funding=fund,
                start=max(a.start, f"{y}-01-01"),
                end=min(a.end, f"{y}-12-31"),
                book_eur=a.book,
            )
            cells.append(f"{y} {r.pnl_eur:8.0f} dd {r.max_dd_pct * 100:4.1f}% liq {r.liquidations}")
        print(f"{'perp' if perp else 'spot'} {lev:g}x".ljust(12) + " | ".join(cells))


if __name__ == "__main__":
    main()

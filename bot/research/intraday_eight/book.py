"""Shared €20k book. Four slots. Nothing is armed."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bot.core.venue_fees import venue_maker_fee, venue_taker_fee
from bot.research.clip_exit_lab.engine import WET, fill_px
from bot.research.intraday_eight.strategies import Trade

MAX_SLOTS = 4
PERP_FEE = 0.0005
PERP_SLIP = 0.0005
MAKER = float(venue_maker_fee("bitvavo"))
TAKER = float(venue_taker_fee("bitvavo"))


@dataclass
class BookResult:
    mode: str
    pnl: float
    max_dd: float
    trades: int
    wins: int
    year_pnl: dict[str, float] = field(default_factory=dict)
    avg_win: float = 0.0
    avg_loss: float = 0.0


def run_book(
    trades: Sequence[Trade],
    *,
    mode: str,
    book: float = 20_000.0,
    start_ts: int = 0,
    end_ts: int = 2**62,
    compound: bool = True,
) -> BookResult:
    """``spot_long`` keeps buys on Bitvavo. ``perp`` prices both sides as a perp.

    ``compound`` sizes each slot from the equity. With it off, every slot is
    a fixed quarter of the starting book until the cash can no longer fund one.
    """
    if mode not in {"spot_long", "perp"}:
        raise ValueError(mode)
    chosen = [
        trade
        for trade in trades
        if start_ts <= trade.entry_ts < end_ts and (mode == "perp" or trade.side > 0)
    ]
    chosen.sort(key=lambda trade: (trade.entry_ts, -trade.score, trade.base))
    cash = book
    open_lots: list[dict[str, float | int | str]] = []
    peak = book
    max_dd = 0.0
    wins: list[float] = []
    losses: list[float] = []
    year_pnl: dict[str, float] = {}
    taken = 0

    def mark() -> None:
        nonlocal peak, max_dd
        equity = cash + sum(float(lot["locked"]) for lot in open_lots)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    def settle(until: int) -> None:
        nonlocal cash
        still: list[dict[str, float | int | str]] = []
        for lot in open_lots:
            if int(lot["exit_ts"]) <= until:
                cash += float(lot["cash_back"])
                pnl = float(lot["pnl"])
                year = str(lot["year"])
                year_pnl[year] = year_pnl.get(year, 0.0) + pnl
                if pnl > 0:
                    wins.append(pnl)
                elif pnl < 0:
                    losses.append(pnl)
            else:
                still.append(lot)
        open_lots[:] = still
        mark()

    for trade in chosen:
        settle(trade.entry_ts)
        if any(lot["base"] == trade.base for lot in open_lots):
            continue
        if len(open_lots) >= MAX_SLOTS:
            continue
        equity = cash + sum(float(lot["locked"]) for lot in open_lots)
        budget = equity / MAX_SLOTS if compound else book / MAX_SLOTS
        if budget < 5.0 or cash < budget:
            continue
        deal = _cash_back(trade, budget, mode)
        if deal is None:
            continue
        cash_back, pnl = deal
        cash -= budget
        open_lots.append(
            {
                "base": trade.base,
                "exit_ts": trade.exit_ts,
                "locked": budget,
                "cash_back": cash_back,
                "pnl": pnl,
                "year": datetime.fromtimestamp(trade.exit_ts / 1000, UTC).strftime("%Y"),
            }
        )
        taken += 1
        mark()
    settle(2**62)
    return BookResult(
        mode=mode,
        pnl=cash - book,
        max_dd=max_dd,
        trades=taken,
        wins=len(wins),
        year_pnl=year_pnl,
        avg_win=(sum(wins) / len(wins)) if wins else 0.0,
        avg_loss=(sum(losses) / len(losses)) if losses else 0.0,
    )


def _side_price(price: float, side: str, kind: str, notional: float, adv: float) -> float:
    if kind == "maker":
        return price
    return fill_px(price, side, notional=notional, adv=adv, model=WET)


@dataclass
class Edge:
    trades: int
    wins: int
    avg_net: float
    profit_factor: float
    pnl: float
    hole: float
    year_pnl: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, int] = field(default_factory=dict)


def measure(
    trades: Sequence[Trade],
    *,
    mode: str,
    stake: float = 5_000.0,
    start_ts: int = 0,
    end_ts: int = 2**62,
) -> Edge:
    """Every signal at a fixed stake. The sum is not limited by one account."""
    chosen = [
        trade
        for trade in trades
        if start_ts <= trade.entry_ts < end_ts and (mode == "perp" or trade.side > 0)
    ]
    chosen.sort(key=lambda trade: (trade.exit_ts, trade.entry_ts, trade.base))
    wins: list[float] = []
    losses: list[float] = []
    year_pnl: dict[str, float] = {}
    reasons: dict[str, int] = {}
    equity = 0.0
    peak = 0.0
    hole = 0.0
    for trade in chosen:
        deal = _cash_back(trade, stake, mode)
        if deal is None:
            continue
        _cash_back_value, pnl = deal
        year = datetime.fromtimestamp(trade.exit_ts / 1000, UTC).strftime("%Y")
        year_pnl[year] = year_pnl.get(year, 0.0) + pnl
        reasons[trade.reason] = reasons.get(trade.reason, 0) + 1
        if pnl > 0:
            wins.append(pnl)
        elif pnl < 0:
            losses.append(pnl)
        equity += pnl
        peak = max(peak, equity)
        hole = max(hole, peak - equity)
    taken = len(wins) + len(losses)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return Edge(
        trades=taken,
        wins=len(wins),
        avg_net=(sum(wins) + sum(losses)) / stake / taken if taken else 0.0,
        profit_factor=(gross_win / gross_loss) if gross_loss else 0.0,
        pnl=sum(wins) + sum(losses),
        hole=hole,
        year_pnl=year_pnl,
        reasons=reasons,
    )


def _cash_back(trade: Trade, budget: float, mode: str) -> tuple[float, float] | None:
    if mode == "spot_long":
        if trade.side < 0:
            return None
        fee_in = MAKER if trade.entry_kind == "maker" else TAKER
        fee_out = MAKER if trade.exit_kind == "maker" else TAKER
        spend = budget / (1.0 + fee_in)
        buy = _side_price(trade.entry_px, "buy", trade.entry_kind, spend, trade.adv)
        sell = _side_price(trade.exit_px, "sell", trade.exit_kind, spend, trade.adv)
        if buy <= 0 or sell <= 0:
            return None
        proceeds = spend / buy * sell * (1.0 - fee_out)
        return proceeds, proceeds - budget
    if trade.side > 0:
        entry = trade.entry_px * (1.0 + PERP_SLIP)
        exit_ = trade.exit_px * (1.0 - PERP_SLIP)
        if entry <= 0 or exit_ <= 0:
            return None
        qty = budget / entry
        pnl = qty * (exit_ - entry) - budget * PERP_FEE - qty * exit_ * PERP_FEE
    else:
        entry = trade.entry_px * (1.0 - PERP_SLIP)
        exit_ = trade.exit_px * (1.0 + PERP_SLIP)
        if entry <= 0 or exit_ <= 0:
            return None
        qty = budget / entry
        pnl = qty * (entry - exit_) - budget * PERP_FEE - qty * exit_ * PERP_FEE
    pnl += -trade.side * budget * trade.funding_sum
    return budget + pnl, pnl

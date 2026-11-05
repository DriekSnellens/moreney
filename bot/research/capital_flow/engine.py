"""Follow euro leaving large names into smaller ones.

Bitvavo does not publish a historical taker side. The sell is read from
Binance USDT daily klines for the same names: taker-sell base volume over
total base volume. The trade, if the signal is used, is the next Bitvavo
EUR open.

Size is the trailing 7-day Binance quote volume, ranked again each day.
The top quartile is large. The bottom quartile is small. A name whose
7-day average price stays inside 0.5% is a peg and stays out of both
baskets, so a stablecoin's turnover is not treated as a large alt being
sold. No ticker is named in the rule.

A large name is sold when its own taker-sell share is at least 55%.
"Many coins" means at least half the large names are sold that day.
Neighbours are 40% and 60% of the names. "A lot of money" means the
quote-weighted sell share of the large basket is at least 55%, with
neighbours 52% and 58%. The mirror is the basket being bought, taker-sell
at most 45%.

The long buys the next Bitvavo open and sells the open after that. A day
needs 32 ranked names, and a basket needs 8. Coins below €10,000 of
Bitvavo quote on the signal day are not bought. Wet fills use a €5,000
stake, taker 0.25% each side, and impact ``0.02 * sqrt(stake / adv)``
capped at 3% on both sides. The adv is the signal day's Bitvavo quote.
Every cut below is locked before the tape runs. Nothing is armed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from bot.research.clip_exit_lab.engine import WET, extra_slip
from bot.research.small_peak.engine import net_long

DAY_MS = 86_400_000
ADV_DAYS = 7
PEG_BAND = 0.005
SELL_COIN = 0.55
SELL_BOOK = 0.55
SELL_BOOK_NEIGHBOURS = (0.52, 0.58)
BUY_BOOK = 0.45
BREADTH = 0.50
BREADTH_NEIGHBOURS = (0.40, 0.60)
MIN_CROSS = 32
MIN_NAMES = 8
MIN_DAY_QUOTE = 10_000.0
STAKE = 5_000.0
RISE = 0.03

# ts, base volume, quote volume, taker-buy base volume
FlowRow = tuple[int, float, float, float]
# ts, open, high, euro quote
BarRow = tuple[int, float, float, float]


@dataclass(frozen=True)
class DayResult:
    signal_ms: int
    large_sell: float
    breadth: float
    n_large: int
    n_peg: int
    small_net: float
    small_wet: float
    small_rise: float
    small_buy: float
    mid_net: float
    mid_wet: float
    mid_rise: float
    large_net: float
    large_wet: float
    large_rise: float
    n_small: int
    n_mid: int
    n_large_trade: int


def sell_share(volume: float, taker_buy: float) -> float:
    """Taker-sell base volume over total base volume. Outside 0–1 is NaN."""
    if volume <= 0.0 or not math.isfinite(volume) or not math.isfinite(taker_buy):
        return math.nan
    share = (volume - taker_buy) / volume
    if share < 0.0 or share > 1.0:
        return math.nan
    return share


def is_peg(vwaps: Sequence[float], *, band: float = PEG_BAND) -> bool:
    """True when the 7-day average price stays inside ``band``."""
    prices = [px for px in vwaps if math.isfinite(px) and px > 0.0]
    if len(prices) < ADV_DAYS:
        return False
    lo = min(prices)
    hi = max(prices)
    if lo <= 0.0:
        return False
    return hi / lo - 1.0 < band


def wet_net(entry: float, exit_px: float, adv: float, *, notional: float = STAKE) -> float:
    """Taker plus impact on both sides. ``adv`` is known before the entry."""
    slip = extra_slip(notional, adv, WET)
    return net_long(entry * (1.0 + slip), exit_px * (1.0 - slip))


def _window(by_ts: Mapping[int, FlowRow], ts: int) -> list[FlowRow] | None:
    rows: list[FlowRow] = []
    for age in range(ADV_DAYS - 1, -1, -1):
        row = by_ts.get(ts - age * DAY_MS)
        if row is None:
            return None
        rows.append(row)
    return rows


def _mean(values: list[float]) -> float:
    good = [value for value in values if math.isfinite(value)]
    if not good:
        return math.nan
    return float(sum(good) / len(good))


def _weighted(values: list[float], weights: list[float]) -> float:
    num = 0.0
    den = 0.0
    for value, weight in zip(values, weights, strict=True):
        if not math.isfinite(value) or not math.isfinite(weight) or weight <= 0.0:
            continue
        num += value * weight
        den += weight
    if den <= 0.0:
        return math.nan
    return num / den


def run_days(
    flow: Mapping[str, Sequence[FlowRow]],
    bars: Mapping[str, Sequence[BarRow]],
) -> list[DayResult]:
    """One row per signal day. The entry is the Bitvavo open one day later."""
    flow_by = {
        base: {
            int(row[0]): (int(row[0]), float(row[1]), float(row[2]), float(row[3])) for row in rows
        }
        for base, rows in flow.items()
    }
    bar_by = {
        base: {
            int(row[0]): (int(row[0]), float(row[1]), float(row[2]), float(row[3])) for row in rows
        }
        for base, rows in bars.items()
    }
    stamps = sorted({ts for book in flow_by.values() for ts in book})
    out: list[DayResult] = []
    for ts in stamps:
        out.append(_one_day(ts, flow_by, bar_by))
    return [row for row in out if row.n_large > 0 or row.n_peg > 0]


def _one_day(
    ts: int,
    flow_by: Mapping[str, Mapping[int, FlowRow]],
    bar_by: Mapping[str, Mapping[int, BarRow]],
) -> DayResult:
    ranked_adv: list[float] = []
    ranked_sell: list[float] = []
    ranked_quote: list[float] = []
    ranked_trade: list[tuple[float, float, float, float] | None] = []
    n_peg = 0
    for base, book in flow_by.items():
        window = _window(book, ts)
        if window is None:
            continue
        vwaps = [row[2] / row[1] if row[1] > 0.0 else math.nan for row in window]
        if is_peg(vwaps):
            n_peg += 1
            continue
        today = window[-1]
        share = sell_share(today[1], today[3])
        if not math.isfinite(share):
            continue
        adv = sum(row[2] for row in window)
        if adv <= 0.0:
            continue
        ranked_adv.append(adv)
        ranked_sell.append(share)
        ranked_quote.append(today[2])
        ranked_trade.append(_trade(bar_by.get(base, {}), ts))
    empty = _blank(ts, n_peg)
    if len(ranked_adv) < MIN_CROSS:
        return empty
    adv = np.asarray(ranked_adv, dtype=np.float64)
    p25, p75 = (float(x) for x in np.quantile(adv, [0.25, 0.75]))
    if not math.isfinite(p25) or not math.isfinite(p75) or p75 <= p25:
        return empty
    large = adv >= p75
    small = adv <= p25
    mid = (adv > p25) & (adv < p75)
    sell = np.asarray(ranked_sell, dtype=np.float64)
    quote = np.asarray(ranked_quote, dtype=np.float64)
    large_ix = np.flatnonzero(large)
    if len(large_ix) < MIN_NAMES:
        return empty
    large_sell = _weighted(sell[large_ix].tolist(), quote[large_ix].tolist())
    breadth = float(np.mean(sell[large_ix] >= SELL_COIN))
    small_stats = _basket(np.flatnonzero(small), ranked_trade, sell, quote)
    mid_stats = _basket(np.flatnonzero(mid), ranked_trade, sell, quote)
    large_stats = _basket(large_ix, ranked_trade, sell, quote)
    return DayResult(
        signal_ms=ts,
        large_sell=large_sell,
        breadth=breadth,
        n_large=int(len(large_ix)),
        n_peg=n_peg,
        small_net=small_stats[0],
        small_wet=small_stats[1],
        small_rise=small_stats[2],
        small_buy=small_stats[3],
        mid_net=mid_stats[0],
        mid_wet=mid_stats[1],
        mid_rise=mid_stats[2],
        large_net=large_stats[0],
        large_wet=large_stats[1],
        large_rise=large_stats[2],
        n_small=small_stats[4],
        n_mid=mid_stats[4],
        n_large_trade=large_stats[4],
    )


def _trade(book: Mapping[int, BarRow], ts: int) -> tuple[float, float, float, float] | None:
    """Fee net, wet net, rise, and the signal-day quote. Entry is the next open."""
    signal = book.get(ts)
    entry = book.get(ts + DAY_MS)
    exit_bar = book.get(ts + 2 * DAY_MS)
    if signal is None or entry is None or exit_bar is None:
        return None
    quote = signal[3]
    entry_px = entry[1]
    exit_px = exit_bar[1]
    if quote < MIN_DAY_QUOTE or entry_px <= 0.0 or exit_px <= 0.0:
        return None
    rise = entry[2] / entry_px - 1.0
    return net_long(entry_px, exit_px), wet_net(entry_px, exit_px, quote), rise, quote


def _basket(
    indexes: np.ndarray,
    trades: list[tuple[float, float, float, float] | None],
    sell: np.ndarray,
    quote: np.ndarray,
) -> tuple[float, float, float, float, int]:
    nets: list[float] = []
    wets: list[float] = []
    rises: list[float] = []
    buy_weights: list[float] = []
    buy_values: list[float] = []
    for index in indexes:
        i = int(index)
        trade = trades[i]
        if trade is None:
            continue
        net, wet, rise, _day_quote = trade
        nets.append(net)
        wets.append(wet)
        rises.append(1.0 if rise >= RISE else 0.0)
        buy_values.append(1.0 - float(sell[i]))
        buy_weights.append(float(quote[i]))
    n = len(nets)
    if n < MIN_NAMES:
        return math.nan, math.nan, math.nan, math.nan, n
    return _mean(nets), _mean(wets), _mean(rises), _weighted(buy_values, buy_weights), n


def _blank(ts: int, n_peg: int) -> DayResult:
    nan = math.nan
    return DayResult(
        ts, nan, nan, 0, n_peg, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan, 0, 0, 0
    )


def keep(
    days: Sequence[DayResult],
    *,
    sell_at_least: float | None = None,
    sell_at_most: float | None = None,
    breadth_at_least: float | None = None,
) -> list[DayResult]:
    """Days whose large-basket signal clears the locked cut."""
    kept: list[DayResult] = []
    for day in days:
        if not math.isfinite(day.large_sell) or not math.isfinite(day.breadth):
            continue
        if sell_at_least is not None and day.large_sell < sell_at_least:
            continue
        if sell_at_most is not None and day.large_sell > sell_at_most:
            continue
        if breadth_at_least is not None and day.breadth < breadth_at_least:
            continue
        kept.append(day)
    return kept


def basket_stats(days: Sequence[DayResult], field: str) -> dict[str, float]:
    """Mean and median of one daily basket field. Days without a basket are out."""
    values = [
        float(getattr(day, field)) for day in days if math.isfinite(float(getattr(day, field)))
    ]
    if not values:
        return {"n": 0.0}
    ordered = sorted(values)
    mid = len(ordered) // 2
    even = 0.5 * (ordered[mid - 1] + ordered[mid])
    median = ordered[mid] if len(ordered) % 2 else even
    return {
        "n": float(len(values)),
        "mean": float(sum(values) / len(values)),
        "median": float(median),
    }

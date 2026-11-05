"""Locked patterns for a second rise, then the same picks as a book.

The question is the one asked of a quiet or obscure alt: a rise of 0 to 10
percent that already happened, and the chance the name rises again. The
rise is the seven-day return ending 28 days earlier. Neighbours move that
window, that band, and that universe. A few shapes add a flat week, a
later dip, a wide range, or a compressed range. CoinMarketCap and AlphaI
are extra filters on that same core, and only on the dates they cover.

The signal is the close. The chance is the next open against the open
seven days later. The book buys, on one weekly clock, the five matches
with the smallest quote. Each slot is a fifth of the book and at most 2
percent of that day's quote. Fills pay the Bitvavo taker and impact.
Pegs and the benchmark stay out.

A book clears the bar with at least 30 weeks, an end above €20,000, and a
plus with at least eight weeks in each of 2024, 2025 and 2026. A chance is
only quoted at 100 names or more. Nothing is armed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from bot.research.capital_flow.engine import is_peg, wet_net
from bot.research.week_book.engine import DAY_MS, Coin

START = 20_000.0
FLOOR = 10_000.0
LARGE = 1_000_000.0
SLOTS = 5
ADV_CAP = 0.02
BENCHMARK = "BTC"
YEARS = (2024, 2025, 2026)
MIN_N = 100
MIN_WEEKS = 30
MIN_YEAR_WEEKS = 8
BANDS: dict[str, tuple[float, float | None]] = {
    "0 tot 10%": (0.0, 0.10),
    "10 tot 30%": (0.10, 0.30),
    "−10 tot 0%": (-0.10, 0.0),
    "vanaf 30%": (0.30, None),
}
LAGS = (0, 14, 28, 42)
LAG_NAME = {
    0: "afgelopen week",
    14: "twee weken geleden",
    28: "een maand geleden",
    42: "zes weken geleden",
}
UNIVERSES = ("obscure", "stil", "dun", "liquide")


@dataclass(frozen=True)
class Spec:
    name: str
    family: str
    universe: str
    lag: int = 28
    band: str = "0 tot 10%"
    shape: str = ""

    def key(self) -> tuple:
        return (self.family, self.universe, self.lag, self.band, self.shape)


def _core(universe: str, lag: int, band: str) -> str:
    return f"{universe}, {LAG_NAME[lag]} {band}"


def grid() -> list[Spec]:
    specs: list[Spec] = []
    for universe in UNIVERSES:
        for lag in LAGS:
            for band in BANDS:
                specs.append(
                    Spec(_core(universe, lag, band), "patroon", universe, lag, band)
                )
    dun = "dun, een maand geleden 0 tot 10%"
    specs.extend(
        [
            Spec(f"{dun}, week vlak", "vorm", "dun", shape="vlak"),
            Spec(f"{dun}, week erna −10% of meer", "vorm", "dun", shape="dip"),
            Spec(f"{dun}, week erna ook 0 tot 10%", "vorm", "dun", shape="herhaal"),
            Spec("dun, wijde band en week 0 tot 10%", "vorm", "dun", shape="wijd"),
            Spec(f"{dun}, band samengedrukt", "vorm", "dun", shape="compressie"),
            Spec("dun, de maand tot een week geleden 0 tot 10%", "vorm", "dun", shape="maand"),
            Spec("dun, afgelopen maand 0 tot 10%", "vorm", "dun", shape="maand_nu"),
            Spec("obscure, afgelopen maand 0 tot 10%", "vorm", "obscure", shape="maand_nu"),
            Spec(f"{dun}, CMC-rang slechter dan 100", "cmc", "dun", shape="cmc_rang"),
            Spec(f"{dun}, CMC-volume stil", "cmc", "dun", shape="cmc_vol"),
            Spec(f"{dun}, marktwaarde 7d omlaag", "cmc", "dun", shape="cmc_mcap_min"),
            Spec(f"{dun}, marktwaarde 7d omhoog", "cmc", "dun", shape="cmc_mcap_plus"),
            Spec(f"{dun}, wereldvolume stil", "cmc", "dun", shape="cmc_wereld"),
            Spec(f"{dun}, positief nieuws", "alphai", "dun", shape="nieuws_pos"),
            Spec(f"{dun}, negatief nieuws", "alphai", "dun", shape="nieuws_neg"),
            Spec(f"{dun}, geen nieuws", "alphai", "dun", shape="nieuws_geen"),
        ]
    )
    return specs


@dataclass
class Table:
    ms: np.ndarray
    phase: np.ndarray
    quote: np.ndarray
    entry: np.ndarray
    exit: np.ndarray
    gross: np.ndarray
    year: np.ndarray
    obscure: np.ndarray
    quiet: np.ndarray
    liquid: np.ndarray
    lag: dict[int, np.ndarray]
    last: np.ndarray
    wide: np.ndarray
    compress: np.ndarray
    month: np.ndarray
    month_now: np.ndarray
    cmc_rank: np.ndarray
    cmc_quiet: np.ndarray
    mcap_down: np.ndarray
    mcap_up: np.ndarray
    world_quiet: np.ndarray
    news: np.ndarray


@dataclass
class Context:
    ranks: dict[tuple[str, int], int] = field(default_factory=dict)
    vol_quiet: set[tuple[str, int]] = field(default_factory=set)
    mcap_down: set[int] = field(default_factory=set)
    mcap_up: set[int] = field(default_factory=set)
    world_quiet: set[int] = field(default_factory=set)
    news: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    news_from: int = 0


def _span(ts: np.ndarray, left: int, right: int) -> bool:
    return int(ts[right]) - int(ts[left]) == (right - left) * DAY_MS


def _ret(close: np.ndarray, ts: np.ndarray, end: int, bars: int) -> float:
    start = end - bars
    if start < 0 or close[start] <= 0.0 or not _span(ts, start, end):
        return np.nan
    return float(close[end] / close[start] - 1.0)


def _in_band(value: float, band: str) -> bool:
    if not np.isfinite(value):
        return False
    lo, hi = BANDS[band]
    if value < lo:
        return False
    return hi is None or value < hi


def _universe(obscure: bool, quiet: bool, liquid: bool, name: str) -> bool:
    if name == "obscure":
        return obscure
    if name == "stil":
        return quiet
    if name == "dun":
        return obscure or quiet
    if name == "liquide":
        return liquid
    raise KeyError(name)


def build_table(
    coins: list[Coin],
    dates: np.ndarray,
    start_ms: int,
    ctx: Context,
) -> Table:
    """One row per name and master day, from the first tradable session."""
    start_at = int(np.searchsorted(dates, start_ms, side="left"))
    phase_of = {
        int(stamp): (index - start_at) % 7
        for index, stamp in enumerate(dates)
        if index >= start_at
    }
    allowed = set(phase_of)
    columns: dict[str, list] = {name: [] for name in (
        "ms", "phase", "quote", "entry", "exit", "gross", "year",
        "obscure", "quiet", "liquid", "last", "wide", "compress",
        "month", "month_now", "cmc_rank", "cmc_quiet", "mcap_down",
        "mcap_up", "world_quiet", "news",
    )}
    lagged = {lag: [] for lag in LAGS}
    for coin in coins:
        if coin.base == BENCHMARK:
            continue
        ts = coin.ts
        n = len(ts)
        for i in range(n - 8):
            stamp = int(ts[i])
            if stamp not in allowed or i < 6:
                continue
            if not _span(ts, i, i + 1) or not _span(ts, i + 1, i + 8):
                continue
            if is_peg(coin.close[i - 6 : i + 1]):
                continue
            quote = float(coin.quote[i])
            entry = float(coin.open[i + 1])
            exit_px = float(coin.open[i + 8])
            if quote < FLOOR or entry <= 0.0 or exit_px <= 0.0:
                continue
            asof = stamp + DAY_MS
            news = _news_code(ctx.news, coin.base, stamp, ctx.news_from)
            columns["ms"].append(stamp)
            columns["phase"].append(phase_of[stamp])
            columns["quote"].append(quote)
            columns["entry"].append(entry)
            columns["exit"].append(exit_px)
            columns["gross"].append(exit_px / entry - 1.0)
            columns["year"].append(_year(stamp))
            columns["obscure"].append(quote < LARGE)
            columns["liquid"].append(quote >= LARGE)
            columns["quiet"].append(_quiet(coin, i))
            columns["last"].append(_ret(coin.close, ts, i, 7))
            columns["wide"].append(_wide(coin, i))
            columns["compress"].append(_compress(coin, i))
            columns["month"].append(_ret(coin.close, ts, i - 7, 28) if i >= 7 else np.nan)
            columns["month_now"].append(_ret(coin.close, ts, i, 28))
            columns["cmc_rank"].append(ctx.ranks.get((coin.base, asof), -1))
            columns["cmc_quiet"].append((coin.base, asof) in ctx.vol_quiet)
            columns["mcap_down"].append(stamp in ctx.mcap_down)
            columns["mcap_up"].append(stamp in ctx.mcap_up)
            columns["world_quiet"].append(stamp in ctx.world_quiet)
            columns["news"].append(news)
            for lag in LAGS:
                lagged[lag].append(_ret(coin.close, ts, i - lag, 7) if i >= lag else np.nan)
    def col(name: str, dtype: object) -> np.ndarray:
        return np.asarray(columns[name], dtype=dtype)

    return Table(
        ms=col("ms", np.int64),
        phase=col("phase", np.int8),
        quote=col("quote", np.float64),
        entry=col("entry", np.float64),
        exit=col("exit", np.float64),
        gross=col("gross", np.float64),
        year=col("year", np.int16),
        obscure=col("obscure", bool),
        quiet=col("quiet", bool),
        liquid=col("liquid", bool),
        lag={lag: np.asarray(values, dtype=np.float64) for lag, values in lagged.items()},
        last=col("last", np.float64),
        wide=col("wide", bool),
        compress=col("compress", bool),
        month=col("month", np.float64),
        month_now=col("month_now", np.float64),
        cmc_rank=col("cmc_rank", np.int32),
        cmc_quiet=col("cmc_quiet", bool),
        mcap_down=col("mcap_down", bool),
        mcap_up=col("mcap_up", bool),
        world_quiet=col("world_quiet", bool),
        news=col("news", np.int8),
    )


def _quiet(coin: Coin, i: int) -> bool:
    if i < 27 or not _span(coin.ts, i - 6, i) or not _span(coin.ts, i - 27, i - 7):
        return False
    recent = float(np.mean(coin.quote[i - 6 : i + 1]))
    prior = float(np.mean(coin.quote[i - 27 : i - 6]))
    return prior > 0.0 and recent <= 0.5 * prior


def _wide(coin: Coin, i: int) -> bool:
    if i < 19 or not _span(coin.ts, i - 19, i) or coin.close[i] <= 0.0:
        return False
    hi = float(np.max(coin.high[i - 19 : i + 1]))
    lo = float(np.min(coin.low[i - 19 : i + 1]))
    return hi > lo and (hi - lo) / float(coin.close[i]) >= 0.25


def _compress(coin: Coin, i: int) -> bool:
    if i < 26 or not _span(coin.ts, i - 6, i) or not _span(coin.ts, i - 26, i - 7):
        return False
    close = float(coin.close[i])
    if close <= 0.0:
        return False
    near_hi = float(np.max(coin.high[i - 6 : i + 1]))
    near_lo = float(np.min(coin.low[i - 6 : i + 1]))
    far_hi = float(np.max(coin.high[i - 26 : i - 6]))
    far_lo = float(np.min(coin.low[i - 26 : i - 6]))
    near = near_hi - near_lo
    far = far_hi - far_lo
    return far > 0.0 and near <= 0.5 * far


def _news_code(
    by_base: dict[str, list[tuple[int, str]]],
    base: str,
    stamp: int,
    window_from: int,
) -> int:
    """1 positive, 2 negative, 3 both, 0 none, -1 before the feed."""
    if stamp < window_from:
        return -1
    items = by_base.get(base)
    if not items:
        return 0
    hi = stamp + DAY_MS
    lo = hi - 7 * DAY_MS
    pos = neg = False
    for published, label in items:
        if published <= lo:
            continue
        if published > hi:
            break
        pos = pos or label == "positive"
        neg = neg or label == "negative"
    if pos and neg:
        return 3
    if pos:
        return 1
    if neg:
        return 2
    return 0


def _core_mask(table: Table, spec: Spec) -> np.ndarray:
    uni = _universe_mask(table, spec.universe)
    return uni & _band_mask(table.lag[spec.lag], spec.band)


def _universe_mask(table: Table, name: str) -> np.ndarray:
    if name == "obscure":
        return table.obscure
    if name == "stil":
        return table.quiet
    if name == "dun":
        return table.obscure | table.quiet
    if name == "liquide":
        return table.liquid
    raise KeyError(name)


def _band_mask(values: np.ndarray, band: str) -> np.ndarray:
    lo, hi = BANDS[band]
    mask = np.isfinite(values) & (values >= lo)
    if hi is not None:
        mask &= values < hi
    return mask


def cell_mask(table: Table, spec: Spec) -> np.ndarray:
    """Names the cell points at. The core is the lagged band."""
    if spec.shape == "":
        return _core_mask(table, spec)
    core = _universe_mask(table, spec.universe) & _band_mask(table.lag[28], "0 tot 10%")
    last_small = _band_mask(table.last, "0 tot 10%")
    if spec.shape == "vlak":
        last = table.last
        return core & np.isfinite(last) & (last >= -0.05) & (last < 0.05)
    if spec.shape == "dip":
        return core & np.isfinite(table.last) & (table.last <= -0.10)
    if spec.shape == "herhaal":
        return core & last_small
    if spec.shape == "wijd":
        return _universe_mask(table, spec.universe) & table.wide & last_small
    if spec.shape == "compressie":
        return core & table.compress
    if spec.shape == "maand":
        return _universe_mask(table, spec.universe) & _band_mask(table.month, "0 tot 10%")
    if spec.shape == "maand_nu":
        return _universe_mask(table, spec.universe) & _band_mask(table.month_now, "0 tot 10%")
    if spec.shape == "cmc_rang":
        return core & (table.cmc_rank > 100)
    if spec.shape == "cmc_vol":
        return core & table.cmc_quiet
    if spec.shape == "cmc_mcap_min":
        return core & table.mcap_down
    if spec.shape == "cmc_mcap_plus":
        return core & table.mcap_up
    if spec.shape == "cmc_wereld":
        return core & table.world_quiet
    if spec.shape == "nieuws_pos":
        return core & ((table.news == 1) | (table.news == 3))
    if spec.shape == "nieuws_neg":
        return core & ((table.news == 2) | (table.news == 3))
    if spec.shape == "nieuws_geen":
        return core & (table.news == 0)
    raise KeyError(spec.shape)


def base_mask(table: Table, spec: Spec) -> np.ndarray:
    """What the cell is compared with.

    A lagged band is compared with its universe. A CoinMarketCap or AlphaI
    filter is compared with the same month-ago rise on the days that tool
    has a print.
    """
    uni = _universe_mask(table, spec.universe)
    if spec.family in {"cmc", "alphai"}:
        core = uni & _band_mask(table.lag[28], "0 tot 10%")
        if spec.family == "alphai":
            return core & (table.news >= 0)
        if spec.shape in {"cmc_rang", "cmc_vol"}:
            return core & (table.cmc_rank > 0)
        return core & (table.mcap_down | table.mcap_up | table.world_quiet)
    return uni


@dataclass
class Row:
    name: str
    family: str
    n: int
    groen: float
    plus10: float
    base: float
    mean_gross: float
    mean_wet: float
    end: float
    pnl: float
    dd: float
    weeks: int
    year_pnl: dict[int, float]
    year_weeks: dict[int, int]
    phase_min: float | None
    slots: float
    clears: bool = False


def _rate(gross: np.ndarray) -> tuple[int, float, float, float]:
    n = int(gross.size)
    if n == 0:
        return 0, 0.0, 0.0, 0.0
    return n, float(np.mean(gross > 0.0)), float(np.mean(gross >= 0.10)), float(np.mean(gross))


def evaluate(table: Table, spec: Spec, clock: np.ndarray) -> Row:
    mask = cell_mask(table, spec)
    primary = mask & (table.phase == 0)
    gross = table.gross[primary]
    n, groen, plus10, mean_gross = _rate(gross)
    base = _rate(table.gross[base_mask(table, spec) & (table.phase == 0)])[1]
    wets = []
    if n:
        notion = np.minimum(START / SLOTS, ADV_CAP * table.quote[primary])
        for entry, exit_px, adv, stake in zip(
            table.entry[primary], table.exit[primary], table.quote[primary], notion, strict=False
        ):
            wets.append(wet_net(float(entry), float(exit_px), float(adv), notional=float(stake)))
    mean_wet = float(np.mean(wets)) if wets else 0.0
    phase_rates = []
    for phase in range(7):
        count, rate, _ten, _avg = _rate(table.gross[mask & (table.phase == phase)])
        if count >= MIN_N:
            phase_rates.append(rate)
    phase_min = min(phase_rates) if len(phase_rates) >= 2 else None
    end, pnl, dd, weeks, year_pnl, year_weeks, slots = _book(table, primary, clock)
    return Row(
        spec.name,
        spec.family,
        n,
        groen,
        plus10,
        base,
        mean_gross,
        mean_wet,
        end,
        pnl,
        dd,
        weeks,
        year_pnl,
        year_weeks,
        phase_min,
        slots,
    )


def _book(
    table: Table, primary: np.ndarray, clock: np.ndarray
) -> tuple[float, float, float, int, dict[int, float], dict[int, int], float]:
    by_day: dict[int, list[int]] = defaultdict(list)
    for idx in np.flatnonzero(primary):
        by_day[int(table.ms[idx])].append(int(idx))
    equity = START
    peak = START
    worst = 0.0
    weeks = 0
    year_end: dict[int, float] = {}
    year_weeks = {year: 0 for year in YEARS}
    used = []
    for stamp in clock:
        picks = by_day.get(int(stamp), [])
        picks.sort(key=lambda idx: table.quote[idx])
        chosen = picks[:SLOTS]
        used.append(len(chosen) / SLOTS)
        if not chosen or equity < 1.0:
            year_end[_year(int(stamp))] = equity
            continue
        pnl = 0.0
        traded = 0
        room = equity
        for idx in chosen:
            notional = min(equity / SLOTS, ADV_CAP * float(table.quote[idx]), room)
            if notional < 1.0:
                continue
            room -= notional
            gain = wet_net(
                float(table.entry[idx]),
                float(table.exit[idx]),
                float(table.quote[idx]),
                notional=notional,
            )
            pnl += notional * gain
            traded += 1
        if traded:
            weeks += 1
            year = _year(int(stamp))
            if year in year_weeks:
                year_weeks[year] += 1
        equity += pnl
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
        year_end[_year(int(stamp))] = equity
    year_pnl: dict[int, float] = {}
    prev = START
    for year in sorted(year_end):
        year_pnl[year] = year_end[year] - prev
        prev = year_end[year]
    slots = float(np.mean(used)) if used else 0.0
    return equity, equity - START, worst, weeks, year_pnl, year_weeks, slots


def clears(row: Row) -> bool:
    if row.weeks < MIN_WEEKS or row.end <= START:
        return False
    return all(
        row.year_pnl.get(year, 0.0) > 0.0 and row.year_weeks.get(year, 0) >= MIN_YEAR_WEEKS
        for year in YEARS
    )


def _year(stamp: int) -> int:
    return datetime.fromtimestamp(stamp / 1000, UTC).year

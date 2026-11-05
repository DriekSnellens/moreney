"""Scan what is already known the day before a 50% rise.

Usage::

    .venv/bin/python -m bot.research.rise_50
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from bot.research.capital_flow.engine import sell_share
from bot.research.rise_50.engine import (
    coin_rows,
    concat_books,
    describe,
    hit_rate,
    intraday_flags,
    market_ratios,
    universe,
)
from bot.research.small_peak.__main__ import (
    BARS,
    MACRO,
    NEWS,
    _load_ohlc,
    _ms,
    load_coins,
    load_market,
)
from bot.research.small_peak.engine import labels_between, state_of

DAILY = Path("data/research/bitvavo_1d_all")
FLOW = Path("data/entry_quality_flow")
OUT = Path("bot/research/rise_50/RISE.md")
NEWS_TO = "2026-11-03"
SIGNALS = (
    ("alle dagen", None),
    ("koopdruk 65%", "koop"),
    ("verkoopdruk", "verkoop"),
    ("coinvolume onder 1×", "vol_lo"),
    ("coinvolume 1–2×", "vol_mid"),
    ("coinvolume vanaf 2×", "vol_hi"),
    ("marktvolume onder 1×", "mkt_lo"),
    ("marktvolume 1–2×", "mkt_mid"),
    ("marktvolume vanaf 2×", "mkt_hi"),
    ("taker-koop 55%", "taker_buy"),
    ("taker-sell 55%", "taker_sell"),
    ("dag ervoor al +10%", "up10"),
    ("dag ervoor al +20%", "up20"),
    ("slot bij de 20-daagse high", "near_high"),
    ("kleine piek die dag", "peak"),
)


def _load_daily(path: Path) -> tuple[np.ndarray, ...]:
    raw = json.loads(path.read_text())
    ts = np.asarray([int(row[0]) for row in raw], dtype=np.int64)
    open_ = np.asarray([float(row[1]) for row in raw], dtype=np.float64)
    high = np.asarray([float(row[2]) for row in raw], dtype=np.float64)
    close = np.asarray([float(row[4]) for row in raw], dtype=np.float64)
    volume = np.asarray([float(row[5]) for row in raw], dtype=np.float64)
    order = np.argsort(ts, kind="mergesort")
    ts, open_, high, close, volume = (col[order] for col in (ts, open_, high, close, volume))
    keep = np.concatenate([[True], np.diff(ts) > 0])
    return ts[keep], open_[keep], high[keep], close[keep], close[keep] * volume[keep]


def _load_taker(path: Path) -> dict[int, float]:
    if not path.exists():
        return {}
    out: dict[int, float] = {}
    for row in json.loads(path.read_text()):
        share = sell_share(float(row[1]), float(row[4]))
        if np.isfinite(share):
            out[int(row[0])] = share
    return out


def _pct(value: float) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{100.0 * value:.1f}%"


def _year(ms: np.ndarray) -> np.ndarray:
    return ms.astype("datetime64[ms]").astype("datetime64[Y]").astype(int) + 1970


def _row(book: dict[str, np.ndarray], rows: np.ndarray, label: str, signal: str | None) -> str:
    stats = describe(book, rows, signal)
    if stats["n"] <= 0.0:
        return f"| {label} | 0 | — | — | — | — | — | — | — |"
    return (
        f"| {label} | {int(stats['n'])} | {_pct(hit_rate(book, rows, signal, 'hit30'))} "
        f"| {_pct(stats['precision'])} | {_pct(hit_rate(book, rows, signal, 'hit100'))} "
        f"| {_pct(stats['recall'])} | {_pct(stats['mean'])} | {_pct(stats['median'])} "
        f"| {_pct(stats['wet'])} |"
    )


def _head() -> list[str]:
    return [
        "| signaal | n | +30% | +50% | +100% | van de +50% | gem. | mediaan | nat |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]


def _section(book: dict[str, np.ndarray], rows: np.ndarray) -> list[str]:
    return [_row(book, rows, label, signal) for label, signal in SIGNALS]


def _years(book: dict[str, np.ndarray], rows: np.ndarray) -> list[str]:
    years = _year(book["entry_ms"])
    lines = [
        "| signaal | 2024 | 2025 | 2026 |",
        "|---|---:|---:|---:|",
    ]
    for label, signal in SIGNALS:
        cells = []
        for year in (2024, 2025, 2026):
            stats = describe(book, rows & (years == year), signal)
            cells.append("—" if stats["n"] <= 0.0 else _pct(stats["mean"]))
        lines.append(f"| {label} | {cells[0]} | {cells[1]} | {cells[2]} |")
    return lines


def _attach_news(book: dict[str, np.ndarray]) -> None:
    start = _ms("2026-08-08")
    end = _ms(NEWS_TO)
    entry = book["entry_ms"].astype(np.int64)
    window = np.flatnonzero((entry >= start) & (entry < end))
    book["coin_bull"][:] = np.nan
    book["mkt_bull"][:] = np.nan
    coins = load_coins(NEWS)
    macro_ts, macro_side = load_market(MACRO)
    empty = (np.asarray([], dtype=np.int64), np.asarray([], dtype=np.int64))
    for index in window:
        stamp = int(entry[index])
        coin_ts, coin_side = coins.get(str(book["base"][index]), empty)
        coin = state_of(labels_between(coin_ts, coin_side, stamp), tie="quiet")
        tape = state_of(labels_between(macro_ts, macro_side, stamp), tie="mixed")
        book["coin_bull"][index] = 1.0 if coin == "bull" else 0.0
        book["mkt_bull"][index] = 1.0 if tape == "bull" else 0.0
    joint = np.full(len(entry), np.nan)
    known = np.isfinite(book["coin_bull"])
    joint[known] = ((book["coin_bull"][known] == 1.0) & (book["up10"][known] == 1.0)).astype(float)
    book["joint"] = joint


def _write(path: Path, book: dict[str, np.ndarray]) -> None:
    broad = universe(book, liquid=False)
    liquid = universe(book, liquid=True)
    base = describe(book, broad, None)
    peak = describe(book, broad, "peak")
    prior = describe(book, broad, "up10")
    lines = [
        "# Stijgingen van 50% of meer",
        "",
        "De vraag is of zo'n coin de dag ervoor al te herkennen is. De entry",
        "is de Bitvavo-open van de volgende dag. Een stijging is de high over",
        "de zeven dagelijkse bars daarna. De order verkoopt op +50% boven de",
        "entry, of op de open zeven dagen later. +30% en +100% zijn de buren.",
        "Een bar die door het niveau opent, vult op de open.",
        "",
        "De signalen staan vast. Koopdruk is het up-bar-aandeel van die dag,",
        "minstens 65%, met verkoop als spiegel. Coin- en marktvolume zijn de",
        "omzet van die dag tegen de zeven dagen ervoor, in de bakken onder 1×,",
        "1–2× en vanaf 2×. Taker-koop en taker-sell komen van Binance en",
        "liggen op 55%. Verder: de dag sloot al +10% of +20%, het slot zit",
        "binnen 5% van de high van 20 dagen, en er was die dag een kleine piek.",
        "",
        "Een peg, zeven closes binnen 0,5%, valt eruit. De brede mand heeft",
        "minstens €10.000 omzet die dag, de liquide mand minstens €1.000.000.",
        "De natte kolom rekent een order van €5.000 bovenop 0,25% taker per",
        "kant. De eerste zeven dagen van een coin zitten er niet in, omdat",
        "de peg-toets en het volume anders niet bekend zijn.",
        "",
        "## Brede mand",
        "",
        *_head(),
        *_section(book, broad),
        "",
        "Het gemiddelde per jaar is de order die op +50% verkoopt.",
        "",
        *_years(book, broad),
        "",
        "## Liquide mand, dagomzet vanaf €1.000.000",
        "",
        *_head(),
        *_section(book, liquid),
        "",
        f"## Nieuwsvenster, entry van 8 augustus 2026 tot {NEWS_TO}",
        "",
        "Alleen de brede mand. Coin bull en markt bull zijn de AlphaI-",
        "meerderheid in de 24 uur vóór de entry.",
        "",
        *_head(),
    ]
    start = _ms("2026-08-08")
    end = _ms(NEWS_TO)
    window = broad & (book["entry_ms"] >= start) & (book["entry_ms"] < end)
    news_rows = (
        ("alle dagen in het venster", None),
        ("coin bull", "coin_bull"),
        ("markt bull", "mkt_bull"),
        ("coin bull en dag ervoor al +10%", "joint"),
    )
    lines += [_row(book, window, label, signal) for label, signal in news_rows]
    lines += [
        "",
        _closing(base, peak, prior),
        "",
        "Niets hiervan wordt live gezet.",
        "",
    ]
    path.write_text("\n".join(lines))


def _closing(base: dict[str, float], peak: dict[str, float], prior: dict[str, float]) -> str:
    if base["n"] <= 0.0:
        return "De brede mand heeft geen dagen."
    text = (
        f"In de brede mand haalt {_pct(base['precision'])} van {int(base['n'])} "
        "dagen binnen zeven dagen een high van +50%. "
        f"De order daarop levert gemiddeld {_pct(base['mean'])}."
    )
    if peak["n"] > 0.0:
        text += (
            f" Na een kleine piek is dat {_pct(peak['precision'])} "
            f"op {int(peak['n'])} dagen, goed voor {_pct(peak['recall'])} van de stijgers, "
            f"en de order levert {_pct(peak['mean'])}."
        )
    if prior["n"] > 0.0:
        text += (
            f" Na een dag die al +10% sloot is de kans {_pct(prior['precision'])} "
            f"op {int(prior['n'])} dagen, goed voor {_pct(prior['recall'])} van de stijgers, "
            f"en de order levert {_pct(prior['mean'])}."
        )
    return text


def _flags(base: str) -> tuple[dict[int, float], set[int], bool]:
    path = BARS / f"{base}.json"
    if not path.exists():
        return {}, set(), False
    bars = _load_ohlc(path)
    if bars is None:
        return {}, set(), False
    ts, open_, high, low, close, volume = bars
    return (*intraday_flags(ts, open_, high, low, close, volume), True)


def main() -> None:
    print("daily", flush=True)
    panels = []
    for path in sorted(DAILY.glob("*.json")):
        ts, open_, high, close, quote = _load_daily(path)
        panels.append((path.stem, ts, open_, high, close, quote))
    totals: dict[int, float] = {}
    for _base, ts, _open, _high, _close, quote in panels:
        for stamp, day_quote in zip(ts, quote, strict=True):
            if day_quote > 0.0:
                totals[int(stamp)] = totals.get(int(stamp), 0.0) + float(day_quote)
    market = market_ratios(totals)
    print(f"coins {len(panels)} market days {len(market)}", flush=True)
    parts = []
    for i, (base, ts, open_, high, close, quote) in enumerate(panels, start=1):
        buy, peaks, has_15m = _flags(base)
        taker = _load_taker(FLOW / f"{base}.json")
        parts.append(
            coin_rows(
                base,
                ts,
                open_,
                high,
                close,
                quote,
                buy=buy,
                peaks=peaks,
                taker=taker,
                market=market,
                has_15m=has_15m,
            )
        )
        if i % 40 == 0:
            print(f"scanned {i}/{len(panels)}", flush=True)
    book = concat_books(parts)
    print(f"rows {len(book['entry_ms'])}", flush=True)
    _attach_news(book)
    _write(OUT, book)
    print(OUT.read_text())


if __name__ == "__main__":
    main()

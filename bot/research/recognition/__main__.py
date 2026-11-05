"""Price the peak-reclaim order and the buy-pressure scalp.

Usage::

    .venv/bin/python -m bot.research.recognition
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.recognition.engine import (
    PathMark,
    ScalpBook,
    concat_scalps,
    discount_bin,
    order_stats,
    path_stats,
    scan_paths,
    scan_scalps,
)
from bot.research.rise_pattern.engine import BUY_SHARE, flow_names
from bot.research.small_peak.__main__ import (
    BARS,
    MACRO,
    NEWS,
    NEWS_FROM,
    _load_ohlc,
    _ms,
    load_coins,
    load_market,
)
from bot.research.small_peak.engine import NEWS_CUT, labels_between, state_of, volume_bin
from bot.research.small_peak.volume import START_MS, market_quote

OUT = Path("bot/research/recognition/MARKS.md")
DISTANCES = (
    "entry boven het niveau",
    "0–1% eronder",
    "1–2% eronder",
    "vanaf 2% eronder",
)
VOLUMES = ("onder 1×", "1–2×", "vanaf 2×")


def _pct(value: float) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{100.0 * value:.1f}%"


def _num(value: float) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{value:.0f}"


def _year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, UTC).year


def _kind(marks: list[PathMark], kind: str) -> list[PathMark]:
    return [mark for mark in marks if mark.kind == kind]


def _order_row(name: str, marks: list[PathMark], field: str) -> str:
    stats = order_stats(np.asarray([getattr(mark, field) for mark in marks], dtype=np.float64))
    if stats.get("n", 0.0) <= 0.0:
        return f"| {name} | 0 | — | — |"
    return f"| {name} | {int(stats['n'])} | {_pct(stats['mean'])} | {_pct(stats['median'])} |"


def _path_row(name: str, marks: list[PathMark]) -> str:
    stats = path_stats(marks)
    if stats.get("n", 0.0) <= 0.0:
        return f"| {name} | 0 | — | — | — | — | — |"
    return (
        f"| {name} | {int(stats['n'])} | {_pct(stats['touch'])} | {_num(stats['bars'])} "
        f"| {_pct(stats['mae'])} | {_pct(stats['mfe'])} | {_pct(stats['discount'])} |"
    )


def _both(name: str, marks: list[PathMark], field: str) -> list[str]:
    return [
        _order_row(f"{name} piek", _kind(marks, "piek"), field),
        _order_row(f"{name} null", _kind(marks, "null"), field),
    ]


def _attach(
    marks: list[PathMark],
    coins: dict[str, tuple[np.ndarray, np.ndarray]],
    macro: tuple[np.ndarray, np.ndarray],
    news_from: int,
) -> list[PathMark]:
    market_ts, market_side = macro
    empty = (np.asarray([], dtype=np.int64), np.asarray([], dtype=np.int64))
    out: list[PathMark] = []
    for mark in marks:
        if mark.entry_ms < news_from or mark.kind not in {"piek", "null"}:
            out.append(mark)
            continue
        coin_ts, coin_side = coins.get(mark.base, empty)
        coin = state_of(labels_between(coin_ts, coin_side, mark.entry_ms), tie="quiet")
        tape = state_of(labels_between(market_ts, market_side, mark.entry_ms), tie="mixed")
        out.append(replace(mark, coin=coin, market=tape))
    return out


def _scalp_row(name: str, nets: np.ndarray, hits: np.ndarray | None = None) -> str:
    stats = order_stats(nets)
    if stats.get("n", 0.0) <= 0.0:
        return f"| {name} | 0 | — | — | — |"
    hit = "—" if hits is None else _pct(float(np.mean(hits)))
    return (
        f"| {name} | {int(stats['n'])} | {hit} | {_pct(stats['mean'])} | {_pct(stats['median'])} |"
    )


def _write(path: Path, marks: list[PathMark], scalps: ScalpBook) -> None:
    peaks = _kind(marks, "piek")
    nulls = _kind(marks, "null")
    head_path = [
        "| meting | n | raakt in 24u | mediaan kwartieren | dip tot verkoop | high 24u | afstand |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    head_order = [
        "| meting | n | gemiddeld | mediaan |",
        "|---|---:|---:|---:|",
    ]
    lines = [
        "# Herkenningspunten",
        "",
        "Twee punten stonden nog open. Het eerste is de kleine piek zelf:",
        "de order die verkoopt zodra de koers de piek weer aanraakt. Een bar",
        "die door de piek heen opent, vult op de open. Anders vult hij op de",
        "piek. Raakt de koers hem niet, dan is de verkoop de open na 4 uur",
        "of na 24 uur. De low van de vulbar telt mee in de dip. Bars na de",
        "verkoop tellen niet mee. De hoogste high over die 24 uur blijft een",
        "beschrijving van het pad.",
        "",
        "De null is dezelfde 8-uursklok zonder piek. Het niveau is dan de",
        "high van de 2 uur ervoor. De afstandsbakken zijn vastgezet: entry",
        "al boven het niveau, 0–1% eronder, 1–2% eronder, en vanaf 2%.",
        "Een hogere low heeft de low van de bevestigingsbar boven het midden",
        "van de aanloop. Coin- en marktvolume gebruiken de bakken onder 1×,",
        "1–2× en vanaf 2×. De gezamenlijke rij is een hogere low én",
        "coinvolume onder 1×.",
        "",
        "Het tweede punt is de koopdruk over 24 uur. Een aandeel van minstens",
        "65% up-bar-omzet raakte vaker een latere high, en de slotkoers gaf",
        "dat terug. De scalp verkoopt op +1% of op +2%, of na 4 uur en na",
        "24 uur als de koers daar niet komt. Koop, verkoop en de hele klok",
        "staan er alle drie in.",
        "",
        "Kosten zijn 0,25% taker per kant. Het pad moet 24 uur zonder gat",
        "zijn. Elke rij stond vast voor de run.",
        "",
        "## Het pad",
        "",
        *head_path,
        _path_row("piek", peaks),
        _path_row("null", nulls),
        "",
        "## Verkopen op het niveau",
        "",
        *head_order,
        _order_row("piek, 4 uur", peaks, "reclaim_4"),
        _order_row("null, 4 uur", nulls, "reclaim_4"),
        _order_row("piek, 24 uur", peaks, "reclaim_24"),
        _order_row("null, 24 uur", nulls, "reclaim_24"),
        _order_row("piek, 24 uur vasthouden", peaks, "hold_24"),
        _order_row("null, 24 uur vasthouden", nulls, "hold_24"),
        "",
        "## Afstand van de entry tot het niveau, verkoop na hoogstens 24 uur",
        "",
        *head_order,
    ]
    for label in DISTANCES:
        chosen = [mark for mark in marks if discount_bin(mark.discount) == label]
        lines += _both(label, chosen, "reclaim_24")
    lines += ["", "## Hogere low, alleen de piek, verkoop na hoogstens 24 uur", "", *head_order]
    higher = [mark for mark in peaks if mark.higher_low]
    lower = [mark for mark in peaks if not mark.higher_low]
    quiet_higher = [mark for mark in higher if volume_bin(mark.coin_vol) == "onder 1×"]
    lines.append(_order_row("hogere low", higher, "reclaim_24"))
    lines.append(_order_row("geen hogere low", lower, "reclaim_24"))
    lines.append(_order_row("hogere low en coinvolume onder 1×", quiet_higher, "reclaim_24"))
    lines += ["", "## Volume, verkoop na hoogstens 24 uur", "", *head_order]
    for label in VOLUMES:
        coin = [mark for mark in marks if volume_bin(mark.coin_vol) == label]
        tape = [mark for mark in marks if volume_bin(mark.market_vol) == label]
        lines += _both(f"coin {label}", coin, "reclaim_24")
        lines += _both(f"markt {label}", tape, "reclaim_24")
    lines += ["", "## Jaren, verkoop na hoogstens 24 uur", "", *head_order]
    for year in (2024, 2025, 2026):
        chosen = [mark for mark in marks if _year(mark.entry_ms) == year]
        lines += _both(str(year), chosen, "reclaim_24")
    news_from = _ms(NEWS_FROM)
    window = [mark for mark in marks if mark.entry_ms >= news_from]
    lines += [
        "",
        f"## Nieuws vanaf {NEWS_FROM}, verkoop na hoogstens 24 uur",
        "",
        "Stil of gelijkspel bij de coin telt als quiet. Gelijkspel bij de",
        "markt telt als mixed.",
        "",
        *head_order,
    ]
    for name, pred in (
        ("coin bull", lambda mark: mark.coin == "bull"),
        ("coin quiet", lambda mark: mark.coin == "quiet"),
        ("coin bear", lambda mark: mark.coin == "bear"),
        ("markt bull", lambda mark: mark.market == "bull"),
        ("markt bear", lambda mark: mark.market == "bear"),
    ):
        lines += _both(name, [mark for mark in window if pred(mark)], "reclaim_24")
    cut = _ms(NEWS_CUT)
    lines += [
        "",
        f"De knip op {NEWS_CUT} is de kalenderhelft van het nieuwsvenster.",
        "",
        *head_order,
        *_both(f"tot {NEWS_CUT}", [mark for mark in window if mark.entry_ms < cut], "reclaim_24"),
        *_both(
            f"vanaf {NEWS_CUT}",
            [mark for mark in window if mark.entry_ms >= cut],
            "reclaim_24",
        ),
        "",
        "## Koopdruk over 24 uur, scalp",
        "",
        "| meting | n | haalt het doel | gemiddeld | mediaan |",
        "|---|---:|---:|---:|---:|",
    ]
    names = flow_names(scalps.share, buy=BUY_SHARE)
    groups = (
        ("alle", np.ones(len(scalps), dtype=bool)),
        ("koop", names == "koop"),
        ("verkoop", names == "verkoop"),
        ("tussen", names == "tussen"),
    )
    orders = (
        ("vasthouden 4 uur", "hold_4", None),
        ("vasthouden 24 uur", "hold_24", None),
        ("+1% of 4 uur", "up1_4", "hit1_4"),
        ("+1% of 24 uur", "up1_24", "hit1_24"),
        ("+2% of 4 uur", "up2_4", "hit2_4"),
        ("+2% of 24 uur", "up2_24", "hit2_24"),
    )
    for group, mask in groups:
        for label, net_name, hit_name in orders:
            nets = getattr(scalps, net_name)[mask]
            hits = None if hit_name is None else getattr(scalps, hit_name)[mask]
            lines.append(_scalp_row(f"{group}, {label}", nets, hits))
    lines += ["", _closing(peaks, nulls), "", "Niets hiervan wordt live gezet.", ""]
    path.write_text("\n".join(lines))


def _closing(peaks: list[PathMark], nulls: list[PathMark]) -> str:
    peak = order_stats(np.asarray([mark.reclaim_24 for mark in peaks], dtype=np.float64))
    null = order_stats(np.asarray([mark.reclaim_24 for mark in nulls], dtype=np.float64))
    if peak.get("n", 0.0) <= 0.0 or null.get("n", 0.0) <= 0.0:
        return "De piek of de null heeft geen pad van 24 uur."
    return (
        "Verkopen zodra de piek geraakt wordt, levert over hoogstens 24 uur "
        f"gemiddeld {_pct(peak['mean'])} op {int(peak['n'])} pieken. "
        f"Dezelfde order op de null levert {_pct(null['mean'])} op {int(null['n'])} klokken."
    )


def main() -> None:
    files = sorted(BARS.glob("*.json"))
    print("market volume", flush=True)
    market = market_quote(files)
    marks: list[PathMark] = []
    parts: list[ScalpBook] = []
    print("paths", flush=True)
    for i, path in enumerate(files, start=1):
        bars = _load_ohlc(path)
        if bars is None:
            continue
        ts, open_, high, low, close, volume = bars
        marks.extend(
            scan_paths(path.stem, ts, open_, high, low, close, volume, market, start_ms=START_MS)
        )
        parts.append(scan_scalps(ts, open_, high, low, close, volume))
        if i % 40 == 0:
            print(f"scanned {i}/{len(files)}", flush=True)
    print("news", flush=True)
    marks = _attach(marks, load_coins(NEWS), load_market(MACRO), _ms(NEWS_FROM))
    scalps = concat_scalps(parts)
    _write(OUT, marks, scalps)
    print(OUT.read_text())


if __name__ == "__main__":
    main()

"""Scan the 15-minute tape for small peaks and their overshoot.

Usage::

    .venv/bin/python -m bot.research.small_peak
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.alphai_sentiment_accuracy.score import side_of
from bot.research.news_strategy_match.engine import market_side
from bot.research.small_peak.engine import (
    HORIZONS,
    NEWS_CUT,
    PRIMARY,
    SPECS,
    Peak,
    attach_news,
    measure_peak,
    null_frame,
    prepare,
    select_peaks,
    summarize,
    summarize_columns,
)

BARS = Path("data/research/alphai_sleeve/15m")
NEWS = Path("data/research/alphai_crypto_news.json")
MACRO = Path("data/research/alphai_macro_news.json")
OUT = Path("bot/research/small_peak/RESULTS.md")
NEWS_FROM = "2026-08-07"
H24 = 1


def _ms(day: str) -> int:
    stamp = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(stamp.timestamp() * 1000)


def _day(published: str) -> tuple[int, str] | None:
    try:
        stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    stamp = stamp.astimezone(UTC)
    return int(stamp.timestamp() * 1000), stamp.date().isoformat()


def _base(ticker: str) -> str:
    text = str(ticker or "").strip().upper()
    if text.endswith("-USD"):
        return text[: -len("-USD")]
    return text


def load_coins(path: Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    raw = json.loads(path.read_text())
    seen: set[tuple[str, str]] = set()
    bucket: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in raw.get("rows") or []:
        parsed = _day(str(row.get("published") or ""))
        if parsed is None:
            continue
        ms, _date = parsed
        uid = str(row.get("uid") or "")
        for ticker, sentiment in (row.get("sentiments") or {}).items():
            base = _base(str(ticker))
            if not base or (uid, base) in seen:
                continue
            seen.add((uid, base))
            side = side_of(str(sentiment))
            if side is None or side == 0:
                continue
            bucket[base].append((ms, side))
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for base, pairs in bucket.items():
        pairs.sort()
        out[base] = (
            np.asarray([ms for ms, _side in pairs], dtype=np.int64),
            np.asarray([side for _ms, side in pairs], dtype=np.int64),
        )
    return out


def load_market(path: Path) -> tuple[np.ndarray, np.ndarray]:
    if not path.exists():
        return np.asarray([], dtype=np.int64), np.asarray([], dtype=np.int64)
    raw = json.loads(path.read_text())
    pairs: list[tuple[int, int]] = []
    for row in raw.get("rows") or []:
        parsed = _day(str(row.get("published") or ""))
        side = market_side(str(row.get("alignment") or ""))
        if parsed is None or side is None or side == 0:
            continue
        pairs.append((parsed[0], side))
    pairs.sort()
    return (
        np.asarray([ms for ms, _side in pairs], dtype=np.int64),
        np.asarray([side for _ms, side in pairs], dtype=np.int64),
    )


def _load_ohlc(path: Path) -> tuple[np.ndarray, ...] | None:
    rows = json.loads(path.read_text())
    if len(rows) < 800:
        return None
    return (
        np.asarray([int(row[0]) for row in rows], dtype=np.int64),
        np.asarray([float(row[1]) for row in rows], dtype=np.float64),
        np.asarray([float(row[2]) for row in rows], dtype=np.float64),
        np.asarray([float(row[3]) for row in rows], dtype=np.float64),
        np.asarray([float(row[4]) for row in rows], dtype=np.float64),
        np.asarray([float(row[5]) for row in rows], dtype=np.float64),
    )


def scan_coin(
    bars: tuple[np.ndarray, ...],
) -> tuple[dict[str, list[Peak]], tuple[np.ndarray, np.ndarray, np.ndarray]]:
    ts, open_, high, low, close, volume = bars
    frame = null_frame(ts, open_, high, close, volume)
    prepared = prepare(high, low, close, volume)
    found: dict[str, list[Peak]] = {spec.name: [] for spec in SPECS}
    if prepared is None:
        return found, frame
    idx, rise, pullback = prepared
    for spec in SPECS:
        for peak_i in select_peaks(idx, rise, pullback, spec):
            peak = measure_peak(ts, open_, high, int(peak_i), float(high[int(peak_i)]))
            if peak is not None:
                found[spec.name].append(peak)
    return found, frame


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _row(name: str, stats: dict[str, float]) -> str:
    if stats.get("n", 0.0) <= 0.0:
        return f"| {name} | 0 | — | — | — | — | — | — | — |"
    return (
        f"| {name} | {int(stats['n'])} | {_pct(stats['p_0.0'])} | {_pct(stats['p_0.01'])} "
        f"| {_pct(stats['p_0.03'])} | {_pct(stats['p_0.06'])} "
        f"| {_pct(stats['median_overshoot'])} | {_pct(stats['median_if_shot'])} "
        f"| {_pct(stats['mean_net'])} |"
    )


def _head() -> list[str]:
    return [
        "| meting | n | door piek | +1% | +3% | +6% | mediaan | mediaan als piek | gem. net |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]


def _year(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y")


def _write(
    path: Path,
    *,
    by_spec: dict[str, list[Peak]],
    null_ms: np.ndarray,
    null_over: np.ndarray,
    null_net: np.ndarray,
    news_peaks: list[Peak],
    null_news: list[Peak],
    n_coins: int,
) -> None:
    primary = by_spec[PRIMARY.name]
    cut = _ms(NEWS_CUT)
    lines = [
        "# Kleine piek en doorschietpercentage",
        "",
        "De vorm stond vast voordat de tape gedraaid werd. Er is daarna geen",
        "drempel bijgesteld, en er is niets live gezet.",
        "",
        "Een kleine piek op de 15-minuten-tape is bevestigd als het hoogste punt",
        "van de laatste 2 uur achter ons ligt: de volgende bar neemt dat hoog",
        "niet uit, en de slotkoers staat 0,4% tot 2,5% onder dat hoog, met",
        "hoogstens 70% van de stijging teruggegeven. De stijging vanuit het",
        "laagste punt van de 4 uur ervoor valt in een band. De hoofdmeting is",
        "1,5% tot 6%. De buren 1% tot 8% en 2% tot 4% staan erbij. Een coin",
        "wacht daarna 4 uur op het volgende signaal, en het gemiddelde",
        "dagvolume is minstens €20.000. De koop is de eerstvolgende opening.",
        "",
        "Doorschieten is `later hoog / piekhoog − 1` over 4 uur, 24 uur en",
        "7 dagen. Een long koopt die opening en verkoopt de opening aan het",
        "einde van de horizon, met 0,25% taker per kant. De null is dezelfde",
        "klok zonder piek: elke 8 uur, gemeten tegen het hoog van de 2 uur",
        f"ervoor. {n_coins} coins met een 15-minuten-bestand.",
        "",
        "## Hoofdvorm, hele tape",
        "",
        *_head(),
    ]
    for i, (label, _bars) in enumerate(HORIZONS):
        lines.append(_row(f"piek {label}", summarize(primary, i)))
        lines.append(_row(f"null {label}", summarize_columns(null_over, null_net, i)))
    target = summarize(primary, H24)
    lines += [
        "",
        "De kolom door piek is het aandeel waarin de koers het piekhoog weer",
        "raakt of passeert. Mediaan is de doorschiet over alle pieken, ook de",
        "pieken die niet gehaald worden. Mediaan als piek is alleen de keren",
        "dat het hoog wel gehaald werd.",
        "",
        "Een verkooporder op 3% dóór de piek, anders na 24 uur, levert op de",
        f"hoofdvorm een gemiddeld net van {_pct(target.get('target_net', 0.0))} "
        f"over {int(target.get('n', 0))} pieken. Vasthouden tot de 24-uurs-opening",
        f"levert {_pct(target.get('mean_net', 0.0))}.",
        "",
        "## Drie breedtes, 24 uur",
        "",
        *_head(),
    ]
    for spec in SPECS:
        lines.append(_row(spec.name, summarize(by_spec[spec.name], H24)))
    lines.append(_row("null", summarize_columns(null_over, null_net, H24)))
    lines += ["", "## Jaren, hoofdvorm, 24 uur", "", *_head()]
    for year in ("2024", "2025", "2026"):
        chosen = [peak for peak in primary if _year(peak.entry_ms) == year]
        mask = np.asarray([_year(int(ms)) == year for ms in null_ms])
        lines.append(_row(f"piek {year}", summarize(chosen, H24)))
        lines.append(_row(f"null {year}", summarize_columns(null_over[mask], null_net[mask], H24)))
    lines += [
        "",
        "## Met AlphaI-nieuws",
        "",
        f"Alleen pieken met een entry vanaf {NEWS_FROM}, het begin van het",
        "macro-archief. Het coin-label en de macro-stem zijn de meerderheid",
        "van de directionele headlines in de 24 uur vóór de entry. Een headline",
        "op of na de entry telt niet. Stil is geen meerderheid.",
        "",
        *_head(),
    ]
    for label, group in _news_groups(news_peaks):
        lines.append(_row(label, summarize(group, H24)))
    before = [peak for peak in news_peaks if peak.entry_ms < cut]
    after = [peak for peak in news_peaks if peak.entry_ms >= cut]
    lines += [
        "",
        f"De knip op {NEWS_CUT} is de kalenderhelft van het nieuwsvenster,",
        "niet gekozen op een uitkomst.",
        "",
        *_head(),
        _row(f"tot {NEWS_CUT}", summarize(before, H24)),
        _row(f"vanaf {NEWS_CUT}", summarize(after, H24)),
        "",
        "De null in hetzelfde venster, met dezelfde nieuwsstem. Zo is te zien",
        "of de stem de doorschiet verandert zonder dat er een kleine piek was.",
        "",
        *_head(),
    ]
    null_groups = _news_groups(null_news, "alle null-entries in het nieuwsvenster")
    for label, group in null_groups:
        lines.append(_row(label, summarize(group, H24)))
    lines += [
        "",
        "Niets hiervan wordt live gezet.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _news_groups(
    peaks: list[Peak],
    title: str = "alle pieken in het nieuwsvenster",
) -> list[tuple[str, list[Peak]]]:
    groups = [
        (title, peaks),
        ("coin bull", [p for p in peaks if p.coin == "bull"]),
        ("coin bear", [p for p in peaks if p.coin == "bear"]),
        ("coin stil", [p for p in peaks if p.coin == "quiet"]),
        ("markt bull", [p for p in peaks if p.market == "bull"]),
        ("markt bear", [p for p in peaks if p.market == "bear"]),
        ("markt gemengd", [p for p in peaks if p.market == "mixed"]),
        ("markt stil", [p for p in peaks if p.market == "quiet"]),
        ("coin bull en markt bull", [p for p in peaks if p.coin == "bull" and p.market == "bull"]),
        ("coin bull en markt bear", [p for p in peaks if p.coin == "bull" and p.market == "bear"]),
        ("coin bear en markt bull", [p for p in peaks if p.coin == "bear" and p.market == "bull"]),
        ("coin bear en markt bear", [p for p in peaks if p.coin == "bear" and p.market == "bear"]),
    ]
    return groups


def main() -> None:
    coins = load_coins(NEWS)
    market = load_market(MACRO)
    news_from = _ms(NEWS_FROM)
    by_spec: dict[str, list[Peak]] = {spec.name: [] for spec in SPECS}
    bases_for: dict[str, list[str]] = {spec.name: [] for spec in SPECS}
    null_ms: list[np.ndarray] = []
    null_over: list[np.ndarray] = []
    null_net: list[np.ndarray] = []
    null_news_rows: list[Peak] = []
    null_news_bases: list[str] = []
    n_coins = 0
    files = sorted(BARS.glob("*.json"))
    for i, path in enumerate(files, start=1):
        bars = _load_ohlc(path)
        if bars is None:
            continue
        n_coins += 1
        found, frame = scan_coin(bars)
        base = path.stem
        for spec in SPECS:
            by_spec[spec.name].extend(found[spec.name])
            bases_for[spec.name].extend([base] * len(found[spec.name]))
        if len(frame[0]):
            null_ms.append(frame[0])
            null_over.append(frame[1])
            null_net.append(frame[2])
            chosen = frame[0] >= news_from
            for pos in np.flatnonzero(chosen):
                null_news_rows.append(
                    Peak(
                        int(frame[0][pos]),
                        (
                            float(frame[1][pos, 0]),
                            float(frame[1][pos, 1]),
                            float(frame[1][pos, 2]),
                        ),
                        (
                            float(frame[2][pos, 0]),
                            float(frame[2][pos, 1]),
                            float(frame[2][pos, 2]),
                        ),
                        0.0,
                    )
                )
                null_news_bases.append(base)
        if i % 40 == 0:
            print(f"scanned {i}/{len(files)}", flush=True)
    ms = np.concatenate(null_ms) if null_ms else np.asarray([], dtype=np.int64)
    over = np.concatenate(null_over) if null_over else np.zeros((0, 3))
    held = np.concatenate(null_net) if null_net else np.zeros((0, 3))
    primary = by_spec[PRIMARY.name]
    in_news = [
        (peak, base)
        for peak, base in zip(primary, bases_for[PRIMARY.name], strict=True)
        if peak.entry_ms >= news_from
    ]
    news_peaks = attach_news(
        [peak for peak, _base in in_news],
        coins,
        market,
        [base for _peak, base in in_news],
    )
    null_news = attach_news(null_news_rows, coins, market, null_news_bases)
    _write(
        OUT,
        by_spec=by_spec,
        null_ms=ms,
        null_over=over,
        null_net=held,
        news_peaks=news_peaks,
        null_news=null_news,
        n_coins=n_coins,
    )
    print(OUT.read_text())


if __name__ == "__main__":
    main()

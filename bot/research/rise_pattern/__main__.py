"""Scan buy pressure, market volume and AlphaI news before a rise.

Usage::

    .venv/bin/python -m bot.research.rise_pattern
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from bot.research.rise_pattern.engine import (
    BUY_NEIGHBOURS,
    BUY_SHARE,
    RISE_3,
    RISE_6,
    Clock,
    concat_clocks,
    flow_names,
    market_bin_names,
    measure_clock,
    signal_rate,
    slice_clock,
    trade_stats,
)
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
from bot.research.small_peak.engine import NEWS_CUT, labels_between, state_of
from bot.research.small_peak.volume import START_MS, market_quote

OUT = Path("bot/research/rise_pattern/RISE.md")


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _row(name: str, stats: dict[str, float]) -> str:
    if stats.get("n", 0.0) <= 0.0:
        return f"| {name} | 0 | — | — | — |"
    return (
        f"| {name} | {int(stats['n'])} | {_pct(stats['p_rise'])} "
        f"| {_pct(stats['mean_net'])} | {_pct(stats['median_net'])} |"
    )


def _head() -> list[str]:
    return [
        "| meting | n | stijgt +3% | gem. netto | mediaan netto |",
        "|---|---:|---:|---:|---:|",
    ]


def _stats(clock: Clock, mask: np.ndarray, *, hours: int) -> dict[str, float]:
    if hours == 4:
        return trade_stats(clock.rise_4h[mask], clock.net_4h[mask])
    return trade_stats(clock.rise_24h[mask], clock.net_24h[mask])


def _rate(left: float, right: float) -> str:
    if not np.isfinite(left) or not np.isfinite(right):
        return "— | —"
    return f"{_pct(left)} | {_pct(right)}"


def scan(market: np.ndarray) -> tuple[Clock, Clock, np.ndarray]:
    parts: list[Clock] = []
    news_parts: list[Clock] = []
    bases: list[np.ndarray] = []
    news_from = _ms(NEWS_FROM)
    files = sorted(BARS.glob("*.json"))
    for i, path in enumerate(files, start=1):
        bars = _load_ohlc(path)
        if bars is None:
            continue
        ts, open_, high, _low, close, volume = bars
        clock = measure_clock(ts, open_, high, close, volume, market, start_ms=START_MS)
        if len(clock) == 0:
            continue
        parts.append(clock)
        keep = clock.ms >= news_from
        if keep.any():
            news_parts.append(slice_clock(clock, keep))
            bases.append(np.full(int(keep.sum()), path.stem, dtype=object))
        if i % 40 == 0:
            print(f"scanned {i}/{len(files)}", flush=True)
    news = concat_clocks(news_parts)
    names = np.concatenate(bases) if bases else np.asarray([], dtype=object)
    return concat_clocks(parts), news, names


def _states(
    clock: Clock,
    bases: np.ndarray,
    coins: dict[str, tuple[np.ndarray, np.ndarray]],
    market: tuple[np.ndarray, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    market_ts, market_side = market
    empty = (np.asarray([], dtype=np.int64), np.asarray([], dtype=np.int64))
    coin_state = np.empty(len(clock), dtype=object)
    tape_state = np.empty(len(clock), dtype=object)
    for i, (stamp, base) in enumerate(zip(clock.ms, bases, strict=True)):
        coin_ts, coin_side = coins.get(str(base), empty)
        coin_state[i] = state_of(labels_between(coin_ts, coin_side, int(stamp)), tie="quiet")
        tape_state[i] = state_of(
            labels_between(market_ts, market_side, int(stamp)),
            tie="mixed",
        )
    return coin_state, tape_state


def _flow_table(clock: Clock, flow: np.ndarray, market: np.ndarray, *, hours: int) -> list[str]:
    druk = market == "vanaf 2×"
    rustig = market == "onder 1×"
    midden = market == "1–2×"
    rows = [
        ("alle klokken", np.ones(len(clock), dtype=bool)),
        ("koop", flow == "koop"),
        ("verkoop", flow == "verkoop"),
        ("tussen", flow == "tussen"),
        ("koop en markt onder 1×", (flow == "koop") & rustig),
        ("koop en markt 1–2×", (flow == "koop") & midden),
        ("koop en markt vanaf 2×", (flow == "koop") & druk),
        ("verkoop en markt vanaf 2×", (flow == "verkoop") & druk),
        ("tussen en markt vanaf 2×", (flow == "tussen") & druk),
    ]
    return [_row(name, _stats(clock, mask, hours=hours)) for name, mask in rows]


def _write(
    path: Path,
    clock: Clock,
    news: Clock,
    coin: np.ndarray,
    market_state: np.ndarray,
) -> None:
    flow = flow_names(clock.share_2h, buy=BUY_SHARE)
    flow_day = flow_names(clock.share_24h, buy=BUY_SHARE)
    tape = market_bin_names(clock.market)
    years = clock.ms.astype("datetime64[ms]").astype("datetime64[Y]").astype(int) + 1970
    lines = [
        "# Patroon vóór een stijging",
        "",
        "De 15-minutenkaarsen van Bitvavo hebben geen losse koop- en",
        "verkooporders. Koopdruk is de euro-omzet van kwartieren die hoger",
        "sloten, gedeeld door de omzet van kwartieren die hoger of lager",
        "sloten. Een doji telt niet mee. Het venster eindigt op het kwartier",
        "vóór de entry.",
        "",
        "Het hoofdvenster is 2 uur. 24 uur koopdruk is de buur en staat apart.",
        "Koop is een aandeel vanaf 65%. De buren 60% en 70% staan erbij, met",
        "verkoop als spiegel. Marktvolume is de euro-omzet van alle coins in",
        "die 2 uur, gedeeld door het eigen gemiddelde 2-uursblok van de dag",
        "ervoor. De bakken zijn onder 1×, 1–2× en vanaf 2×.",
        "",
        "De klok is elke 8 uur. De long koopt die open en verkoopt de open",
        "4 uur of 24 uur later, met 0,25% taker per kant. Het pad van 24 uur",
        "heeft geen gat, en de coin deed de dag ervoor gemiddeld minstens",
        "€20.000 omzet per kwartier. Een stijger haalt in die 24 uur een high",
        "van minstens 3% boven de entry. De grens van 6% staat alleen in de",
        "beschrijvende tabel.",
        "",
        "AlphaI-coin en marktnieuws zijn de meerderheid in de 24 uur vóór de",
        "entry. Een kop op het entry-tijdstip telt niet. Het volle patroon is",
        "koop, marktvolume vanaf 2×, een bullish coin en een bullish markt.",
        "Elke rij hieronder stond vast voor de run.",
        "",
        "## 24 uur, koopdruk over 2 uur",
        "",
        *_head(),
        *_flow_table(clock, flow, tape, hours=24),
        "",
        "## 24 uur, koopdruk over 24 uur",
        "",
        *_head(),
    ]
    day_rows = [
        ("koop", flow_day == "koop"),
        ("verkoop", flow_day == "verkoop"),
        ("koop en markt vanaf 2×", (flow_day == "koop") & (tape == "vanaf 2×")),
    ]
    lines += [_row(name, _stats(clock, mask, hours=24)) for name, mask in day_rows]
    lines += ["", "## Buren van de 65%-grens, 2 uur koopdruk, 24 uur vastgehouden", ""]
    lines += _head()
    for buy in BUY_NEIGHBOURS:
        names = flow_names(clock.share_2h, buy=buy)
        label = f"{int(round(100 * buy))}%"
        lines.append(_row(f"koop vanaf {label}", _stats(clock, names == "koop", hours=24)))
        both = (names == "koop") & (tape == "vanaf 2×")
        lines.append(_row(f"koop vanaf {label} en markt vanaf 2×", _stats(clock, both, hours=24)))
    lines += ["", "## Jaren, 24 uur", ""]
    lines += _head()
    druk_koop = (flow == "koop") & (tape == "vanaf 2×")
    for year in (2024, 2025, 2026):
        year_mask = years == year
        lines.append(_row(f"{year} alle", _stats(clock, year_mask, hours=24)))
        lines.append(_row(f"{year} koop", _stats(clock, year_mask & (flow == "koop"), hours=24)))
        lines.append(
            _row(f"{year} koop en markt vanaf 2×", _stats(clock, year_mask & druk_koop, hours=24))
        )
    risers = clock.rise_24h >= RISE_3
    big = clock.rise_24h >= RISE_6
    lines += [
        "",
        "## Zitten de signalen vaker bij de stijgers?",
        "",
        "Het aandeel van de klokken dat het signaal al had, apart voor paden",
        "die daarna 3% of 6% stijgen en voor de rest.",
        "",
        "| signaal | bij +3% | bij de rest | bij +6% | bij de rest van +6% |",
        "|---|---:|---:|---:|---:|",
    ]
    descriptive = [
        ("koop", flow == "koop"),
        ("verkoop", flow == "verkoop"),
        ("markt vanaf 2×", tape == "vanaf 2×"),
        ("koop en markt vanaf 2×", druk_koop),
    ]
    for name, mask in descriptive:
        left, rest = signal_rate(mask, risers)
        high, high_rest = signal_rate(mask, big)
        lines.append(f"| {name} | {_rate(left, rest)} | {_rate(high, high_rest)} |")
    share_ok = np.isfinite(clock.share_2h)
    market_ok = np.isfinite(clock.market)
    buy_up = float(np.nanmean(clock.share_2h[risers & share_ok]))
    buy_rest = float(np.nanmean(clock.share_2h[~risers & share_ok]))
    vol_up = float(np.nanmean(clock.market[risers & market_ok]))
    vol_rest = float(np.nanmean(clock.market[~risers & market_ok]))
    lines += [
        "",
        f"Gemiddelde 2-uurs koopdruk bij +3%: {_pct(buy_up)} tegen {_pct(buy_rest)} bij de rest.",
        f"Gemiddelde marktvolume-ratio bij +3%: {vol_up:.2f}× tegen {vol_rest:.2f}× bij de rest.",
        "",
        f"## Nieuwsvenster vanaf {NEWS_FROM}, 24 uur",
        "",
        "Coin- en marktstemming komen uit AlphaI. Stil of gelijkspel bij de",
        "coin telt als quiet. Gelijkspel bij de markt telt als mixed.",
        "",
        *_head(),
    ]
    news_flow = flow_names(news.share_2h, buy=BUY_SHARE)
    news_tape = market_bin_names(news.market)
    koop = news_flow == "koop"
    verkoop = news_flow == "verkoop"
    druk = news_tape == "vanaf 2×"
    coin_bull = coin == "bull"
    tape_bull = market_state == "bull"
    full = koop & druk & coin_bull & tape_bull
    news_rows = [
        ("alle klokken in het venster", np.ones(len(news), dtype=bool)),
        ("koop", koop),
        ("markt vanaf 2×", druk),
        ("coin bull", coin_bull),
        ("markt bull", tape_bull),
        ("koop en markt vanaf 2×", koop & druk),
        ("koop en coin bull", koop & coin_bull),
        ("koop en markt bull", koop & tape_bull),
        ("koop, markt vanaf 2× en coin bull", koop & druk & coin_bull),
        ("koop, markt vanaf 2× en markt bull", koop & druk & tape_bull),
        ("koop, coin bull en markt bull", koop & coin_bull & tape_bull),
        ("vol patroon", full),
        (
            "verkoop, markt vanaf 2×, coin bull en markt bull",
            verkoop & druk & coin_bull & tape_bull,
        ),
    ]
    lines += [_row(name, _stats(news, mask, hours=24)) for name, mask in news_rows]
    cut = _ms(NEWS_CUT)
    lines += [
        "",
        f"De knip op {NEWS_CUT} is de kalenderhelft van het nieuwsvenster.",
        "",
        *_head(),
        _row(f"vol patroon tot {NEWS_CUT}", _stats(news, full & (news.ms < cut), hours=24)),
        _row(f"vol patroon vanaf {NEWS_CUT}", _stats(news, full & (news.ms >= cut), hours=24)),
        "",
        "## 4 uur vastgehouden",
        "",
        *_head(),
        *_flow_table(clock, flow, tape, hours=4),
        _row("vol patroon", _stats(news, full, hours=4)),
        "",
    ]
    full_stats = _stats(news, full, hours=24)
    base_stats = _stats(news, np.ones(len(news), dtype=bool), hours=24)
    if full_stats.get("n", 0.0) <= 0.0 or base_stats.get("n", 0.0) <= 0.0:
        lines.append("Het volle patroon heeft in dit venster geen enkele klok.")
    else:
        lines.append(
            "Het volle patroon stijgt over 24 uur in "
            f"{_pct(full_stats['p_rise'])} van {int(full_stats['n'])} klokken, "
            f"tegen {_pct(base_stats['p_rise'])} van alle klokken in het nieuwsvenster. "
            f"Het gemiddelde netto is {_pct(full_stats['mean_net'])} "
            f"tegen {_pct(base_stats['mean_net'])}."
        )
    lines += ["", "Niets hiervan wordt live gezet.", ""]
    path.write_text("\n".join(lines))


def main() -> None:
    files = sorted(BARS.glob("*.json"))
    print("market volume", flush=True)
    market = market_quote(files)
    print("clocks", flush=True)
    clock, news, bases = scan(market)
    print(f"clocks {len(clock)} news {len(news)}", flush=True)
    coins = load_coins(NEWS)
    macro = load_market(MACRO)
    coin, tape = _states(news, bases, coins, macro)
    _write(OUT, clock, news, coin, tape)
    print(OUT.read_text())


if __name__ == "__main__":
    main()

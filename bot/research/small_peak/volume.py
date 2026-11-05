"""Split the small peak by coin volume and by Bitvavo market volume.

The earlier pass used volume only as a floor of €20,000 a day. Here the
same primary peak is split on two ratios, both known at the peak's close:

* coin volume — that coin's euro turnover in the 2-hour peak, divided by
  its own average 2-hour block over the prior day
* market volume — the same ratio for the sum of every coin on this tape

Bins are under 1×, 1–2× and from 2×. Two joint rows are locked as well:
both under 1×, and both from 2×. The null clock is split the same way.
Nothing is armed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from bot.research.small_peak.__main__ import BARS, _load_ohlc, _pct, _row
from bot.research.small_peak.engine import (
    BAR_MS,
    PRIMARY,
    Peak,
    measure_peak,
    null_frame,
    prepare,
    select_peaks,
    summarize,
    summarize_columns,
    volume_bin,
    volume_ratio,
)

OUT = Path("bot/research/small_peak/VOLUME.md")
START_MS = 1_704_067_200_000
N_SLOTS = 110_000
BINS = ("onder 1×", "1–2×", "vanaf 2×")
H4 = 0
H24 = 1


def market_quote(files: list[Path]) -> np.ndarray:
    """Euro turnover per 15-minute slot, summed across the tape."""
    acc = np.zeros(N_SLOTS, dtype=np.float64)
    for path in files:
        bars = _load_ohlc(path)
        if bars is None:
            continue
        ts, _open, _high, _low, close, volume = bars
        slot = (ts - START_MS) // BAR_MS
        ok = (slot >= 0) & (slot < N_SLOTS)
        np.add.at(acc, slot[ok], close[ok] * volume[ok])
    return acc


def _ratios_at(
    quote: np.ndarray, ts: np.ndarray, end_index: np.ndarray, market: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    coin = volume_ratio(quote, end_index)
    slot = (ts[end_index] - START_MS) // BAR_MS
    inside = (slot >= 0) & (slot < len(market))
    tape = np.full(len(end_index), np.nan)
    if inside.any():
        tape[inside] = volume_ratio(market, slot[inside])
    return coin, tape


def scan(
    market: np.ndarray,
) -> tuple[list[Peak], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    peaks: list[Peak] = []
    null_ms: list[np.ndarray] = []
    null_over: list[np.ndarray] = []
    null_net: list[np.ndarray] = []
    null_coin: list[np.ndarray] = []
    null_tape: list[np.ndarray] = []
    files = sorted(BARS.glob("*.json"))
    for i, path in enumerate(files, start=1):
        bars = _load_ohlc(path)
        if bars is None:
            continue
        ts, open_, high, low, close, volume = bars
        quote = close * volume
        frame = null_frame(ts, open_, high, close, volume)
        if len(frame[0]):
            end_ms = frame[0] - BAR_MS
            end_i = np.searchsorted(ts, end_ms, side="left")
            hit = (end_i < len(ts)) & (ts[np.minimum(end_i, len(ts) - 1)] == end_ms)
            coin = np.full(len(frame[0]), np.nan)
            tape = np.full(len(frame[0]), np.nan)
            if hit.any():
                coin_hit, tape_hit = _ratios_at(quote, ts, end_i[hit], market)
                coin[hit] = coin_hit
                tape[hit] = tape_hit
            null_ms.append(frame[0])
            null_over.append(frame[1])
            null_net.append(frame[2])
            null_coin.append(coin)
            null_tape.append(tape)
        prepared = prepare(high, low, close, volume)
        if prepared is not None:
            idx, rise, pullback = prepared
            chosen = select_peaks(idx, rise, pullback, PRIMARY)
            if len(chosen):
                coin, tape = _ratios_at(quote, ts, chosen, market)
                for peak_i, coin_vol, tape_vol in zip(chosen, coin, tape, strict=True):
                    i_peak = int(peak_i)
                    measured = measure_peak(
                        ts,
                        open_,
                        high,
                        i_peak,
                        float(high[i_peak]),
                        coin_vol=float(coin_vol),
                        market_vol=float(tape_vol),
                    )
                    if measured is not None:
                        peaks.append(measured)
        if i % 40 == 0:
            print(f"scanned {i}/{len(files)}", flush=True)
    return (
        peaks,
        np.concatenate(null_ms) if null_ms else np.asarray([], dtype=np.int64),
        np.concatenate(null_over) if null_over else np.zeros((0, 3)),
        np.concatenate(null_net) if null_net else np.zeros((0, 3)),
        np.column_stack(
            [
                np.concatenate(null_coin) if null_coin else np.asarray([]),
                np.concatenate(null_tape) if null_tape else np.asarray([]),
            ]
        )
        if null_coin
        else np.zeros((0, 2)),
    )


def _mask(peaks: list[Peak], name: str) -> list[Peak]:
    if name == "beide onder 1×":
        return [
            p
            for p in peaks
            if volume_bin(p.coin_vol) == "onder 1×" and volume_bin(p.market_vol) == "onder 1×"
        ]
    if name == "beide vanaf 2×":
        return [
            p
            for p in peaks
            if volume_bin(p.coin_vol) == "vanaf 2×" and volume_bin(p.market_vol) == "vanaf 2×"
        ]
    if name.startswith("coin "):
        return [p for p in peaks if volume_bin(p.coin_vol) == name.removeprefix("coin ")]
    return [p for p in peaks if volume_bin(p.market_vol) == name.removeprefix("markt ")]


def _null_mask(ratios: np.ndarray, name: str) -> np.ndarray:
    coin = np.array([volume_bin(float(v)) for v in ratios[:, 0]])
    tape = np.array([volume_bin(float(v)) for v in ratios[:, 1]])
    if name == "beide onder 1×":
        return (coin == "onder 1×") & (tape == "onder 1×")
    if name == "beide vanaf 2×":
        return (coin == "vanaf 2×") & (tape == "vanaf 2×")
    if name.startswith("coin "):
        return coin == name.removeprefix("coin ")
    return tape == name.removeprefix("markt ")


def _groups() -> tuple[str, ...]:
    names = [f"coin {label}" for label in BINS]
    names += [f"markt {label}" for label in BINS]
    names += ["beide onder 1×", "beide vanaf 2×"]
    return tuple(names)


def _write(
    path: Path,
    peaks: list[Peak],
    null_over: np.ndarray,
    null_net: np.ndarray,
    null_ratio: np.ndarray,
) -> None:
    lines = [
        "# Volume bij de kleine piek",
        "",
        "De vorige meting gebruikte volume alleen als een drempel: een coin",
        "met minder dan €20.000 gemiddelde dagomzet viel af. Of de piek op",
        "drukke of stille handel kwam, werd niet gesplitst. Dat gebeurt hier.",
        "",
        "Twee verhoudingen, allebei bekend op de slotkoers van de piek. De",
        "teller is de euro-omzet van die 2 uur. De noemer is het gemiddelde",
        "2-uursblok van de dag ervoor, zonder de piek zelf.",
        "",
        "- **Coinvolume** is de omzet van die coin.",
        "- **Marktvolume** is de som van de euro-omzet van alle coins op deze",
        "  15-minuten-tape in hetzelfde kwartier.",
        "",
        "De bakken zijn onder 1×, 1–2× en vanaf 2×. Daarnaast beide onder 1×",
        "en beide vanaf 2×. De null is dezelfde klok zonder piek, in dezelfde",
        "bak. Kosten blijven 0,25% taker per kant. De piekvorm is de hoofdmeting",
        "van 1,5% tot 6%.",
        "",
    ]
    for horizon, title in ((H24, "24 uur"), (H4, "4 uur")):
        lines += [
            f"## {title}",
            "",
            "| meting | n | door piek | +1% | +3% | +6% | mediaan | mediaan als piek | gem. net |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for name in _groups():
            chosen = _mask(peaks, name)
            lines.append(_row(f"piek {name}", summarize(chosen, horizon)))
            keep = _null_mask(null_ratio, name) if len(null_ratio) else np.asarray([], dtype=bool)
            lines.append(
                _row(
                    f"null {name}",
                    summarize_columns(null_over[keep], null_net[keep], horizon),
                )
            )
        lines.append("")
    heavy = _mask(peaks, "beide vanaf 2×")
    quiet = _mask(peaks, "beide onder 1×")
    lines += [
        "Een long op een piek met beide volumes vanaf 2× levert over 24 uur",
        f"gemiddeld {_pct(summarize(heavy, H24).get('mean_net', 0.0))} "
        f"op {int(summarize(heavy, H24).get('n', 0))} pieken. Beide onder 1×",
        f"levert {_pct(summarize(quiet, H24).get('mean_net', 0.0))} "
        f"op {int(summarize(quiet, H24).get('n', 0))} pieken.",
        "",
        "Niets hiervan wordt live gezet.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    files = sorted(BARS.glob("*.json"))
    print("market volume", flush=True)
    market = market_quote(files)
    peaks, _ms, over, held, ratios = scan(market)
    _write(OUT, peaks, over, held, ratios)
    print(OUT.read_text())


if __name__ == "__main__":
    main()

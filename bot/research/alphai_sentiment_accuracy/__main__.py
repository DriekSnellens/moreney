"""Measure AlphaI news-sentiment hit rates on Bitvavo 15-minute opens.

Usage::

    python -m bot.research.alphai_sentiment_accuracy
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from bot.research.alphai_sentiment_accuracy.score import (
    DAILY_HORIZONS,
    HIGH_RELEVANCE,
    HORIZONS,
    Rate,
    forward_return,
    majority_side,
    price_at,
    rate_of,
    side_of,
)
from bot.research.momentum_backtest.engine import fetch_candles

NEWS = Path("data/research/alphai_crypto_news.json")
BARS = Path("data/research/alphai_sleeve/15m")
BTC_CACHE = Path("data/research/btc_eur_15m_sentiment.json")
OUT = Path("bot/research/alphai_sentiment_accuracy/RESULTS.md")
# Quote assets, not a searched coin list. Everything else is "other".
MAJORS = frozenset({"BTC", "ETH"})


def _bars(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = json.loads(path.read_text())
    ts = np.asarray([int(r[0]) for r in rows], dtype=np.int64)
    opens = np.asarray([float(r[1]) for r in rows], dtype=np.float64)
    return ts, opens


def _btc_bars() -> tuple[np.ndarray, np.ndarray]:
    """Sleeve store has no BTC file. Use a Bitvavo 15m cache for the news window."""
    path = BARS / "BTC.json"
    if path.exists():
        return _bars(path)
    if not BTC_CACHE.exists():
        start = int(datetime(2026, 8, 1, tzinfo=UTC).timestamp() * 1000)
        end = int(datetime(2026, 11, 4, tzinfo=UTC).timestamp() * 1000)
        rows = fetch_candles("BTC", start, end, pause_sec=0.05)
        BTC_CACHE.parent.mkdir(parents=True, exist_ok=True)
        BTC_CACHE.write_text(json.dumps(rows), encoding="utf-8")
    return _bars(BTC_CACHE)


def _base(ticker: str) -> str:
    t = str(ticker or "").strip().upper()
    if t.endswith("-USD"):
        return t[: -len("-USD")]
    return t


def _load_calls() -> tuple[dict[str, list[tuple[int, int, int]]], int, int]:
    """Per base: (published_ms, side, relevance). Neutrals counted, not stored."""
    raw = json.loads(NEWS.read_text())
    seen: set[tuple[str, str]] = set()
    out: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    directional = 0
    neutral = 0
    for row in raw["rows"]:
        uid = str(row.get("uid") or "")
        published = str(row.get("published") or "")
        try:
            stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        ms = int(stamp.timestamp() * 1000)
        relevance = int(row.get("relevance") or 0)
        for ticker, sentiment in (row.get("sentiments") or {}).items():
            base = _base(str(ticker))
            if not base or (uid, base) in seen:
                continue
            seen.add((uid, base))
            side = side_of(str(sentiment))
            if side is None:
                continue
            if side == 0:
                neutral += 1
                continue
            directional += 1
            out[base].append((ms, side, relevance))
    return out, directional, neutral


def _score_base(
    calls: list[tuple[int, int, int]],
    ts: np.ndarray,
    opens: np.ndarray,
    btc_ts: np.ndarray,
    btc_opens: np.ndarray,
) -> dict[str, list[tuple[int, float, float | None]]]:
    """horizon -> list of (side, coin return, excess vs BTC or None)."""
    found: dict[str, list[tuple[int, float, float | None]]] = {name: [] for name, _ in HORIZONS}
    for published_ms, side, _rel in calls:
        for name, bars in HORIZONS:
            fwd = forward_return(ts, opens, published_ms, bars)
            if fwd is None:
                continue
            ret, entry_ms, exit_ms = fwd
            btc_in = price_at(btc_ts, btc_opens, entry_ms)
            btc_out = price_at(btc_ts, btc_opens, exit_ms)
            excess = None if btc_in is None or btc_out is None else ret - (btc_out / btc_in - 1.0)
            found[name].append((side, ret, excess))
    return found


def _daily_votes(
    calls: list[tuple[int, int, int]],
) -> list[tuple[int, int]]:
    """(next midnight ms, majority side) for days that are not a tie."""
    by_day: dict[str, list[int]] = defaultdict(list)
    for published_ms, side, _rel in calls:
        day = datetime.fromtimestamp(published_ms / 1000, UTC).date().isoformat()
        by_day[day].append(side)
    out: list[tuple[int, int]] = []
    for day, sides in by_day.items():
        vote = majority_side(sides)
        if vote is None:
            continue
        start = datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(days=1)
        out.append((int(start.timestamp() * 1000), vote))
    return out


Book = dict[str, list[tuple[int, float, float | None]]]
Rows = list[tuple[str, Rate | None]]


def _fmt(rate: Rate | None) -> str:
    if rate is None:
        return "| — | 0 | — | — | — | — | — | — | — |"
    return (
        f"| {rate.hit_pct:.1f}% | {rate.n} | {rate.bull_hit_pct:.1f}% ({rate.bull_n}) | "
        f"{rate.bear_hit_pct:.1f}% ({rate.bear_n}) | {rate.up_pct:.1f}% | {rate.null_pct:.1f}% | "
        f"{rate.lift_pp:+.1f} pp | {rate.mean_bull_pct:+.2f}% | {rate.mean_bear_pct:+.2f}% |"
    )


def _table(title: str, rows: list[tuple[str, Rate | None]]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| horizon | hit | n | bull (n) | bear (n) | up | null | lift | mean bull | mean bear |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, rate in rows:
        lines.append(f"| {name} {_fmt(rate)}")
    lines.append("")
    return lines


def main() -> None:
    calls, directional, neutral = _load_calls()
    btc_ts, btc_opens = _btc_bars()
    priced: dict[str, list[tuple[int, float, float | None]]] = {name: [] for name, _ in HORIZONS}
    high: dict[str, list[tuple[int, float, float | None]]] = {name: [] for name, _ in HORIZONS}
    majors: dict[str, list[tuple[int, float]]] = {name: [] for name, _ in HORIZONS}
    others: dict[str, list[tuple[int, float]]] = {name: [] for name, _ in HORIZONS}
    daily: dict[str, list[tuple[int, float, float | None]]] = {name: [] for name in DAILY_HORIZONS}
    missing_tape = 0
    for base, rows in sorted(calls.items()):
        path = BARS / f"{base}.json"
        if not path.exists():
            missing_tape += len(rows)
            continue
        ts, opens = _bars(path)
        scored = _score_base(rows, ts, opens, btc_ts, btc_opens)
        high_calls = [row for row in rows if row[2] >= HIGH_RELEVANCE]
        high_scored = _score_base(high_calls, ts, opens, btc_ts, btc_opens)
        bucket = majors if base in MAJORS else others
        for name, _bars_n in HORIZONS:
            priced[name].extend(scored[name])
            high[name].extend(high_scored[name])
            bucket[name].extend((side, ret) for side, ret, _xs in scored[name])
        horizon_bars = {name: bars for name, bars in HORIZONS}
        for midnight, side in _daily_votes(rows):
            for name in DAILY_HORIZONS:
                fwd = forward_return(ts, opens, midnight, horizon_bars[name])
                if fwd is None:
                    continue
                ret, entry_ms, exit_ms = fwd
                btc_in = price_at(btc_ts, btc_opens, entry_ms)
                btc_out = price_at(btc_ts, btc_opens, exit_ms)
                excess = None
                if btc_in is not None and btc_out is not None:
                    excess = ret - (btc_out / btc_in - 1.0)
                daily[name].append((side, ret, excess))

    def price_rows(book: Book) -> Rows:
        out: Rows = []
        for name, _bars_n in HORIZONS:
            out.append((name, rate_of([(s, r) for s, r, _x in book[name]])))
        return out

    def excess_rows(book: Book) -> Rows:
        out: Rows = []
        for name, _bars_n in HORIZONS:
            pairs = [(s, x) for s, _r, x in book[name] if x is not None]
            out.append((name, rate_of(pairs)))
        return out

    lines = [
        "# AlphaI news sentiment, hit rate",
        "",
        "Each ticker label on a headline is one call. Positive is a call that",
        "the next open is higher; negative is a call that it is lower. Neutral",
        "is not a direction and is left out of the hit rate. The entry is the",
        "first 15-minute Bitvavo open at or after the headline (or, for the",
        "daily vote, the next UTC midnight). The exit is the open one horizon",
        "later. A gap, or a bar more than 15 minutes after the label, drops",
        "the call. A return of zero is a miss.",
        "",
        "Null is the hit rate you get by keeping this mix of bullish and",
        "bearish labels and ignoring which headline they came from: the share",
        "of bullish labels times the share of up moves, plus the share of",
        "bearish labels times the share of down moves. Lift is hit minus that",
        "null, in percentage points. Mean bull and mean bear are the average",
        "forward returns of those labels, in percent, with no fee.",
        "",
        f"Tape `{BARS}`. BTC-EUR 15m comes from Bitvavo, cached at `{BTC_CACHE}`,",
        "because that sleeve store has no BTC file.",
        f"News `{NEWS}`. Horizons and the relevance cut at",
        f"{HIGH_RELEVANCE} were fixed before the run. Nothing is armed.",
        "",
        f"Directional labels {directional}. Neutral labels {neutral} "
        f"({100.0 * neutral / (directional + neutral):.1f}% of labels). "
        f"Labels with no Bitvavo 15m file: {missing_tape}.",
        "",
    ]
    lines += _table("Price sign, every directional label", price_rows(priced))
    lines += _table("Sign versus BTC, every directional label", excess_rows(priced))
    lines += _table(
        f"Price sign, relevance {HIGH_RELEVANCE} or higher",
        price_rows(high),
    )
    lines += _table(
        "Price sign, one majority vote per coin per UTC day",
        [(name, rate_of([(s, r) for s, r, _x in daily[name]])) for name in DAILY_HORIZONS],
    )
    lines += _table(
        "Price sign, BTC and ETH only",
        [(name, rate_of(majors[name])) for name, _n in HORIZONS],
    )
    lines += _table(
        "Price sign, every other coin",
        [(name, rate_of(others[name])) for name, _n in HORIZONS],
    )
    text = "\n".join(lines)
    OUT.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

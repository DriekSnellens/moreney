"""Every combination of the levers already used in the sentiment studies.

Trade specs are the cartesian product of entry (next open, one hour later,
next UTC midnight), horizon (1h, 4h, 24h, 7d), side (long, short), stop
(none, 3%, 6%, 10%) and trail (none, 10%). Sentiment states are the product
of the label, relevance below or at 8, trailing 24h breadth, and the
BTC+ETH slice versus every other coin. A state needs 20 finished trades and
a positive mean before it is allowed to leave cash.

The map is fit only on trades that exited before the halfway cut. The second
half trades that frozen map. A weekly refit uses only trades that have closed.
The luckiest cell on the second half is reported and is not the map.
Nothing is armed.
"""

from __future__ import annotations

import csv
import sys
from datetime import UTC, datetime, timedelta

import numpy as np

from bot.research.alphai_sentiment_accuracy.payoff import (
    BOOK_EUR,
    FEE,
    _ohlc,
    net_return,
    slot_book_compound,
)
from bot.research.alphai_sentiment_accuracy.score import BAR_MS, HIGH_RELEVANCE, HORIZONS
from bot.research.alphai_sentiment_accuracy.search import time_cut
from bot.research.alphai_sentiment_accuracy.selector import MIN_MARKET

DAY_MS = 24 * 60 * 60 * 1000
WEEK_MS = 7 * DAY_MS
MIN_COMPLETED = MIN_MARKET
MIN_BREADTH = 5
BREADTH_SPLIT = 0.5
# Same slice as the hit-rate study: quote assets, not a searched coin list.
MAJORS = frozenset({"BTC", "ETH"})
STOPS = (0.0, 0.03, 0.06, 0.10)
TRAILS = (0.0, 0.10)
TRAIL_PCT = 0.10
ENTRIES = ("next open", "plus 1h", "next midnight")
ENTRY_DELAY = (0, 4, None)
HORIZON_NAMES = tuple(name for name, _bars in HORIZONS)
HORIZON_BARS = tuple(bars for _name, bars in HORIZONS)
LABELS = ("bullish", "bearish")
RELS = ("rel<8", "rel>=8")
BREADTHS = ("quiet", "breadth bull", "breadth bear")
UNIVERSES = ("other", "major")
N_STATE = len(LABELS) * len(RELS) * len(BREADTHS) * len(UNIVERSES)
N_STRAT = len(ENTRIES) * len(HORIZON_BARS) * 2 * len(STOPS) * len(TRAILS)
OUT_PATH = "bot/research/alphai_sentiment_accuracy/GRID.md"
CSV_PATH = "bot/research/alphai_sentiment_accuracy/GRID_CELLS.csv"


def strat_index(entry_i: int, hor_i: int, side_i: int, stop_i: int, trail_i: int) -> int:
    n_hor = len(HORIZON_BARS)
    return (((entry_i * n_hor + hor_i) * 2 + side_i) * len(STOPS) + stop_i) * len(TRAILS) + trail_i


def strat_name(index: int) -> str:
    trail_i = index % len(TRAILS)
    index //= len(TRAILS)
    stop_i = index % len(STOPS)
    index //= len(STOPS)
    side_i = index % 2
    index //= 2
    hor_i = index % len(HORIZON_BARS)
    entry_i = index // len(HORIZON_BARS)
    side = "long" if side_i == 0 else "short"
    stop = "no stop" if STOPS[stop_i] == 0.0 else f"stop {STOPS[stop_i] * 100:.0f}%"
    trail = "no trail" if TRAILS[trail_i] == 0.0 else f"trail {TRAILS[trail_i] * 100:.0f}%"
    return f"{side} {HORIZON_NAMES[hor_i]} {stop} {trail} {ENTRIES[entry_i]}"


def state_index(label_side: int, rel_high: bool, breadth: int, major: bool) -> int:
    side_i = 0 if label_side > 0 else 1
    rel_i = 1 if rel_high else 0
    uni = 1 if major else 0
    return ((side_i * len(RELS) + rel_i) * len(BREADTHS) + breadth) * len(UNIVERSES) + uni


def state_name(index: int) -> str:
    uni = index % len(UNIVERSES)
    index //= len(UNIVERSES)
    breadth = index % len(BREADTHS)
    index //= len(BREADTHS)
    rel = index % len(RELS)
    side = index // len(RELS)
    return f"{LABELS[side]}, {RELS[rel]}, {BREADTHS[breadth]}, {UNIVERSES[uni]}"


def trailing_breadth_codes(
    event_ms: np.ndarray,
    event_side: np.ndarray,
    query_ms: np.ndarray,
) -> np.ndarray:
    """0 quiet, 1 bullish share, 2 bearish share. Same-timestamp news is excluded."""
    bull = np.cumsum(event_side > 0)
    out = np.zeros(len(query_ms), dtype=np.int8)
    for i, ms in enumerate(query_ms):
        hi = int(np.searchsorted(event_ms, int(ms), side="left"))
        lo = int(np.searchsorted(event_ms, int(ms) - DAY_MS, side="left"))
        n = hi - lo
        if n < MIN_BREADTH:
            continue
        prev = int(bull[lo - 1]) if lo else 0
        share = (int(bull[hi - 1]) - prev) / n
        out[i] = 1 if share >= BREADTH_SPLIT else 2
    return out


def next_midnight_ms(published_ms: int) -> int:
    day = datetime.fromtimestamp(published_ms / 1000, UTC).date()
    nxt = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
    return int(nxt.timestamp() * 1000)


def entry_bar(ts: np.ndarray, opens: np.ndarray, when_ms: int) -> int | None:
    """First 15-minute bar at or after ``when_ms``, within one bar."""
    if len(ts) == 0:
        return None
    i = int(np.searchsorted(ts, int(when_ms), side="left"))
    if i >= len(ts) or int(ts[i]) - int(when_ms) > BAR_MS:
        return None
    if float(opens[i]) <= 0.0:
        return None
    return i


def delayed_entry(ts: np.ndarray, i: int, delay: int, horizon: int) -> int | None:
    j = i + int(delay)
    last = j + int(horizon)
    if last >= len(ts):
        return None
    if int(ts[j]) - int(ts[i]) != int(delay) * BAR_MS:
        return None
    if int(ts[last]) - int(ts[j]) != int(horizon) * BAR_MS:
        return None
    return j


def path_exits(
    ts: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    i: int,
    horizon: int,
    side: int,
    fee: float,
) -> list[tuple[float, int]] | None:
    """Net and exit time for every stop and trail on one path.

    The order matches ``walk_exit``: a stop is checked before the peak, and a
    trail that fires on the close fills on the next open. Results are ordered
    stop-major, then trail.
    """
    last = i + int(horizon)
    if i < 0 or side == 0 or last >= len(ts):
        return None
    if int(ts[last]) - int(ts[i]) != int(horizon) * BAR_MS:
        return None
    entry = float(opens[i])
    if entry <= 0.0:
        return None
    stop_px = [entry * (1.0 - side * stop) if stop > 0.0 else 0.0 for stop in STOPS]
    stop_fill: list[tuple[float, int] | None] = [None] * len(STOPS)
    trail_fill: list[tuple[float, int] | None] = [None] * len(STOPS)
    peak = entry
    for k in range(i, last):
        low = float(lows[k])
        high = float(highs[k])
        opened = float(opens[k])
        for s, stop in enumerate(STOPS):
            if stop <= 0.0 or stop_fill[s] is not None:
                continue
            if side > 0 and low <= stop_px[s]:
                fill = opened if opened < stop_px[s] else stop_px[s]
                stop_fill[s] = (fill, int(ts[k]))
            elif side < 0 and high >= stop_px[s]:
                fill = opened if opened > stop_px[s] else stop_px[s]
                stop_fill[s] = (fill, int(ts[k]))
        peak = max(peak, high) if side > 0 else min(peak, low)
        closed = float(closes[k])
        trail_hit = (side > 0 and closed <= peak * (1.0 - TRAIL_PCT)) or (
            side < 0 and closed >= peak * (1.0 + TRAIL_PCT)
        )
        if trail_hit:
            fill_px = float(opens[k + 1])
            fill_ms = int(ts[k + 1])
            for s, _stop in enumerate(STOPS):
                if trail_fill[s] is None and stop_fill[s] is None:
                    trail_fill[s] = (fill_px, fill_ms)
    time_fill = (float(opens[last]), int(ts[last]))
    out: list[tuple[float, int]] = []
    for s, _stop in enumerate(STOPS):
        for trail in TRAILS:
            events = [time_fill]
            if _stop > 0.0 and stop_fill[s] is not None:
                events.append(stop_fill[s])
            if trail > 0.0 and trail_fill[s] is not None:
                events.append(trail_fill[s])
            fill_px, fill_ms = min(events, key=lambda item: item[1])
            if fill_px <= 0.0:
                out.append((float("nan"), fill_ms))
            else:
                out.append((net_return(side, entry, fill_px, fee), fill_ms))
    return out


def select_map(train_mean: np.ndarray, train_n: np.ndarray, min_n: int) -> np.ndarray:
    """Per state, the earliest strategy whose mean is strictly best and positive."""
    chosen = np.full(train_mean.shape[0], -1, dtype=np.int16)
    best = np.zeros(train_mean.shape[0], dtype=np.float64)
    for strat in range(train_mean.shape[1]):
        better = (train_n[:, strat] >= min_n) & (train_mean[:, strat] > best)
        best = np.where(better, train_mean[:, strat], best)
        chosen = np.where(better, strat, chosen)
    return chosen


def _fit(
    entry0: np.ndarray,
    state: np.ndarray,
    nets: np.ndarray,
    exits: np.ndarray,
    as_of: int,
) -> tuple[np.ndarray, np.ndarray]:
    mean = np.zeros((N_STATE, N_STRAT), dtype=np.float64)
    count = np.zeros((N_STATE, N_STRAT), dtype=np.int32)
    train_h = entry0 < as_of
    for strat in range(N_STRAT):
        ok = train_h & np.isfinite(nets[:, strat]) & (exits[:, strat] <= as_of)
        for st in range(N_STATE):
            sel = ok & (state == st)
            n = int(sel.sum())
            count[st, strat] = n
            if n:
                mean[st, strat] = float(nets[sel, strat].mean())
    return mean, count


def _score_period(
    entry0: np.ndarray,
    state: np.ndarray,
    nets: np.ndarray,
    start: int,
    end: int | None,
) -> tuple[np.ndarray, np.ndarray]:
    mean = np.zeros((N_STATE, N_STRAT), dtype=np.float64)
    count = np.zeros((N_STATE, N_STRAT), dtype=np.int32)
    window = entry0 >= start if end is None else (entry0 >= start) & (entry0 < end)
    for strat in range(N_STRAT):
        ok = window & np.isfinite(nets[:, strat])
        for st in range(N_STATE):
            sel = ok & (state == st)
            n = int(sel.sum())
            count[st, strat] = n
            if n:
                mean[st, strat] = float(nets[sel, strat].mean())
    return mean, count


def _deploy(
    chosen: np.ndarray,
    idx: np.ndarray,
    state: np.ndarray,
    nets: np.ndarray,
    entries: np.ndarray,
    exits: np.ndarray,
    bases: list[str],
) -> list[tuple[int, int, str, float]]:
    trades: list[tuple[int, int, str, float]] = []
    for i in idx:
        strat = int(chosen[int(state[i])])
        if strat < 0 or not np.isfinite(nets[i, strat]):
            continue
        trades.append(
            (int(entries[i, strat]), int(exits[i, strat]), bases[i], float(nets[i, strat]))
        )
    return trades


def _book_line(name: str, trades: list[tuple[int, int, str, float]]) -> str:
    _taken, end, max_dd = slot_book_compound(trades)
    mean = float(np.mean([row[3] for row in trades])) if trades else 0.0
    ret = end / BOOK_EUR - 1.0
    return (
        f"| {name} | {len(trades)} | {_taken} | {mean * 100:+.3f}% | "
        f"{end:,.2f} | {100 * ret:+.1f}% | {100 * max_dd:.1f}% |"
    )


def _load() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    from bot.research.alphai_sentiment_accuracy.__main__ import (  # noqa: PLC0415
        BARS,
        _load_calls,
    )

    calls, _directional, _neutral = _load_calls()
    ev_ms: list[int] = []
    ev_side: list[int] = []
    for rows in calls.values():
        for published, side, _rel in rows:
            ev_ms.append(published)
            ev_side.append(side)
    event_ms = np.asarray(ev_ms, dtype=np.int64)
    event_side = np.asarray(ev_side, dtype=np.int8)
    order = np.argsort(event_ms, kind="mergesort")
    event_ms = event_ms[order]
    event_side = event_side[order]

    net_rows: list[np.ndarray] = []
    exit_rows: list[np.ndarray] = []
    entry_rows: list[np.ndarray] = []
    entry0s: list[int] = []
    states: list[int] = []
    bases: list[str] = []
    seen = 0
    for base, rows in sorted(calls.items()):
        path = BARS / f"{base}.json"
        if not path.exists():
            continue
        seen += 1
        if seen % 40 == 0:
            print(f"grid load {seen} {base}", file=sys.stderr)
        ts, opens, highs, lows, closes = _ohlc(path)
        query = np.asarray([published for published, _side, _rel in rows], dtype=np.int64)
        breadths = trailing_breadth_codes(event_ms, event_side, query)
        major = base in MAJORS
        for (published, side, rel), breadth in zip(rows, breadths, strict=True):
            i0 = entry_bar(ts, opens, published)
            if i0 is None:
                continue
            when = (
                published,
                published,
                next_midnight_ms(published),
            )
            delays = (0, 4, 0)
            net_row = np.full(N_STRAT, np.nan, dtype=np.float64)
            exit_row = np.zeros(N_STRAT, dtype=np.int64)
            entry_row = np.zeros(N_STRAT, dtype=np.int64)
            for entry_i, delay in enumerate(delays):
                origin = i0 if entry_i < 2 else entry_bar(ts, opens, when[2])
                if origin is None:
                    continue
                for hor_i, horizon in enumerate(HORIZON_BARS):
                    j = delayed_entry(ts, origin, delay if entry_i < 2 else 0, horizon)
                    if j is None:
                        continue
                    for side_i, trade_side in enumerate((1, -1)):
                        got = path_exits(
                            ts, opens, highs, lows, closes, j, horizon, trade_side, FEE
                        )
                        if got is None:
                            continue
                        for k, (net, ex_ms) in enumerate(got):
                            stop_i, trail_i = divmod(k, 2)
                            strat = strat_index(entry_i, hor_i, side_i, stop_i, trail_i)
                            net_row[strat] = net
                            exit_row[strat] = ex_ms
                            entry_row[strat] = int(ts[j])
            net_rows.append(net_row)
            exit_rows.append(exit_row)
            entry_rows.append(entry_row)
            entry0s.append(int(ts[i0]))
            states.append(state_index(side, rel >= HIGH_RELEVANCE, int(breadth), major))
            bases.append(base)
    entry0 = np.asarray(entry0s, dtype=np.int64)
    state = np.asarray(states, dtype=np.int16)
    nets = np.vstack(net_rows) if net_rows else np.zeros((0, N_STRAT))
    exits = np.vstack(exit_rows) if exit_rows else np.zeros((0, N_STRAT), dtype=np.int64)
    entries = np.vstack(entry_rows) if entry_rows else np.zeros((0, N_STRAT), dtype=np.int64)
    return entry0, state, nets, exits, entries, bases


def _peek(test_mean: np.ndarray, test_n: np.ndarray, train_n: np.ndarray) -> tuple[str, str]:
    def best(mask: np.ndarray) -> str:
        if not np.any(mask):
            return "—"
        masked = np.where(mask, test_mean, -np.inf)
        flat = int(np.argmax(masked))
        st, strat = divmod(flat, N_STRAT)
        return (
            f"`{state_name(st)}` / `{strat_name(strat)}` "
            f"{test_mean[st, strat] * 100:+.3f}% "
            f"(test n={int(test_n[st, strat])}, train n={int(train_n[st, strat])})"
        )

    both = (test_n >= MIN_COMPLETED) & (train_n >= MIN_COMPLETED)
    test_only = test_n >= MIN_COMPLETED
    return best(both), best(test_only)


def main() -> None:
    entry0, state, nets, exits, entries, bases = _load()
    cut = time_cut(entry0.tolist()) if len(entry0) else 0
    train_mean, train_n = _fit(entry0, state, nets, exits, cut)
    chosen = select_map(train_mean, train_n, MIN_COMPLETED)
    test_mean, test_n = _score_period(entry0, state, nets, cut, None)
    test_idx = np.flatnonzero(entry0 >= cut)
    frozen = _deploy(chosen, test_idx, state, nets, entries, exits, bases)

    forward: list[tuple[int, int, str, float]] = []
    forward_test: list[tuple[int, int, str, float]] = []
    if len(entry0):
        week = int(entry0.min())
        last = int(entry0.max())
        while week <= last:
            w_mean, w_n = _fit(entry0, state, nets, exits, week)
            w_choice = select_map(w_mean, w_n, MIN_COMPLETED)
            idx = np.flatnonzero((entry0 >= week) & (entry0 < week + WEEK_MS))
            forward.extend(_deploy(w_choice, idx, state, nets, entries, exits, bases))
            test_idx = idx[entry0[idx] >= cut]
            forward_test.extend(_deploy(w_choice, test_idx, state, nets, entries, exits, bases))
            week += WEEK_MS

    eligible = int((train_n >= MIN_COMPLETED).sum())
    positive = int(((train_n >= MIN_COMPLETED) & (train_mean > 0.0)).sum())
    held = (train_n >= MIN_COMPLETED) & (train_mean > 0.0) & (test_n >= MIN_COMPLETED)
    if np.any(held):
        held_test = test_mean[held]
        still = float(np.mean(held_test > 0.0))
        median = float(np.median(held_test))
    else:
        still = 0.0
        median = 0.0
    peek_both, peek_test = _peek(test_mean, test_n, train_n)
    n_chosen = int(np.sum(chosen >= 0))
    if len(entry0):
        first = datetime.fromtimestamp(int(entry0.min()) / 1000, UTC).date()
        last_day = datetime.fromtimestamp(int(entry0.max()) / 1000, UTC).date()
        cut_day = datetime.fromtimestamp(cut / 1000, UTC).date()
        span = f"{first.isoformat()} to {last_day.isoformat()}, cut {cut_day.isoformat()}"
    else:
        span = "—"

    lines = [
        "# Sentiment grid, every combination of the existing levers",
        "",
        f"{N_STRAT} trade specifications × {N_STATE} sentiment states "
        f"= {N_STRAT * N_STATE} combinations. The trade specs are entry, horizon,",
        "side, stop and trail. The states are the label, relevance 8, the trailing",
        "24-hour breadth (quiet under 5 headlines, otherwise a bullish share of",
        "one half), and BTC+ETH versus every other coin. These sets were fixed",
        "before the run. No new percentage was added.",
        "",
        "A state leaves cash only when a spec has 20 trades that already exited",
        "and a positive mean. The halfway cut uses the next-open entry. Training",
        "cannot see a trade that exits after the cut. The second half trades the",
        "frozen map. The weekly refit does the same with only closed trades.",
        "Fees are the Bitvavo taker on both sides. The euro book is four slots",
        "from €20,000, resized with equity. Nothing is armed.",
        "",
        f"Headlines with a next open: {len(entry0)}. Span {span}.",
        f"States that chose a spec: {n_chosen} of {N_STATE}.",
        f"Cells with 20 training trades: {eligible}. Of those, positive mean: {positive}.",
        f"Train-positive cells that also have 20 test trades: {int(held.sum())}. "
        f"Share still positive on the test: {100 * still:.1f}%. "
        f"Median test mean: {median * 100:+.3f}%.",
        "",
        "Luckiest cell on the second half, looked up after the fact and not used:",
        f"among cells with 20 train and 20 test trades: {peek_both}.",
        f"among cells with 20 test trades: {peek_test}.",
        "",
        "### Chosen on the first half, scored on the second",
        "",
        "| state | chosen | train n | train mean | test n | test mean |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for st in range(N_STATE):
        strat = int(chosen[st])
        name = "flat" if strat < 0 else strat_name(strat)
        tr_n = int(train_n[st, strat]) if strat >= 0 else 0
        tr_m = f"{train_mean[st, strat] * 100:+.3f}%" if strat >= 0 else "—"
        te_n = int(test_n[st, strat]) if strat >= 0 else 0
        te_m = f"{test_mean[st, strat] * 100:+.3f}%" if strat >= 0 and te_n else "—"
        lines.append(f"| {state_name(st)} | {name} | {tr_n} | {tr_m} | {te_n} | {te_m} |")
    lines += [
        "",
        "### Books",
        "",
        "Mean net is every deployed signal. Flat states are not signals.",
        "The euro columns are the four-slot book.",
        "",
        "| book | signals | slotted | mean net | end € | return | max DD |",
        "|---|---:|---:|---:|---:|---:|---:|",
        _book_line("frozen map, second half", frozen),
        _book_line("weekly refit, second half", forward_test),
        _book_line("weekly refit, full span", forward),
        "",
        "The full grid is in `GRID_CELLS.csv`.",
        "",
    ]
    text = "\n".join(lines) + "\n"
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        handle.write(text)
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["state", "strategy", "train_n", "train_mean", "test_n", "test_mean", "chosen"]
        )
        for st in range(N_STATE):
            for strat in range(N_STRAT):
                if train_n[st, strat] == 0 and test_n[st, strat] == 0:
                    continue
                writer.writerow(
                    [
                        state_name(st),
                        strat_name(strat),
                        int(train_n[st, strat]),
                        f"{train_mean[st, strat]:.6f}",
                        int(test_n[st, strat]),
                        f"{test_mean[st, strat]:.6f}",
                        "yes" if int(chosen[st]) == strat else "",
                    ]
                )
    print(text)


if __name__ == "__main__":
    main()

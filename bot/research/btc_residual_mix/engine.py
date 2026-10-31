"""Causal 1d BTC + residual-weekly mix.

Residual pick: 20d skip-1 excess vs BTC (excess > floor). BTC sleeve is a
generic fraction so the book is not 100% one alt. Flatten: SMA50 on all
lots, BTC only, none, or regime (risk-off = 100% residual).

Wet = next-open Bitvavo taker. Not armed live. No per-coin branches.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from bot.live.momentum_btc_rs_clip import (
    closes_of,
    high_proximity,
    quote_vol,
    residual_rank_score,
    rs_excess,
    sma,
)
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_exit_lab.engine import (
    WET,
    Book,
    FillModel,
    Order,
    _adv,
    _apply_orders,
    _cut,
    _overlay_orders,
    _px_map,
    bar_date,
    metrics,
    rows_through,
    run_clip_exits,
    year_pnl,
)
from bot.research.clip_exit_lab.policies import ExitPolicy

DAY_MS = 86_400_000
MIN_QVOL = 80_000.0
# Daily residual vol below this is treated as this, so a frozen print
# cannot dominate an information-ratio rank.
SPREAD_VOL_FLOOR = 0.005
Flatten = Literal["all", "btc", "none", "regime"]


def _signal_closes(closes: Sequence[float], skip: int) -> Sequence[float]:
    if skip <= 0:
        return closes
    if len(closes) <= skip:
        return ()
    return closes[:-skip]


def rearm_after_exit(
    book: Book,
    overlay: Sequence[Order],
    closes: Mapping[str, float],
    mode: str,
) -> bool:
    """Whether this exit may open the next leader before the week is up.

    ``lock`` requires the lock in the sell reason. ``green`` and
    ``green_next`` require the signal close to be above the entry. A
    partial sell does not count. Anything else keeps the normal week.
    """
    if mode not in ("lock", "green", "green_next"):
        return False
    for order in overlay:
        if order.side != "sell" or order.role != "alt" or order.qty > 0:
            continue
        lot = book.lots.get(order.base)
        if lot is None or lot.entry_px <= 0:
            continue
        if mode == "lock":
            if "alt_lock" in (order.reason or ""):
                return True
            continue
        close = float(closes.get(order.base) or 0.0)
        if close > lot.entry_px:
            return True
    return False


def drop_down_gaps(
    orders: Sequence[Order],
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    cap: float,
) -> list[Order]:
    """Cancel an alt buy when the open has gapped down through the cap.

    Sells still fill. The book then waits for the clock it already set.
    """
    kept: list[Order] = []
    for order in orders:
        if order.side == "buy" and order.role == "alt":
            gap = open_gap(ohlc, order.base, date)
            if gap is not None and gap < -float(cap) - 1e-12:
                continue
        kept.append(order)
    return kept


def tide_alts(
    next_alts: Sequence[str],
    held_alts: Sequence[str],
    *,
    tide_ok: bool,
) -> tuple[str, ...]:
    """Keep a held alt when BTC's own window is below the tide floor.

    A flat book stays flat. Names that are not already held are not opened.
    """
    if tide_ok:
        return tuple(next_alts)
    return tuple(held_alts)


def strip_red_cut_while_leader(orders: Sequence[Order], leader: str) -> list[Order]:
    """Drop a red-cut sell while that name is still the residual leader.

    A trail or lock on the same order still sells. An empty leader means
    nobody qualifies, so the red cut stands.
    """
    kept: list[Order] = []
    for order in orders:
        parts = [part for part in str(order.reason).split("+") if part]
        if (
            order.side == "sell"
            and leader
            and order.base == leader
            and "alt_red_cut" in parts
        ):
            rest = [part for part in parts if part != "alt_red_cut"]
            if not rest:
                continue
            order = Order(
                side=order.side,
                base=order.base,
                role=order.role,
                adv=order.adv,
                qty=order.qty,
                notional=order.notional,
                reason="+".join(rest),
                keep_entry=order.keep_entry,
                same_bar_px=order.same_bar_px,
            )
        kept.append(order)
    return kept


def lead_lost(
    mode: str,
    *,
    base: str,
    leader: str,
    excess: float | None,
    close: float,
    entry: float,
    floor: float,
) -> bool:
    """Whether a held alt has lost the residual lead on this close.

    ``rank`` sells when it is not the top qualifier. ``rank_red`` also
    requires the close to be under the entry, so a green ex-leader stays.
    ``floor`` sells when its own excess is no longer above the floor,
    including when it dropped out of the ranked set.
    """
    if mode == "rank":
        return base != leader
    if mode == "rank_red":
        return base != leader and entry > 0 and close < entry
    if mode == "floor":
        return excess is None or float(excess) <= float(floor) + 1e-12
    return False


def widen_trail_while_leader(
    orders: Sequence[Order],
    leader: str,
    *,
    wide: float,
    peaks: Mapping[str, float],
    closes: Mapping[str, float],
) -> list[Order]:
    """Hold a leader through the 10% trail until the close is ``wide`` off the peak.

    A name that is not the leader keeps the live trail. A lock on the same
    order still sells.
    """
    if wide <= 0 or not leader:
        return list(orders)
    kept: list[Order] = []
    for order in orders:
        parts = [part for part in str(order.reason).split("+") if part]
        peak = float(peaks.get(order.base) or 0.0)
        close = float(closes.get(order.base) or 0.0)
        off = 1.0 - close / peak if peak > 0 and close > 0 else 0.0
        if (
            order.side == "sell"
            and order.base == leader
            and "alt_trail" in parts
            and off < float(wide) - 1e-12
        ):
            rest = [part for part in parts if part != "alt_trail"]
            if not rest:
                continue
            order = Order(
                side=order.side,
                base=order.base,
                role=order.role,
                adv=order.adv,
                qty=order.qty,
                notional=order.notional,
                reason="+".join(rest),
                keep_entry=order.keep_entry,
                same_bar_px=order.same_bar_px,
            )
        kept.append(order)
    return kept


def crowd_alts(
    next_alts: Sequence[str],
    held_alts: Sequence[str],
    *,
    count: int,
    bound: int,
    side: str,
) -> tuple[str, ...]:
    """Open a new ride only when the qualifier count is on the allowed side.

    A name already held stays. ``max`` allows the open when the count is at
    most ``bound``. ``min`` allows it when the count is at least ``bound``.
    """
    if side == "max":
        ok = count <= int(bound)
    elif side == "min":
        ok = count >= int(bound)
    else:
        ok = True
    if ok:
        return tuple(next_alts)
    return tuple(held_alts)


def hold_unless_gap(
    next_alts: Sequence[str],
    held_alts: Sequence[str],
    excess: Mapping[str, float],
    *,
    gap: float,
) -> tuple[str, ...]:
    """Keep the held name unless the challenger's excess leads by ``gap``.

    A fresh open and a flatten to cash are unchanged. A held name with no
    excess is not protected: the rotation stands.
    """
    if gap <= 0 or not next_alts or not held_alts:
        return tuple(next_alts)
    leaving = [base for base in held_alts if base not in next_alts]
    if not leaving:
        return tuple(next_alts)
    held_xs = excess.get(leaving[0])
    chal_xs = excess.get(str(next_alts[0]))
    if held_xs is None or chal_xs is None:
        return tuple(next_alts)
    if float(chal_xs) - float(held_xs) + 1e-12 < float(gap):
        return tuple(held_alts)
    return tuple(next_alts)


def block_fresh(
    next_alts: Sequence[str],
    held_alts: Sequence[str],
    *,
    allow: bool,
) -> tuple[str, ...]:
    """Block a new name when ``allow`` is false. A flatten to cash stays cash.

    A name that is already the next target is kept. A rotation into someone
    else sticks with the held name. A fresh open becomes cash.
    """
    if allow:
        return tuple(next_alts)
    held = tuple(held_alts)
    stayed = tuple(base for base in next_alts if base in set(held))
    if stayed:
        return stayed
    if next_alts and held:
        return held
    return ()


def extension_allows(dist: float | None, *, side: str, bound: float) -> bool:
    """Whether BTC's distance above its SMA is on the allowed side of ``bound``.

    ``max`` allows a distance at or under the bound. ``min`` allows a
    distance at or over it. A missing distance does not allow a new open.
    """
    if dist is None:
        return False
    if side == "max":
        return float(dist) <= float(bound) + 1e-12
    if side == "min":
        return float(dist) + 1e-12 >= float(bound)
    return True


def ride_to_keep(mode: str, *, green: bool, is_leader: bool) -> bool:
    """Keep a held alt through a BTC SMA cash day. A new name is not opened.

    ``green`` is a close above the entry. ``leader`` is still the top
    residual qualifier. ``both`` needs the two. Anything else keeps the
    live cash rule.
    """
    if mode == "green":
        return green
    if mode == "leader":
        return is_leader
    if mode == "both":
        return green and is_leader
    return False


def _volume_expanded(rows: Sequence[Sequence[float]], mult: float) -> bool:
    """Last 20 days of quote volume versus the 20 days before that."""
    if len(rows) < 40 or mult <= 0:
        return False
    recent = quote_vol(rows[-20:])
    prior = quote_vol(rows[-40:-20])
    if prior <= 0:
        return False
    return recent + 1e-9 >= float(mult) * prior


def losing_exit(close: float, entry: float) -> bool:
    """A full sell whose signal close is under the entry."""
    return entry > 0 and close > 0 and close < entry


def open_gap(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    base: str,
    date: str,
) -> float | None:
    """Next open versus the prior close. None when either price is missing."""
    rows = rows_through(ohlc.get(base) or [], date)
    if len(rows) < 2 or len(rows[-1]) < 2 or len(rows[-2]) < 5:
        return None
    prev = float(rows[-2][4])
    opened = float(rows[-1][1])
    if prev <= 0 or opened <= 0:
        return None
    return opened / prev - 1.0


def drop_gapped_buys(
    orders: Sequence[Order],
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    cap: float,
) -> list[Order]:
    """Cancel an alt buy when the open has already gapped through the cap.

    Sells still fill. The book then waits for the clock it already set.
    """
    kept: list[Order] = []
    for order in orders:
        if order.side == "buy" and order.role == "alt":
            gap = open_gap(ohlc, order.base, date)
            if gap is not None and gap > float(cap) + 1e-12:
                continue
        kept.append(order)
    return kept


def top_volume_universe(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    n: int,
    *,
    min_bars: int = 60,
) -> tuple[str, ...]:
    """The ``n`` names with the highest recent quote volume.

    BTC is the benchmark, not a candidate. A name needs ``min_bars``
    completed days so a new listing cannot lead on a handful of prints.
    """
    scored: list[tuple[float, str]] = []
    for base, rows in ohlc.items():
        if str(base).upper() == "BTC":
            continue
        end = _cut(rows, date)
        if end < min_bars:
            continue
        qv = quote_vol(rows[max(0, end - 20) : end])
        if qv <= 0:
            continue
        scored.append((qv, str(base)))
    scored.sort(reverse=True)
    return tuple(base for _, base in scored[: max(1, int(n))])


def _broke_recent_high(rows: Sequence[Sequence[float]], n: int) -> bool | None:
    """Whether today's close is above the high of the prior ``n`` days.

    Today is not part of that high. Short history returns None.
    """
    if n <= 0 or len(rows) < n + 1:
        return None
    today = float(rows[-1][4]) if len(rows[-1]) >= 5 else 0.0
    prior = [float(r[2]) for r in rows[-(n + 1) : -1] if len(r) >= 3 and float(r[2]) > 0]
    if today <= 0 or len(prior) < n:
        return None
    return today > max(prior) + 1e-12


def _max_day_return(closes: Sequence[float], n: int, skip: int) -> float | None:
    """Largest one-day simple return over the last ``n`` signal days."""
    use = list(_signal_closes(closes, skip))
    if len(use) < n + 1 or n <= 0:
        return None
    window = use[-(n + 1) :]
    best: float | None = None
    for i in range(1, len(window)):
        prev = float(window[i - 1])
        if prev <= 0 or float(window[i]) <= 0:
            return None
        day = float(window[i]) / prev - 1.0
        if best is None or day > best:
            best = day
    return best


def _window_return(closes: Sequence[float], lb: int, skip: int) -> float | None:
    use = _signal_closes(closes, skip)
    if len(use) < lb + 1:
        return None
    end = float(use[-1])
    start = float(use[-1 - lb])
    if start <= 0 or end <= 0:
        return None
    return end / start - 1.0


def _log_pair(
    alt: Sequence[float], btc: Sequence[float], n: int, skip: int
) -> tuple[list[float], list[float]] | None:
    """``n`` daily log returns for alt and BTC, ending on the signal close."""
    a = list(_signal_closes(alt, skip))
    b = list(_signal_closes(btc, skip))
    m = min(len(a), len(b))
    if m < n + 1:
        return None
    a = a[m - (n + 1) : m]
    b = b[m - (n + 1) : m]
    ar: list[float] = []
    br: list[float] = []
    for i in range(1, len(a)):
        if min(a[i - 1], b[i - 1], a[i], b[i]) <= 0:
            return None
        ar.append(math.log(a[i] / a[i - 1]))
        br.append(math.log(b[i] / b[i - 1]))
    if len(ar) < n:
        return None
    return ar, br


def _ols_beta(alt: Sequence[float], btc: Sequence[float], n: int, skip: int) -> float | None:
    got = _log_pair(alt, btc, n, skip)
    if got is None:
        return None
    ar, br = got
    mb = sum(br) / len(br)
    var_b = sum((x - mb) ** 2 for x in br)
    if var_b <= 1e-12:
        return None
    ma = sum(ar) / len(ar)
    cov = sum((ar[i] - ma) * (br[i] - mb) for i in range(len(ar)))
    return cov / var_b


def _spread_vol(alt: Sequence[float], btc: Sequence[float], n: int, skip: int) -> float | None:
    got = _log_pair(alt, btc, n, skip)
    if got is None:
        return None
    ar, br = got
    spreads = [ar[i] - br[i] for i in range(len(ar))]
    mean = sum(spreads) / len(spreads)
    var = sum((x - mean) ** 2 for x in spreads) / len(spreads)
    return max(math.sqrt(var), SPREAD_VOL_FLOOR)


def btc_risk_on(last: float, sma_px: float | None, *, gap: float | None = None) -> bool:
    """True when BTC is above its SMA, or not through a deeper band.

    ``gap`` None is the live rule: any close under the SMA is risk-off.
    A gap of 0.05 stays risk-on until the close is 5% under the SMA.
    A gap of 1.0 does not fire while the close is positive.
    """
    if sma_px is None or sma_px <= 0 or last <= 0:
        return False
    band = 0.0 if gap is None else max(0.0, float(gap))
    return last > sma_px * (1.0 - band)


def pick_residual(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    *,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    min_qvol_eur: float = MIN_QVOL,
    lookback_days: int = 20,
    skip_days: int = 1,
    excess_floor: float = 0.0,
    n_alts: int = 1,
    require_alt_sma: bool = False,
    sma_n: int = 50,
    exclude_bases: Sequence[str] | None = None,
    near_high_weight: float = 0.0,
    rank_mode: str = "excess",
    rank_window: int = 20,
    rank_slot: str = "",
    agree_days: int = 0,
    vol_mult: float = 0.0,
    cross_days: int = 0,
    own_floor: float | None = None,
    break_mode: str = "",
    break_days: int = 0,
) -> dict[str, Any]:
    ban = {str(b).upper() for b in (exclude_bases or ())}
    btc_c = closes_of(rows_through(ohlc.get("BTC") or [], date))
    ranked: list[dict[str, Any]] = []
    for base in universe:
        if str(base).upper() in ban:
            continue
        rows = rows_through(ohlc.get(base) or [], date)
        cl = closes_of(rows)
        xs = rs_excess(cl, btc_c, lb=lookback_days, skip=skip_days)
        qv = quote_vol(rows)
        if xs is None or qv < min_qvol_eur:
            continue
        if agree_days > int(lookback_days):
            long_xs = rs_excess(cl, btc_c, lb=int(agree_days), skip=skip_days)
            if long_xs is None or long_xs <= 0:
                continue
        if vol_mult > 0 and not _volume_expanded(rows, vol_mult):
            continue
        if cross_days > 0:
            prior_alt = cl[: -int(cross_days)] if len(cl) > int(cross_days) else []
            prior_btc = btc_c[: -int(cross_days)] if len(btc_c) > int(cross_days) else []
            prior_xs = rs_excess(prior_alt, prior_btc, lb=lookback_days, skip=skip_days)
            if prior_xs is None or prior_xs > excess_floor:
                continue
        if own_floor is not None:
            own = _window_return(cl, lookback_days, skip_days)
            if own is None or own + 1e-12 < float(own_floor):
                continue
        if break_mode in ("above", "inside") and break_days > 0:
            broke = _broke_recent_high(rows, int(break_days))
            if broke is None:
                continue
            if break_mode == "above" and not broke:
                continue
            if break_mode == "inside" and broke:
                continue
        if require_alt_sma:
            s = sma(cl, sma_n)
            last_px = float(cl[-1]) if cl else 0.0
            if s is None or last_px <= s:
                continue
        highs = [float(r[2]) for r in rows if len(r) >= 3]
        close = float(cl[-1]) if cl else 0.0
        prox = high_proximity(highs, close)
        score_xs = float(xs)
        if rank_mode == "beta":
            beta = _ols_beta(cl, btc_c, int(rank_window), skip_days)
            r_alt = _window_return(cl, lookback_days, skip_days)
            r_btc = _window_return(btc_c, lookback_days, skip_days)
            if beta is not None and r_alt is not None and r_btc is not None:
                score_xs = r_alt - beta * r_btc
        score = residual_rank_score(score_xs, prox, weight=float(near_high_weight or 0.0))
        if rank_mode == "ir":
            vol = _spread_vol(cl, btc_c, int(rank_window), skip_days)
            if vol is not None and vol > 0:
                score = score / vol
        if rank_mode in ("accel", "decel"):
            prior_alt = cl[: -int(rank_window)] if len(cl) > int(rank_window) else []
            prior_btc = btc_c[: -int(rank_window)] if len(btc_c) > int(rank_window) else []
            prior_xs = rs_excess(prior_alt, prior_btc, lb=lookback_days, skip=skip_days)
            if prior_xs is None:
                continue
            delta = float(xs) - float(prior_xs)
            score = delta if rank_mode == "accel" else -delta
        if rank_mode in ("max_high", "max_low"):
            spike = _max_day_return(cl, int(rank_window), skip_days)
            if spike is None:
                continue
            score = spike if rank_mode == "max_high" else -spike
        if rank_mode in ("liq_high", "liq_low"):
            level = quote_vol(rows, int(rank_window))
            if level <= 0:
                continue
            score = level if rank_mode == "liq_high" else -level
        ranked.append(
            {
                "base": base,
                "excess": xs,
                "qvol": round(qv, 0),
                "prox60": prox,
                "score": score,
            }
        )
    use_score = rank_mode != "excess" or float(near_high_weight or 0.0) > 0
    if use_score:
        ranked.sort(key=lambda r: float(r["score"]), reverse=True)
    else:
        ranked.sort(key=lambda r: float(r["excess"]), reverse=True)
    qualified = [str(r["base"]) for r in ranked if float(r["excess"]) > excess_floor]
    slot = str(rank_slot or "")
    if slot == "second":
        alts = qualified[1:2]
    elif slot == "third":
        alts = qualified[2:3]
    elif slot == "floor":
        cool = [r for r in ranked if float(r["excess"]) > excess_floor]
        cool.sort(key=lambda r: (float(r["excess"]), str(r["base"])))
        alts = [str(cool[0]["base"])] if cool else []
    else:
        alts = qualified[: max(1, int(n_alts))]
    want = alts[0] if alts else "BTC"
    return {
        "want": want,
        "wants": alts,
        "ranked": ranked[:8],
        "excess": {str(row["base"]): float(row["excess"]) for row in ranked},
    }


def apply_alt_allow(
    pick: Mapping[str, Any],
    allowed: set[str] | None,
    *,
    mode: str = "gate",
    excess_floor: float = 0.0,
    n_alts: int = 1,
    excess_override: float | None = None,
) -> list[str]:
    """Keep residual rank. ``allowed is None`` leaves the pick unchanged.

    Modes:
      gate            — residual winner only if it is allowed; else empty
      intersect       — strongest residual names that are allowed + clear floor
      prefer          — winner if allowed; else intersect; else raw residual
      overlap_or_rs   — intersect when any allowed name clears floor; else RS
      override_gate   — like gate, but keep RS winner when excess >= override
    """
    wants = [str(b) for b in (pick.get("wants") or []) if b and b != "BTC"]
    ranked = list(pick.get("ranked") or [])
    n = max(1, int(n_alts))
    if allowed is None:
        return wants[:n]

    def _intersect() -> list[str]:
        return [
            str(row["base"])
            for row in ranked
            if str(row.get("base") or "") in allowed
            and float(row.get("excess") or 0.0) > excess_floor
        ][:n]

    m = str(mode or "gate").lower()
    if m == "intersect":
        return _intersect()
    if m in {"prefer", "soft"}:
        if wants and wants[0] in allowed:
            return wants[:1]
        hit = _intersect()
        return hit if hit else wants[:n]
    if m in {"overlap_or_rs", "overlap"}:
        hit = _intersect()
        return hit if hit else wants[:n]
    if m in {"override_gate", "gate_override"}:
        if wants and wants[0] in allowed:
            return wants[:1]
        if (
            excess_override is not None
            and wants
            and ranked
            and str(ranked[0].get("base") or "") == wants[0]
            and float(ranked[0].get("excess") or 0.0) >= float(excess_override)
        ):
            return wants[:1]
        return []
    # Default hard gate.
    if wants and wants[0] in allowed:
        return wants[:1]
    return []


def _iso_week(date: str) -> str:
    now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _held(book: Book) -> dict[str, str]:
    return {lot.base: lot.role for lot in book.lots.values()}


def _targets(
    *,
    winner: str,
    risk_on: bool,
    btc_frac: float,
    flatten: Flatten,
) -> tuple[str, str]:
    alt = winner if winner != "BTC" else ""
    alt_frac = max(0.0, 1.0 - float(btc_frac))
    if flatten == "all" and not risk_on:
        return "", ""
    if flatten in {"regime", "btc"} and not risk_on:
        return "", alt
    if alt and alt_frac >= 0.05:
        return ("BTC" if btc_frac >= 0.05 else ""), alt
    if flatten == "none" or risk_on:
        return "BTC", ""
    return "", ""


def _targets_multi(
    *,
    winners: Sequence[str],
    risk_on: bool,
    btc_frac: float,
    flatten: Flatten,
) -> tuple[str, tuple[str, ...]]:
    alts = tuple(w for w in winners if w and w != "BTC")
    alt_frac = max(0.0, 1.0 - float(btc_frac))
    if flatten == "all" and not risk_on:
        return "", ()
    if flatten in {"regime", "btc"} and not risk_on:
        return "", alts
    if alts and alt_frac >= 0.05:
        return ("BTC" if btc_frac >= 0.05 else ""), alts
    if flatten == "none" or risk_on:
        return "BTC", ()
    return "", ()


def _spike_reclaim_orders(
    book: Book,
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    policy: ExitPolicy,
    watches: list[dict[str, Any]],
    overlay: Sequence[Order],
    *,
    risk_on: bool,
    alt_frac: float,
    excess_floor: float,
    lookback_days: int,
    skip_days: int,
    n_alts: int,
    require_alt_sma: bool,
    sma_n: int,
    near_high_weight: float = 0.0,
    rank_mode: str = "excess",
    rank_window: int = 20,
) -> list[Order]:
    """Buy a spiked alt back if price reclaims that day's high within the window."""
    days = int(policy.alt_spike_reclaim_days or 0)
    if days <= 0:
        return []
    highs = _px_map(ohlc, date, field=2)
    for order in overlay:
        if order.side != "sell" or order.reason != "alt_spike":
            continue
        high = float(highs.get(order.base) or 0.0)
        if high <= 0:
            continue
        deadline = (
            datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)
        ).strftime("%Y-%m-%d")
        watches[:] = [w for w in watches if w["base"] != order.base]
        watches.append(
            {
                "base": order.base,
                "high": high,
                "signal_date": date,
                "deadline": deadline,
            }
        )
    if not risk_on:
        # A one-day SMA dip must not erase the watch. Buy only while risk is on.
        watches[:] = [w for w in watches if date <= str(w["deadline"])]
        return []
    closes = _px_map(ohlc, date, field=4)
    held_alt = next((lot.base for lot in book.lots.values() if lot.role == "alt"), None)
    still: list[dict[str, Any]] = []
    buys: list[Order] = []
    for watch in watches:
        base = str(watch["base"])
        if date <= str(watch["signal_date"]):
            still.append(watch)
            continue
        if date > str(watch["deadline"]) or held_alt not in (None, base):
            continue
        if base in book.lots:
            continue
        close = float(closes.get(base) or 0.0)
        if close < float(watch["high"]):
            still.append(watch)
            continue
        pick = pick_residual(
            ohlc,
            date,
            excess_floor=excess_floor,
            lookback_days=lookback_days,
            skip_days=skip_days,
            n_alts=n_alts,
            require_alt_sma=require_alt_sma,
            sma_n=sma_n,
            near_high_weight=near_high_weight,
            rank_mode=rank_mode,
            rank_window=rank_window,
        )
        if base not in (pick.get("wants") or []):
            still.append(watch)
            continue
        eq = book.mark(closes)
        buys.append(
            Order(
                side="buy",
                base=base,
                role="alt",
                adv=_adv(ohlc, base, date),
                notional=eq * alt_frac,
                reason="alt_reclaim",
            )
        )
    watches[:] = still
    return buys


def _mark_scale_open(
    book: Book,
    fills: Sequence[Mapping[str, Any]],
    *,
    starter_frac: float,
    add_arm_pct: float,
) -> None:
    """Remember a starter buy, and close the flag once the add fills."""
    if add_arm_pct <= 0 or not (0.0 < starter_frac < 1.0):
        return
    for fill in fills:
        lot = book.lots.get(str(fill.get("base") or ""))
        if lot is None:
            continue
        if fill.get("side") == "buy" and fill.get("reason") == "residual":
            lot.scale_open = True
        elif fill.get("side") == "buy" and fill.get("reason") == "scale_in":
            lot.scale_open = False


def _scale_in_orders(
    book: Book,
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    *,
    add_arm_pct: float,
    alt_frac: float,
    excess_floor: float,
    lookback_days: int,
    skip_days: int,
    n_alts: int,
    require_alt_sma: bool,
    sma_n: int,
    near_high_weight: float,
    skip_bases: set[str],
    rank_mode: str = "excess",
    rank_window: int = 20,
) -> list[Order]:
    """Add the rest of the sleeve once the starter is through the arm.

    The name must still be the residual leader on this close. The order
    fills next open. Entry price is left on the first fill.
    """
    if add_arm_pct <= 0:
        return []
    closes = _px_map(ohlc, date, field=4)
    pick = pick_residual(
        ohlc,
        date,
        excess_floor=excess_floor,
        lookback_days=lookback_days,
        skip_days=skip_days,
        n_alts=n_alts,
        require_alt_sma=require_alt_sma,
        sma_n=sma_n,
        near_high_weight=near_high_weight,
        rank_mode=rank_mode,
        rank_window=rank_window,
    )
    leaders = {str(b) for b in (pick.get("wants") or [])}
    orders: list[Order] = []
    eq = book.mark(closes)
    for lot in book.lots.values():
        if lot.role != "alt" or not lot.scale_open or lot.base in skip_bases:
            continue
        if lot.base not in leaders or lot.entry_px <= 0:
            continue
        close = float(closes.get(lot.base) or 0.0)
        if close <= 0 or close / lot.entry_px - 1.0 < add_arm_pct:
            continue
        held = lot.qty * close
        add_n = eq * max(0.0, alt_frac) - held
        if add_n < 50.0:
            lot.scale_open = False
            continue
        orders.append(
            Order(
                side="buy",
                base=lot.base,
                role="alt",
                adv=_adv(ohlc, lot.base, date),
                notional=add_n,
                reason="scale_in",
                keep_entry=True,
            )
        )
    return orders


def run_btc_residual(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    btc_frac: float = 0.75,
    excess_floor: float = 0.0,
    flatten: Flatten = "none",
    sma_n: int = 50,
    rebalance_days: int = 7,
    lookback_days: int = 20,
    skip_days: int = 1,
    n_alts: int = 1,
    require_alt_sma: bool = False,
    model: FillModel = WET,
    policy: ExitPolicy | None = None,
    keep_curve: bool = False,
    keep_weeks: bool = False,
    keep_trades: bool = False,
    strategy: str = "",
    rearm_mode: str = "",
    btc_tide_floor: float | None = None,
    green_pause_days: int = 0,
    universe_top_n: int = 0,
    alt_allow: Mapping[str, set[str]] | None = None,
    alt_allow_mode: str = "gate",
    alt_allow_excess_override: float | None = None,
    cash_when_no_alt: bool = False,
    rebalance_weekday: int | None = None,
    immediate_rebuy: bool = False,
    near_high_weight: float = 0.0,
    entry_gate: Callable[[str, str], bool] | None = None,
    alt_size_scale: Callable[[str, str], float] | None = None,
    exit_cooldown_days: int | None = None,
    starter_frac: float = 1.0,
    add_arm_pct: float = 0.0,
    rank_mode: str = "excess",
    rank_window: int = 20,
    btc_veto_gap: float | None = None,
    rank_slot: str = "",
    gap_cap: float | None = None,
    lead_exit: str = "",
    keep_ride: str = "",
    loss_ban_days: int = 0,
    agree_days: int = 0,
    vol_mult: float = 0.0,
    cross_days: int = 0,
    lead_wide: float = 0.0,
    crowd_side: str = "",
    crowd_n: int = 0,
    gap_down: float | None = None,
    rotate_gap: float = 0.0,
    btc_ext_side: str = "",
    btc_ext: float = 0.0,
    own_floor: float | None = None,
    equity_sma: int = 0,
    break_mode: str = "",
    break_days: int = 0,
) -> dict[str, Any]:
    """One book: BTC fraction + residual winner on the rest.

    ``entry_gate(date, base)`` returning False drops that winner on the
    weekly clock (the book goes to cash and re-checks daily, like
    ``cash_when_no_alt``). ``alt_size_scale(date, base)`` scales the alt buy
    notional into [0, 1]; the rest stays cash. Both are generic overlays for
    regime, sizing and positioning research.

    ``btc_frac=0`` is 100% residual weekly. ``btc_frac=1`` is BTC-only
    (SMA50 cash if flatten is all/regime).

    Sizing uses marked equity each rebalance (compound / winst herbelegd).
    ``rebalance_weekday`` (0=Mon … 6=Sun) matches live week-clock pin.

    ``immediate_rebuy``: after a trail/overlay sell, queue a next-open buy of
    the best qualifying alt **excluding** just-sold bases (no 7d cash wait).

    ``starter_frac`` < 1 with ``add_arm_pct`` > 0 buys that fraction first and
    adds the rest, once, when the close is that far above the first fill and
    the name is still the residual leader. The add does not move the entry
    the lock uses, and it does not restart the week clock.
    """
    name = strategy or f"btc{int(btc_frac * 100)}_res_f{flatten}"
    dates = [bar_date(r) for r in (ohlc.get("BTC") or [])]
    book = Book(book_eur)
    pending: list[Order] = []
    equity: list[float] = []
    eq_dates: list[str] = []
    holds: list[str] = []
    trades: list[dict[str, Any]] = []
    last_reb = 0
    loss_bans: dict[str, int] = {}
    want_btc = ""
    want_alts: tuple[str, ...] = ()
    n_rotate = 0
    n_overlay = 0
    n_immediate_rebuy = 0
    overlay_reasons: dict[str, int] = {}
    reclaim_watches: list[dict[str, Any]] = []
    need = int(lookback_days) + int(skip_days) + 1
    alt_frac = max(0.0, 1.0 - float(btc_frac))
    wd_pin = (
        int(rebalance_weekday)
        if rebalance_weekday is not None and int(rebalance_weekday) >= 0
        else None
    )

    for date in dates:
        if date < start or date > end:
            continue
        now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        now_ms = int(now.timestamp() * 1000)
        if model.delay_next_open and pending:
            if gap_cap is not None and float(gap_cap) > 0:
                pending = drop_gapped_buys(pending, ohlc, date, float(gap_cap))
            if gap_down is not None and float(gap_down) > 0:
                pending = drop_down_gaps(pending, ohlc, date, float(gap_down))
            if pending:
                fills = _apply_orders(book, pending, ohlc, date, model, opened_ms=now_ms)
                trades.extend(fills)
                _mark_scale_open(book, fills, starter_frac=starter_frac, add_arm_pct=add_arm_pct)
                for fill in fills:
                    if fill.get("reason") == "alt_reclaim":
                        lot = book.lots.get(str(fill["base"]))
                        if lot is not None:
                            lot.spike_done = True
            pending = []
        overlay: list[Order] = []
        overlay_sold: set[str] = set()
        rearm_hit = False
        if policy is not None:
            overlay = _overlay_orders(book, ohlc, date, policy, now_ms)
            if policy.alt_red_cut_if_leader:
                sleeve = (
                    top_volume_universe(ohlc, date, universe_top_n)
                    if universe_top_n > 0
                    else DEFAULT_UNIVERSE
                )
                lead = pick_residual(
                    ohlc,
                    date,
                    universe=sleeve,
                    excess_floor=excess_floor,
                    lookback_days=lookback_days,
                    skip_days=skip_days,
                    n_alts=1,
                    require_alt_sma=require_alt_sma,
                    sma_n=sma_n,
                    near_high_weight=near_high_weight,
                    rank_mode=rank_mode,
                    rank_window=rank_window,
                )
                wants = list(lead.get("wants") or [])
                overlay = strip_red_cut_while_leader(overlay, wants[0] if wants else "")
            if lead_exit:
                sleeve = (
                    top_volume_universe(ohlc, date, universe_top_n)
                    if universe_top_n > 0
                    else DEFAULT_UNIVERSE
                )
                lead = pick_residual(
                    ohlc,
                    date,
                    universe=sleeve,
                    excess_floor=excess_floor,
                    lookback_days=lookback_days,
                    skip_days=skip_days,
                    n_alts=1,
                    require_alt_sma=require_alt_sma,
                    sma_n=sma_n,
                    near_high_weight=near_high_weight,
                    rank_mode=rank_mode,
                    rank_window=rank_window,
                )
                wants = list(lead.get("wants") or [])
                leader = wants[0] if wants else ""
                excess_by = dict(lead.get("excess") or {})
                closes = _px_map(ohlc, date, field=4)
                selling = {o.base for o in overlay if o.side == "sell"}
                for lot in list(book.lots.values()):
                    if lot.role != "alt" or lot.base in selling:
                        continue
                    close = float(closes.get(lot.base) or 0.0)
                    if close <= 0:
                        continue
                    if not lead_lost(
                        lead_exit,
                        base=lot.base,
                        leader=leader,
                        excess=excess_by.get(lot.base),
                        close=close,
                        entry=lot.entry_px,
                        floor=excess_floor,
                    ):
                        continue
                    overlay.append(
                        Order(
                            side="sell",
                            base=lot.base,
                            role="alt",
                            adv=_adv(ohlc, lot.base, date),
                            reason="alt_lead_exit",
                        )
                    )
            if lead_wide > 0:
                sleeve = (
                    top_volume_universe(ohlc, date, universe_top_n)
                    if universe_top_n > 0
                    else DEFAULT_UNIVERSE
                )
                wide_pick = pick_residual(
                    ohlc,
                    date,
                    universe=sleeve,
                    excess_floor=excess_floor,
                    lookback_days=lookback_days,
                    skip_days=skip_days,
                    n_alts=1,
                    require_alt_sma=require_alt_sma,
                    sma_n=sma_n,
                    near_high_weight=near_high_weight,
                    rank_mode=rank_mode,
                    rank_window=rank_window,
                )
                wide_wants = list(wide_pick.get("wants") or [])
                wide_leader = wide_wants[0] if wide_wants else ""
                wide_closes = _px_map(ohlc, date, field=4)
                wide_peaks = {lot.base: lot.peak_px for lot in book.lots.values()}
                overlay = widen_trail_while_leader(
                    overlay,
                    wide_leader,
                    wide=lead_wide,
                    peaks=wide_peaks,
                    closes=wide_closes,
                )
            overlay_sold = {o.base for o in overlay if o.side == "sell"}
            n_overlay += sum(1 for o in overlay if o.side == "sell")
            for o in overlay:
                if o.reason:
                    overlay_reasons[o.reason] = overlay_reasons.get(o.reason, 0) + 1
            closes_now = _px_map(ohlc, date, field=4) if overlay_sold else {}
            if loss_ban_days > 0 and overlay_sold:
                for order in overlay:
                    if order.side != "sell" or order.qty > 0:
                        continue
                    lot = book.lots.get(order.base)
                    close = float(closes_now.get(order.base) or 0.0)
                    entry = lot.entry_px if lot is not None else 0.0
                    if losing_exit(close, entry):
                        loss_bans[order.base] = now_ms + int(loss_ban_days) * DAY_MS
            if overlay_sold and rearm_mode:
                rearm_hit = rearm_after_exit(book, overlay, closes_now, rearm_mode)
            pause_days = int(green_pause_days or 0)
            pause_hit = False
            if overlay_sold and pause_days > int(rebalance_days):
                pause_hit = rearm_after_exit(book, overlay, closes_now, "green")
            if overlay_sold:
                want_alts = tuple(a for a in want_alts if a not in overlay_sold)
                # Default: a trail/lock sell restarts the full week clock.
                # ``exit_cooldown_days`` shortens that wait to the next pick.
                # ``green_next`` shortens it only when the close is still
                # above the entry. ``green_pause_days`` lengthens it, and
                # only then. A losing exit keeps the full week.
                if pause_hit:
                    last_reb = now_ms + (pause_days - int(rebalance_days)) * DAY_MS
                elif exit_cooldown_days is None and not (
                    rearm_hit and rearm_mode == "green_next"
                ):
                    last_reb = now_ms
                else:
                    wait = 1 if rearm_hit and rearm_mode == "green_next" else max(
                        0, int(exit_cooldown_days or 0)
                    )
                    last_reb = now_ms - (int(rebalance_days) - wait) * DAY_MS
            # A touch stop traded during the bar. Book it at that price now
            # so the next-open queue does not sell the same lot again.
            same_bar = [o for o in overlay if o.side == "sell" and o.same_bar_px > 0]
            if same_bar:
                trades.extend(
                    _apply_orders(book, same_bar, ohlc, date, model, opened_ms=now_ms)
                )
                overlay = [o for o in overlay if o not in same_bar]
        btc_c = closes_of(rows_through(ohlc.get("BTC") or [], date))
        s50 = sma(btc_c, sma_n)
        last = float(btc_c[-1]) if btc_c else 0.0
        risk_on = btc_risk_on(last, s50, gap=btc_veto_gap)
        uni = (
            top_volume_universe(ohlc, date, universe_top_n)
            if universe_top_n > 0
            else DEFAULT_UNIVERSE
        )
        active_bans = {base for base, until in loss_bans.items() if until > now_ms}
        # Immediate rebuy after trail/overlay: same next-open as the sell, skip
        # the sold name(s). Weekly rotate path below still handles due clocks.
        early = immediate_rebuy or (rearm_hit and rearm_mode in ("lock", "green"))
        if (
            early
            and overlay_sold
            and risk_on
            and float(btc_frac) <= 0.0
            and len(btc_c) >= need
        ):
            pick_now = pick_residual(
                ohlc,
                date,
                universe=uni,
                excess_floor=excess_floor,
                lookback_days=lookback_days,
                skip_days=skip_days,
                n_alts=n_alts,
                require_alt_sma=require_alt_sma,
                sma_n=sma_n,
                exclude_bases=sorted(set(overlay_sold) | active_bans),
                near_high_weight=near_high_weight,
                rank_mode=rank_mode,
                rank_window=rank_window,
                rank_slot=rank_slot,
                agree_days=agree_days,
                vol_mult=vol_mult,
                cross_days=cross_days,
                own_floor=own_floor,
                break_mode=break_mode,
                break_days=break_days,
            )
            winners = list(pick_now.get("wants") or [])
            if winners:
                px = _px_map(ohlc, date, field=4)
                eq = book.mark(px)
                # After pending sells fill next open, notion ≈ current mark eq.
                for alt in winners:
                    if alt in overlay_sold:
                        continue
                    overlay.append(
                        Order(
                            side="buy",
                            base=alt,
                            role="alt",
                            adv=_adv(ohlc, alt, date),
                            notional=eq / max(1, len(winners)),
                            reason="immediate_rebuy",
                        )
                    )
                    n_immediate_rebuy += 1
                want_alts = tuple(winners)
                want_btc = ""
                last_reb = now_ms
        age_due = last_reb <= 0 or (now_ms - last_reb) >= rebalance_days * DAY_MS
        due = age_due and now.weekday() == wd_pin if wd_pin is not None else age_due
        if due and len(btc_c) >= need:
            pick = pick_residual(
                ohlc,
                date,
                universe=uni,
                excess_floor=excess_floor,
                lookback_days=lookback_days,
                skip_days=skip_days,
                n_alts=n_alts,
                require_alt_sma=require_alt_sma,
                sma_n=sma_n,
                near_high_weight=near_high_weight,
                rank_mode=rank_mode,
                rank_window=rank_window,
                rank_slot=rank_slot,
                agree_days=agree_days,
                vol_mult=vol_mult,
                cross_days=cross_days,
                own_floor=own_floor,
                break_mode=break_mode,
                break_days=break_days,
                exclude_bases=sorted(active_bans) or None,
            )
            if alt_allow is not None and date in alt_allow:
                pick = dict(pick)
                pick["wants"] = apply_alt_allow(
                    pick,
                    set(alt_allow[date]),
                    mode=alt_allow_mode,
                    excess_floor=excess_floor,
                    n_alts=n_alts,
                    excess_override=alt_allow_excess_override,
                )
            if entry_gate is not None:
                pick = dict(pick)
                pick["wants"] = [
                    str(b) for b in (pick.get("wants") or []) if entry_gate(date, str(b))
                ]
            last_reb = now_ms
            px = _px_map(ohlc, date, field=4)
            eq = book.mark(px)
            held = _held(book)
            next_btc, next_alts = _targets_multi(
                winners=list(pick.get("wants") or []),
                risk_on=risk_on,
                btc_frac=btc_frac,
                flatten=flatten,
            )
            if btc_tide_floor is not None:
                r_btc = _window_return(btc_c, lookback_days, skip_days)
                tide_ok = r_btc is not None and r_btc + 1e-12 >= float(btc_tide_floor)
                held_alts = [
                    b for b, role in held.items() if role == "alt" and b not in overlay_sold
                ]
                next_alts = tide_alts(next_alts, held_alts, tide_ok=tide_ok)
                if not tide_ok:
                    next_btc = ""
            if keep_ride and not risk_on:
                held_alts = [
                    b for b, role in held.items() if role == "alt" and b not in overlay_sold
                ]
                leader = str((pick.get("wants") or [""])[0])
                kept = []
                for base in held_alts:
                    lot = book.lots.get(base)
                    close = float(px.get(base) or 0.0)
                    entry = lot.entry_px if lot is not None else 0.0
                    if ride_to_keep(
                        keep_ride,
                        green=entry > 0 and close > entry,
                        is_leader=base == leader and leader != "",
                    ):
                        kept.append(base)
                if kept:
                    next_alts = tuple(kept)
                    next_btc = ""
            if crowd_side and crowd_n > 0:
                n_qual = sum(
                    1
                    for value in (pick.get("excess") or {}).values()
                    if float(value) > float(excess_floor) + 1e-12
                )
                held_alts = [
                    b for b, role in held.items() if role == "alt" and b not in overlay_sold
                ]
                next_alts = crowd_alts(
                    next_alts,
                    held_alts,
                    count=n_qual,
                    bound=int(crowd_n),
                    side=crowd_side,
                )
                if not next_alts:
                    next_btc = ""
            if rotate_gap > 0:
                held_alts = [
                    b for b, role in held.items() if role == "alt" and b not in overlay_sold
                ]
                next_alts = hold_unless_gap(
                    next_alts,
                    held_alts,
                    dict(pick.get("excess") or {}),
                    gap=float(rotate_gap),
                )
                if not next_alts:
                    next_btc = ""
            if btc_ext_side:
                dist = last / s50 - 1.0 if s50 and s50 > 0 and last > 0 else None
                allow = extension_allows(dist, side=btc_ext_side, bound=float(btc_ext))
                held_alts = [
                    b for b, role in held.items() if role == "alt" and b not in overlay_sold
                ]
                next_alts = block_fresh(next_alts, held_alts, allow=allow)
                if not next_alts:
                    next_btc = ""
            if equity_sma > 0:
                allow_eq = True
                if len(equity) >= int(equity_sma):
                    window = equity[-int(equity_sma) :]
                    allow_eq = eq + 1e-9 >= sum(window) / float(len(window))
                held_alts = [
                    b for b, role in held.items() if role == "alt" and b not in overlay_sold
                ]
                next_alts = block_fresh(next_alts, held_alts, allow=allow_eq)
                if not next_alts:
                    next_btc = ""
            changed = (next_btc != want_btc) or (next_alts != want_alts) or not held
            if changed and (next_btc or next_alts or held):
                orders: list[Order] = []
                for base, role in list(held.items()):
                    keep = (role == "btc" and base == next_btc) or (
                        role == "alt" and base in next_alts
                    )
                    if not keep:
                        orders.append(
                            Order(
                                side="sell",
                                base=base,
                                role=role,
                                adv=_adv(ohlc, base, date),
                                reason="rotate",
                            )
                        )
                if next_btc and held.get("BTC") != "btc":
                    full_btc = not next_alts and not cash_when_no_alt
                    notion = eq if full_btc else eq * btc_frac
                    orders.append(
                        Order(
                            side="buy",
                            base="BTC",
                            role="btc",
                            adv=_adv(ohlc, "BTC", date),
                            notional=notion,
                            reason="btc",
                        )
                    )
                split = max(1, len(next_alts))
                for alt in next_alts:
                    if held.get(alt) == "alt" or alt in overlay_sold:
                        continue
                    notion = (eq if not next_btc else eq * alt_frac) / split
                    if alt_size_scale is not None:
                        notion *= max(0.0, min(1.0, float(alt_size_scale(date, alt))))
                    if add_arm_pct > 0 and 0.0 < starter_frac < 1.0:
                        notion *= starter_frac
                    if notion <= 0:
                        continue
                    orders.append(
                        Order(
                            side="buy",
                            base=alt,
                            role="alt",
                            adv=_adv(ohlc, alt, date),
                            notional=notion,
                            reason="residual",
                        )
                    )
                merged = overlay + orders
                if merged:
                    n_rotate += int(bool(orders))
                    if model.delay_next_open:
                        pending = merged
                    else:
                        filled = _apply_orders(book, merged, ohlc, date, model, opened_ms=now_ms)
                        trades.extend(filled)
                        _mark_scale_open(
                            book, filled, starter_frac=starter_frac, add_arm_pct=add_arm_pct
                        )
                    overlay = []
            want_btc, want_alts = next_btc, next_alts
            if overlay_sold:
                want_alts = tuple(a for a in want_alts if a not in overlay_sold)
            if not next_btc and not next_alts:
                last_reb = 0
        if overlay:
            if model.delay_next_open:
                pending = overlay
            else:
                trades.extend(_apply_orders(book, overlay, ohlc, date, model, opened_ms=now_ms))
        if policy is not None and int(policy.alt_spike_reclaim_days or 0) > 0:
            reclaim = _spike_reclaim_orders(
                book,
                ohlc,
                date,
                policy,
                reclaim_watches,
                overlay,
                risk_on=risk_on,
                alt_frac=alt_frac,
                excess_floor=excess_floor,
                lookback_days=lookback_days,
                skip_days=skip_days,
                n_alts=n_alts,
                require_alt_sma=require_alt_sma,
                sma_n=sma_n,
                near_high_weight=near_high_weight,
                rank_mode=rank_mode,
                rank_window=rank_window,
            )
            for order in reclaim:
                overlay_reasons[order.reason] = overlay_reasons.get(order.reason, 0) + 1
            if reclaim:
                if model.delay_next_open:
                    pending = list(pending) + reclaim
                else:
                    fills = _apply_orders(book, reclaim, ohlc, date, model, opened_ms=now_ms)
                    trades.extend(fills)
                    for fill in fills:
                        if fill.get("reason") == "alt_reclaim":
                            lot = book.lots.get(str(fill["base"]))
                            if lot is not None:
                                lot.spike_done = True
        if add_arm_pct > 0 and 0.0 < starter_frac < 1.0 and risk_on:
            busy = {o.base for o in pending if o.side == "sell"}
            busy |= overlay_sold
            adds = _scale_in_orders(
                book,
                ohlc,
                date,
                add_arm_pct=add_arm_pct,
                alt_frac=alt_frac,
                excess_floor=excess_floor,
                lookback_days=lookback_days,
                skip_days=skip_days,
                n_alts=n_alts,
                require_alt_sma=require_alt_sma,
                sma_n=sma_n,
                near_high_weight=near_high_weight,
                skip_bases=busy | {o.base for o in pending},
                rank_mode=rank_mode,
                rank_window=rank_window,
            )
            if adds:
                if model.delay_next_open:
                    pending = list(pending) + adds
                else:
                    filled = _apply_orders(book, adds, ohlc, date, model, opened_ms=now_ms)
                    trades.extend(filled)
                    _mark_scale_open(
                        book, filled, starter_frac=starter_frac, add_arm_pct=add_arm_pct
                    )
        px = _px_map(ohlc, date, field=4)
        equity.append(book.mark(px))
        eq_dates.append(date)
        holds.append(
            ",".join(sorted(f"{lot.role}:{lot.base}" for lot in book.lots.values())) or "cash"
        )

    hold = holds[-1] if holds else "cash"
    extra: dict[str, Any] = {
        "end_hold": hold,
        "n_rotates": n_rotate,
        "year_pnl": year_pnl(eq_dates, equity, book_eur),
        "btc_frac": btc_frac,
        "excess_floor": excess_floor,
        "flatten": flatten,
        "sma_n": sma_n,
        "lookback_days": lookback_days,
        "skip_days": skip_days,
        "rebalance_days": rebalance_days,
        "n_alts": int(n_alts),
        "require_alt_sma": bool(require_alt_sma),
        "n_overlay_exits": n_overlay,
        "n_immediate_rebuy": n_immediate_rebuy,
        "immediate_rebuy": bool(immediate_rebuy),
        "near_high_weight": float(near_high_weight or 0.0),
        "overlay_reasons": overlay_reasons,
        "exit_policy": None if policy is None else policy.name,
    }
    if keep_weeks:
        extra["weeks"] = _weeks(eq_dates, equity, holds, book_eur)
    out = {
        "strategy": name,
        "model": model.name,
        "held": hold,
        "trades_tail": trades[-12:],
        **({"trades": trades} if keep_trades else {}),
        **metrics(
            equity,
            start_eur=book_eur,
            n_trades=len(trades),
            n_days=len(equity),
            extra=extra,
        ),
    }
    if keep_curve:
        out["curve"] = [[d, round(v, 2)] for d, v in zip(eq_dates, equity, strict=False)]
    return out


def _weeks(
    dates: Sequence[str],
    equity: Sequence[float],
    holds: Sequence[str],
    start_eur: float,
) -> list[dict[str, Any]]:
    by: dict[str, list[tuple[str, float, str]]] = {}
    for d, e, h in zip(dates, equity, holds, strict=False):
        by.setdefault(_iso_week(d), []).append((d, e, h))
    rows: list[dict[str, Any]] = []
    prev = start_eur
    for week in by:
        pts = by[week]
        end_e = pts[-1][1]
        rows.append(
            {
                "week": week,
                "start": pts[0][0],
                "end": pts[-1][0],
                "hold": pts[-1][2],
                "end_eur": round(end_e, 2),
                "pnl_eur": round(end_e - prev, 2),
                "min_eur": round(min(v for _, v, _ in pts), 2),
                "max_eur": round(max(v for _, v, _ in pts), 2),
            }
        )
        prev = end_e
    return rows


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"curve", "trades_tail"}}


def run_mix_scan(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
) -> dict[str, Any]:
    live = ExitPolicy(name="live")
    clip = run_clip_exits(
        ohlc, live, start=start, end=end, book_eur=book_eur, model=model, keep_curve=True
    )
    specs: list[tuple[str, float, float, Flatten]] = [
        ("residual_100", 0.0, 0.0, "none"),
        ("btc_sma50", 1.0, 0.0, "all"),
        ("btc75_res25_flat", 0.75, 0.0, "all"),
        ("btc75_res25_hold", 0.75, 0.0, "none"),
        ("btc50_res50_flat", 0.50, 0.0, "all"),
        ("btc50_res50_hold", 0.50, 0.0, "none"),
        ("btc75_res25_regime", 0.75, 0.0, "regime"),
        ("btc50_res50_regime", 0.50, 0.0, "regime"),
        ("btc75_res25_floor8", 0.75, 0.08, "all"),
    ]
    rows: dict[str, Any] = {"clip_live": _strip(clip)}
    for name, frac, floor, flat in specs:
        row = run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            btc_frac=frac,
            excess_floor=floor,
            flatten=flat,
            model=model,
            keep_curve=True,
            keep_weeks=True,
            strategy=name,
        )
        rows[name] = _strip(row)
    return {"start": start, "end": end, "book_eur": book_eur, "model": model.name, **rows}


def MIX_EXIT_POLICIES() -> list[ExitPolicy]:
    """Overlays on the alt sleeve only. BTC still follows SMA50 / weekly mix."""
    rows = [
        ExitPolicy(name="none"),
        ExitPolicy(name="alt_stop_8", alt_stop_pct=0.08),
        ExitPolicy(name="alt_stop_12", alt_stop_pct=0.12),
        ExitPolicy(name="alt_stop_15", alt_stop_pct=0.15),
        ExitPolicy(name="alt_stop_20", alt_stop_pct=0.20),
        ExitPolicy(name="alt_trail_8", alt_trail_pct=0.08),
        ExitPolicy(name="alt_trail_10", alt_trail_pct=0.10),
        ExitPolicy(name="alt_trail_12", alt_trail_pct=0.12),
        ExitPolicy(name="alt_trail_15", alt_trail_pct=0.15),
        ExitPolicy(name="alt_tp_15", alt_tp_pct=0.15),
        ExitPolicy(name="alt_tp_20", alt_tp_pct=0.20),
        ExitPolicy(name="alt_tp_30", alt_tp_pct=0.30),
        ExitPolicy(name="alt_time_14", alt_max_days=14),
        ExitPolicy(name="alt_donch_10", alt_donch_n=10),
        ExitPolicy(name="alt_excess_lte_0", alt_min_excess=0.0),
        ExitPolicy(name="alt_stop8_fold", alt_stop_pct=0.08, fold_alt_to_btc=True),
        ExitPolicy(name="alt_trail12_fold", alt_trail_pct=0.12, fold_alt_to_btc=True),
        ExitPolicy(name="alt_partial_tp15", alt_partial_tp_pct=0.15, alt_partial_frac=0.5),
    ]
    return rows


def run_exit_scan(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    btc_frac: float = 0.5,
    flatten: Flatten = "all",
    model: FillModel = WET,
) -> dict[str, Any]:
    live: dict[str, Any] | None = None
    ranked: list[dict[str, Any]] = []
    for policy in MIX_EXIT_POLICIES():
        row = run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            btc_frac=btc_frac,
            excess_floor=0.0,
            flatten=flatten,
            model=model,
            policy=policy,
            strategy=f"btc{int(btc_frac * 100)}_{flatten}_{policy.name}",
        )
        slim = _strip(row)
        if policy.name == "none":
            live = slim
        ranked.append(slim)
    base_pnl = float((live or {}).get("pnl_eur") or 0.0)
    ranked.sort(key=lambda r: (-float(r["calmar"]), -float(r["pnl_eur"])))
    for row in ranked:
        row["delta_vs_none"] = round(float(row["pnl_eur"]) - base_pnl, 2)
    return {
        "start": start,
        "end": end,
        "book_eur": book_eur,
        "btc_frac": btc_frac,
        "flatten": flatten,
        "none": live,
        "ranked": ranked,
    }


TRAIL10 = ExitPolicy(name="alt_trail_10", alt_trail_pct=0.10)
TRAIL8 = ExitPolicy(name="alt_trail_8", alt_trail_pct=0.08)
TRAIL12 = ExitPolicy(name="alt_trail_12", alt_trail_pct=0.12)


def owner_pack_specs() -> list[dict[str, Any]]:
    """Directional €20k owner packs on the residual/clip tape.

    Not a claim of all strategies — HFT/arb/funding/15m/AlphaI stay out.
    """
    specs: list[dict[str, Any]] = []
    for frac in (0.0, 0.25, 0.4, 0.5, 0.6, 0.75, 1.0):
        for policy in (None, TRAIL10):
            tag = "none" if policy is None else policy.name
            specs.append(
                {
                    "family": "residual_mix",
                    "name": f"btc{int(frac * 100)}_flat50_{tag}",
                    "btc_frac": frac,
                    "flatten": "all",
                    "sma_n": 50,
                    "lookback_days": 20,
                    "skip_days": 1,
                    "rebalance_days": 7,
                    "policy": policy,
                }
            )
    for frac in (0.5, 0.75):
        for policy in (None, TRAIL10):
            tag = "none" if policy is None else policy.name
            specs.append(
                {
                    "family": "residual_mix",
                    "name": f"btc{int(frac * 100)}_neverflat_{tag}",
                    "btc_frac": frac,
                    "flatten": "none",
                    "sma_n": 50,
                    "lookback_days": 20,
                    "skip_days": 1,
                    "rebalance_days": 7,
                    "policy": policy,
                }
            )
    for sma_n in (20, 100, 200):
        specs.append(
            {
                "family": "residual_mix",
                "name": f"btc50_flat{sma_n}_alt_trail_10",
                "btc_frac": 0.5,
                "flatten": "all",
                "sma_n": sma_n,
                "lookback_days": 20,
                "skip_days": 1,
                "rebalance_days": 7,
                "policy": TRAIL10,
            }
        )
    for lb, skip in ((10, 1), (40, 1), (60, 5)):
        specs.append(
            {
                "family": "residual_mix",
                "name": f"btc50_flat50_lb{lb}_alt_trail_10",
                "btc_frac": 0.5,
                "flatten": "all",
                "sma_n": 50,
                "lookback_days": lb,
                "skip_days": skip,
                "rebalance_days": 7,
                "policy": TRAIL10,
            }
        )
    for reb in (14, 30):
        specs.append(
            {
                "family": "residual_mix",
                "name": f"btc50_flat50_reb{reb}_alt_trail_10",
                "btc_frac": 0.5,
                "flatten": "all",
                "sma_n": 50,
                "lookback_days": 20,
                "skip_days": 1,
                "rebalance_days": reb,
                "policy": TRAIL10,
            }
        )
    for policy in (TRAIL8, TRAIL12):
        specs.append(
            {
                "family": "residual_mix",
                "name": f"btc50_flat50_{policy.name}",
                "btc_frac": 0.5,
                "flatten": "all",
                "sma_n": 50,
                "lookback_days": 20,
                "skip_days": 1,
                "rebalance_days": 7,
                "policy": policy,
            }
        )
    specs.append(
        {
            "family": "residual_mix",
            "name": "btc100_flat200_none",
            "btc_frac": 1.0,
            "flatten": "all",
            "sma_n": 200,
            "lookback_days": 20,
            "skip_days": 1,
            "rebalance_days": 7,
            "policy": None,
        }
    )
    specs.append(
        {
            "family": "residual_mix",
            "name": "btc100_neverflat_none",
            "btc_frac": 1.0,
            "flatten": "none",
            "sma_n": 50,
            "lookback_days": 20,
            "skip_days": 1,
            "rebalance_days": 7,
            "policy": None,
        }
    )
    return specs


def run_owner_grid(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
    include_clip: bool = True,
    include_donch: bool = True,
) -> dict[str, Any]:
    """Rank directional owner packs by Calmar. Research only — not live."""
    ranked: list[dict[str, Any]] = []
    if include_clip:
        live = ExitPolicy(name="live")
        clip = run_clip_exits(
            ohlc, live, start=start, end=end, book_eur=book_eur, model=model
        )
        row = _strip(clip)
        row["family"] = "clip_live"
        row["name"] = "clip_live_75_25_floor8"
        ranked.append(row)
    if include_donch:
        from bot.live.momentum_donchian import loop_sleeve_configs
        from bot.research.clip_donch_mix.engine import run_donchian_sleeve

        for cfg in loop_sleeve_configs():
            don = run_donchian_sleeve(
                ohlc, cfg, start=start, end=end, book_eur=book_eur, model=model
            )
            row = _strip(don)
            row["family"] = "donchian"
            row["name"] = f"donch20k_{cfg.name}"
            ranked.append(row)
    for spec in owner_pack_specs():
        row = run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            btc_frac=float(spec["btc_frac"]),
            excess_floor=0.0,
            flatten=spec["flatten"],
            sma_n=int(spec["sma_n"]),
            lookback_days=int(spec["lookback_days"]),
            skip_days=int(spec["skip_days"]),
            rebalance_days=int(spec["rebalance_days"]),
            model=model,
            policy=spec.get("policy"),
            strategy=str(spec["name"]),
        )
        slim = _strip(row)
        slim["family"] = spec["family"]
        slim["name"] = spec["name"]
        ranked.append(slim)
    ranked.sort(key=lambda r: (-float(r.get("calmar") or 0.0), -float(r.get("pnl_eur") or 0.0)))
    champ = ranked[0] if ranked else {}
    for row in ranked:
        row["delta_calmar_vs_top"] = round(
            float(row.get("calmar") or 0.0) - float(champ.get("calmar") or 0.0), 3
        )
        row["delta_pnl_vs_top"] = round(
            float(row.get("pnl_eur") or 0.0) - float(champ.get("pnl_eur") or 0.0), 2
        )
    return {
        "start": start,
        "end": end,
        "book_eur": book_eur,
        "model": model.name,
        "n_packs": len(ranked),
        "best": champ.get("name"),
        "best_calmar": champ.get("calmar"),
        "ranked": ranked,
    }

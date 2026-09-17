"""Daily Momentum Desk — pure decision core shared by backtest and live.

One strategy, one timeframe, one exit rule:

* Regime filter: trade only when BTC 24h return and alt breadth are healthy.
* Entry: once per decision hour, rank alts by 24h excess return vs BTC,
  require proximity to the 24h high and real volume, take the top N.
* Exit: trailing stop from peak on closed 15m bars, a hard stop from entry,
  and a time exit when the position is not above break-even after N hours.
* Risk: day / week realized loss limits pause new entries.

Everything in this module is deterministic and side-effect free so the
backtester (``bot.research.momentum_backtest``) and the live runner
(``bot.live.momentum_runner``) execute exactly the same code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from bot.live.momentum_trade_outcomes import build_entry_ctx

BAR_MS = 15 * 60 * 1000
BARS_PER_DAY = 96

# (ts_ms, open, high, low, close, volume_base)
Candle = Sequence[float]

DEFAULT_UNIVERSE: tuple[str, ...] = (
    "ETH",
    "SOL",
    "XRP",
    "ADA",
    "DOGE",
    "LINK",
    "DOT",
    "AVAX",
    "LTC",
    "NEAR",
    "ATOM",
    "OP",
    "ARB",
    "SUI",
    "FET",
    "UNI",
)

DEFAULT_CLUSTERS: dict[str, str] = {
    "SOL": "L1",
    "AVAX": "L1",
    "NEAR": "L1",
    "SUI": "L1",
    "DOT": "L1",
    "ATOM": "L1",
    "ADA": "L1",
    "ETH": "ETH",
    "ARB": "L2",
    "OP": "L2",
    "LINK": "DEFI",
    "UNI": "DEFI",
    "FET": "AI",
    "LTC": "PAY",
    "XRP": "PAY",
    "DOGE": "MEME",
}


@dataclass(frozen=True)
class DeskConfig:
    decision_hours_utc: tuple[int, ...] = (0,)
    clip_eur: float = 500.0
    alphai_clip_mult: float = 1.3
    # Size overlay: ``binary`` = flat alphai_clip_mult on every pick (legacy).
    # ``conviction`` = scale between alphai_clip_mult_min and alphai_clip_mult
    # using score/rank + headline conflict + optional price-confirm/reliability.
    # Does not change membership gates — only clip size for AlphaI picks.
    alphai_size_mode: str = "conviction"
    alphai_clip_mult_min: float = 1.0
    alphai_conflict_damp: float = 0.55
    alphai_stale_minutes: float = 45.0
    alphai_price_confirm_sizing: bool = True
    alphai_reliability_sizing: bool = True
    # Trade-outcome size overlay (generic context buckets). Soft clip only —
    # never rewrites WR membership filters. See momentum_trade_outcomes.py.
    outcome_size_enabled: bool = True
    max_positions: int = 3
    top_n: int = 2
    top_n_broad: int = 3
    broad_breadth: float = 0.7
    # Fee-aware floor. WR winner (135d live-scale) and prior €40k calmar search
    # both kept 2.5%; thinner excess names are the main Jun/Jul bleed source.
    min_excess: float = 0.025
    # Always-on excess floor = fee_rt × this (binds even if min_excess is lowered).
    entry_fee_buffer_mult: float = 6.0
    # Chase reject (0 disables). Same search: the 9% glue-to-high gate did not
    # improve calmar vs off; keep disabled so extended leaders can still enter.
    max_chase_ret_24h: float = 0.0
    chase_near_high: float = 0.008  # must be at least this far under the high
    max_from_high: float = 0.02
    min_volume_eur: float = 1_000_000.0
    btc_min_ret: float = -0.01
    min_breadth: float = 0.5
    trail_pct: float = 0.03
    # Ratchet: once the peak gain reaches ``trail_tight_after`` the trail
    # narrows to ``trail_tight_pct`` (0 disables). WR winner (135d live-scale)
    # used 4%→2% with a 3% base trail — locks gains earlier than the prior
    # 4% / 5%→2.5% pack and lifted WR 40%→54% on the same window.
    trail_tight_after: float = 0.04
    trail_tight_pct: float = 0.02
    hard_stop_pct: float = 0.03
    # Staged early stop (0 disables): until peak gain reaches
    # ``early_stop_until_peak``, use the tighter ``early_stop_pct`` instead of
    # ``hard_stop_pct``. Cuts losers that never print a meaningful green print
    # without clipping trails once the trade has confirmed.
    early_stop_pct: float = 0.0
    early_stop_until_peak: float = 0.0
    # WR winner: 36h time exit (vs prior 24h) with the tighter trail pack.
    time_exit_hours: float = 36.0
    # Time-to-green (0 disables): if age ≥ ``green_deadline_hours`` and peak
    # gain is still below ``green_min_peak``, exit as ``no_green``. Off on the
    # WR winner — the 4h gate cut WR ~54%→45% in the same live-scale search.
    green_deadline_hours: float = 0.0
    green_min_peak: float = 0.01
    # Midflat (0 disables). Keep off; WR winner relies on trail + 36h time exit.
    midflat_hours: float = 0.0
    # Fade-velocity / ETA-to-zero (0 ``fade_eta_sec`` disables). Live path with
    # dense venue marks: once peak unrealized net is meaningful, if smoothed
    # net is falling toward zero fast enough that ETA < fade_eta_sec for
    # ``fade_confirm_sec``, exit as ``fade_fast``. Preserves slow runners
    # (normal trail) while cutting cascades from green toward red.
    fade_eta_sec: float = 180.0
    fade_confirm_sec: float = 20.0
    fade_smooth_sec: float = 40.0
    fade_min_peak_eur: float = 15.0
    fade_min_peak_pct: float = 0.012
    fade_min_giveback_eur: float = 5.0
    fee_rt: float = 0.003
    day_loss_limit_eur: float = 40.0
    week_loss_limit_eur: float = 100.0
    pause_hours_after_week_limit: float = 48.0
    max_entries_per_base_per_day: int = 1
    # "reduce" scales the clip on AlphaI macro caution, "block" stops new
    # entries, "ignore" disregards it. Default reduce: soft+macro idle +
    # clip×0.7; do not use block (too blunt) or permanent ignore.
    macro_caution_mode: str = "reduce"
    macro_caution_clip_mult: float = 0.7
    # Under macro caution + reduce: optional AlphaI-only gate. Default off —
    # empty picks + strong tape caused live deadlocks (e.g. NEAR 07:00 miss).
    macro_caution_requires_alphai_pick: bool = False
    # Soft regime: weak BTC/breadth no longer hard-blocks. Instead the
    # desk stays open for AlphaI picks at a reduced clip so early legs
    # of a bounce are not missed while tape is still thin.
    soft_regime_on_weak_tape: bool = True
    soft_regime_clip_mult: float = 0.5
    # Survival: when BTC and breadth are both soft-fail, idle (preserve
    # capital) instead of force-longing AlphaI picks into a dead tape.
    weak_tape_idle_on_double: bool = True
    # Survival: soft tape + AlphaI macro caution → idle. Soft+reduce was
    # the path into early hard-stops on thin bounce attempts.
    soft_regime_idle_on_macro_caution: bool = True
    # Under macro caution, demand excess that clears fee_rt × buffer
    # before a weak/tape-only name can enter (coin-agnostic fee guard).
    # Must be ≥ entry_fee_buffer_mult so caution is never looser than base.
    macro_caution_fee_buffer_mult: float = 7.0
    # Soft regime (weak BTC/breadth): AlphaI-only AND fee×this excess floor.
    soft_regime_fee_buffer_mult: float = 6.0
    # Ranking: AlphaI pick boost + mild absolute-momentum complement
    # (volume already gated). Keeps RS primary, rewards confirmed names.
    alphai_rank_boost: float = 0.01
    momentum_rank_weight: float = 0.05
    # Tape-strength sizing. 12-week attribution at 7/13 UTC: entries taken
    # with >= 85% of the universe up on the day averaged +13.9 EUR per 1000
    # EUR clip (n=31) against +3.1 EUR for the rest (n=28); broad rallies
    # persist, narrow ones fade. Strong tape sizes up, thin tape sizes down.
    strong_breadth: float = 0.85
    strong_clip_mult: float = 1.3
    # Only apply strong_clip_mult when the name clears quality (AlphaI pick or
    # excess ≥ strong_clip_min_excess). Broad-but-fake tape sized up into jun/jul losses.
    strong_clip_requires_quality: bool = True
    strong_clip_min_excess: float = 0.04
    weak_clip_mult: float = 0.7
    # Weekend decisions (Sat/Sun UTC) read 24h returns printed on thin
    # liquidity; they were 18 of 59 trades and netted about zero while
    # adding a third of the drawdown. Exits keep running on weekends.
    skip_weekend_entries: bool = True
    # Exit side of AlphaI: a bearish headline on a held base does not dump the
    # position (that was fee churn in the old desk) but narrows the trail to
    # ``trail_tight_pct`` so the winner is protected while the news is fresh.
    alphai_avoid_tightens_trail: bool = True
    # Research knobs (backtest only): decide on every 15m bar instead of the
    # decision hours, and trigger stops on the bar's low (proxy for minute-level
    # monitoring) instead of on the close.
    decision_every_bar: bool = False
    # Live cadence (0 = use ``decision_hours_utc``). Continuous minute/every-bar
    # scanning destroys WR on the 135d live-scale window (~54%→~36%); keep 0
    # and rely on quality hours + ``refill_on_exit`` for freed slots.
    decision_interval_sec: float = 0.0
    # After a successful exit frees a slot, run one immediate entry decision
    # (same filters) so a weekday runner is not waited on until the next hour.
    refill_on_exit: bool = True
    exit_on_touch: bool = False
    # Dynamic universe: at each decision keep only the K bases with the highest
    # trailing 24h EUR volume (0 = use the whole universe). Lets a wide pool
    # follow where the money is without hindsight-picking today's hot names.
    universe_top_by_volume: int = 0
    # Backtest only: total EUR the desk may have deployed at once (0 = no
    # cap). Mirrors the live router, which shrinks a clip to the cash left
    # (down to ``min_clip_fraction``) and otherwise skips the entry.
    book_eur: float = 0.0
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    clusters: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_CLUSTERS))

    def with_overrides(self, **kwargs: Any) -> DeskConfig:
        return replace(self, **kwargs)


@dataclass(frozen=True)
class BaseStats:
    base: str
    price: float
    ret_24h: float
    from_high: float
    volume_eur: float


@dataclass(frozen=True)
class RegimeDecision:
    ok: bool
    btc_ret: float | None
    breadth: float
    reasons: tuple[str, ...]
    soft: bool = False


@dataclass(frozen=True)
class Candidate:
    base: str
    excess: float
    ret_24h: float
    from_high: float
    volume_eur: float
    score: float
    alphai_pick: bool


@dataclass(frozen=True)
class AlphaIView:
    """AlphaI overlay: membership sets plus optional size-overlay features."""

    avoid: frozenset[str] = frozenset()
    picks: frozenset[str] = frozenset()
    macro_caution: bool = False
    pick_scores: Mapping[str, float] = field(default_factory=dict)
    pick_ranks: Mapping[str, int] = field(default_factory=dict)
    bullish_headline_counts: Mapping[str, int] = field(default_factory=dict)
    bearish_headline_counts: Mapping[str, int] = field(default_factory=dict)
    price_confirm_scales: Mapping[str, float] = field(default_factory=dict)
    base_reliability: Mapping[str, float] = field(default_factory=dict)
    generated_at_ms: int | None = None
    watch: frozenset[str] = frozenset()

    @classmethod
    def from_recommendations(cls, payload: Mapping[str, Any] | None) -> AlphaIView:
        if not payload:
            return cls()

        def _bases(rows: Any) -> frozenset[str]:
            out: set[str] = set()
            for row in rows or []:
                base = row.get("base") if isinstance(row, Mapping) else row
                if base:
                    out.add(str(base).upper())
            return frozenset(out)

        scores: dict[str, float] = {}
        ranks: dict[str, int] = {}
        bull_n: dict[str, int] = {}
        bear_n: dict[str, int] = {}
        for row in payload.get("picks") or []:
            if not isinstance(row, Mapping):
                continue
            base = str(row.get("base") or "").upper()
            if not base:
                continue
            if row.get("score") is not None:
                try:
                    scores[base] = float(row["score"])
                except (TypeError, ValueError):
                    pass
            if row.get("rank") is not None:
                try:
                    ranks[base] = int(row["rank"])
                except (TypeError, ValueError):
                    pass
            bull = row.get("bullish_headlines") or row.get("bullish") or []
            bear = row.get("bearish_headlines") or row.get("bearish") or []
            if isinstance(bull, list):
                bull_n[base] = len(bull)
            if isinstance(bear, list):
                bear_n[base] = len(bear)

        confirm: dict[str, float] = {}
        raw_scales = payload.get("price_confirm_scales") or {}
        if isinstance(raw_scales, Mapping):
            for k, v in raw_scales.items():
                try:
                    confirm[str(k).upper()] = max(0.0, min(1.0, float(v)))
                except (TypeError, ValueError):
                    continue
        # Older payloads only list lagging bases.
        for base in payload.get("price_lag_bases") or []:
            b = str(base).upper()
            if b and b not in confirm:
                confirm[b] = 0.35

        reliability: dict[str, float] = {}
        raw_rel = payload.get("base_reliability") or {}
        if isinstance(raw_rel, Mapping):
            for k, v in raw_rel.items():
                try:
                    reliability[str(k).upper()] = max(0.40, min(1.10, float(v)))
                except (TypeError, ValueError):
                    continue

        generated_at_ms: int | None = None
        for key in ("generated_at", "updated_at", "asof"):
            raw = payload.get(key)
            if not raw:
                continue
            try:
                from datetime import datetime

                ts = str(raw).replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts)
                generated_at_ms = int(dt.timestamp() * 1000)
                break
            except Exception:  # noqa: BLE001
                continue

        return cls(
            avoid=_bases(payload.get("avoid")),
            picks=_bases(payload.get("picks")),
            macro_caution=bool(payload.get("macro_caution")),
            pick_scores=scores,
            pick_ranks=ranks,
            bullish_headline_counts=bull_n,
            bearish_headline_counts=bear_n,
            price_confirm_scales=confirm,
            base_reliability=reliability,
            generated_at_ms=generated_at_ms,
            watch=_bases(payload.get("watch")),
        )

    def headline_conflict_ratio(self, base: str) -> float:
        b = str(base or "").upper()
        bull = int(self.bullish_headline_counts.get(b, 0))
        bear = int(self.bearish_headline_counts.get(b, 0))
        total = bull + bear
        if total <= 0:
            return 0.0
        return bear / float(total)

    def price_confirm_scale(self, base: str) -> float:
        b = str(base or "").upper()
        if b in self.price_confirm_scales:
            try:
                return max(0.0, min(1.0, float(self.price_confirm_scales[b])))
            except (TypeError, ValueError):
                return 1.0
        return 1.0

    def pick_conviction(self, base: str, *, cfg: DeskConfig | None = None) -> float:
        """Relative 0..1 conviction for an AlphaI pick (score/rank + damps)."""
        b = str(base or "").upper()
        if b not in self.picks:
            return 0.0
        positive = {
            k: float(v)
            for k, v in self.pick_scores.items()
            if k in self.picks and float(v) > 0 and k not in self.avoid
        }
        if not positive:
            # No score payload → treat like legacy full pick boost.
            conv = 1.0
        elif b not in positive:
            conv = 0.75
        else:
            ranked = sorted(positive.items(), key=lambda kv: kv[1], reverse=True)
            n = len(ranked)
            rank_idx = next(i for i, (k, _) in enumerate(ranked) if k == b)
            rank_conv = 1.0 - (rank_idx / max(n, 1))
            vals = [s for _, s in ranked]
            mx, mn = max(vals), min(vals)
            score_conv = (positive[b] - mn) / (mx - mn) if mx > mn else 1.0
            conv = 0.55 * rank_conv + 0.45 * score_conv

        damp = 0.55 if cfg is None else float(cfg.alphai_conflict_damp)
        conflict = self.headline_conflict_ratio(b)
        if conflict > 0.0:
            conv *= max(0.55, 1.0 - damp * conflict)

        use_px = True if cfg is None else bool(cfg.alphai_price_confirm_sizing)
        if use_px:
            scale = self.price_confirm_scale(b)
            if scale < 1.0:
                conv *= max(0.35, scale)

        use_rel = True if cfg is None else bool(cfg.alphai_reliability_sizing)
        if use_rel and b in self.base_reliability:
            try:
                rel = float(self.base_reliability[b])
                conv *= max(0.45, min(1.10, rel))
            except (TypeError, ValueError):
                pass
        return max(0.0, min(1.0, conv))

    def with_overlays(
        self,
        *,
        price_confirm_scales: Mapping[str, float] | None = None,
        base_reliability: Mapping[str, float] | None = None,
    ) -> AlphaIView:
        """Return a copy with live tape/reliability overlays merged in."""
        confirm = dict(self.price_confirm_scales)
        if price_confirm_scales:
            for k, v in price_confirm_scales.items():
                try:
                    confirm[str(k).upper()] = max(0.0, min(1.0, float(v)))
                except (TypeError, ValueError):
                    continue
        reliability = dict(self.base_reliability)
        if base_reliability:
            for k, v in base_reliability.items():
                try:
                    reliability[str(k).upper()] = max(0.40, min(1.10, float(v)))
                except (TypeError, ValueError):
                    continue
        return replace(
            self,
            price_confirm_scales=confirm,
            base_reliability=reliability,
        )


def alphai_entry_clip_mult(
    base: str,
    view: AlphaIView,
    cfg: DeskConfig,
    *,
    now_ms: int | None = None,
) -> tuple[float, tuple[str, ...]]:
    """Clip multiplier for an AlphaI pick; ``1.0`` and no tags for non-picks."""
    b = str(base or "").upper()
    if b not in view.picks:
        return 1.0, ()

    mode = str(cfg.alphai_size_mode or "binary").strip().lower()
    if mode != "conviction":
        # Membership tag ``alphai_pick`` is added by select_entries; keep tags size-only.
        return float(cfg.alphai_clip_mult), ()

    stale_min = float(cfg.alphai_stale_minutes or 0.0)
    if (
        stale_min > 0.0
        and now_ms is not None
        and view.generated_at_ms is not None
        and (now_ms - int(view.generated_at_ms)) > stale_min * 60_000
    ):
        return 1.0, ("alphai_stale",)

    conv = view.pick_conviction(b, cfg=cfg)
    lo = float(cfg.alphai_clip_mult_min)
    hi = float(cfg.alphai_clip_mult)
    if hi < lo:
        lo, hi = hi, lo
    mult = lo + (hi - lo) * conv
    return mult, (f"alphai_conv={conv:.2f}", f"alphai_clip_x{mult:.2f}")


@dataclass(frozen=True)
class Entry:
    base: str
    clip_eur: float
    score: float
    reasons: tuple[str, ...]
    # Generic entry-context snapshot for outcome learning (no coin identity).
    entry_ctx: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    base: str
    entry_price: float
    quantity: float
    notional_eur: float
    opened_ms: int
    peak: float
    entry_fee_eur: float = 0.0
    entry_reason: str = ""
    venue: str = "bitvavo"
    # Generic attributes at entry (persisted for outcome learning on close).
    entry_ctx: dict[str, Any] = field(default_factory=dict)

    def gross_return(self, price: float) -> float:
        return price / self.entry_price - 1.0 if self.entry_price > 0 else 0.0


@dataclass(frozen=True)
class ExitDecision:
    reason: str
    gross_return: float
    urgent: bool
    # Fill assumption for the backtest when the exit triggered intrabar.
    price: float | None = None


@dataclass
class FadeState:
    """Live fade-velocity tracker (not persisted; re-arms after restart)."""

    peak_net_eur: float = 0.0
    peak_gross_return: float = 0.0
    net_ema: float | None = None
    last_ts: float = 0.0
    breach_since: float | None = None


def unrealized_net_eur(pos: Position, mark: float, cfg: DeskConfig) -> float:
    """Mark-to-market net EUR incl. entry fee + expected exit half-fee."""
    return (
        pos.quantity * (mark - pos.entry_price)
        - float(pos.entry_fee_eur)
        - (pos.quantity * mark * cfg.fee_rt / 2.0)
    )


def update_fade_state(
    state: FadeState,
    *,
    net_eur: float,
    gross_return: float,
    now: float,
    cfg: DeskConfig,
) -> tuple[FadeState, ExitDecision | None]:
    """Update EMA/peak and optionally fire ``fade_fast``.

    Coin-agnostic: arm after peak unrealized net clears ``fade_min_peak_eur``
    **or** peak return clears ``fade_min_peak_pct``. Exit when smoothed net is
    still green, has given back enough from peak, and ETA to zero
    (net / -velocity) stays below ``fade_eta_sec`` for ``fade_confirm_sec``.
    """
    eta_lim = float(cfg.fade_eta_sec)
    if eta_lim <= 0.0:
        return state, None

    peak_net = max(float(state.peak_net_eur), float(net_eur))
    peak_gross = max(float(state.peak_gross_return), float(gross_return))
    smooth = max(1.0, float(cfg.fade_smooth_sec))
    prev_ema = state.net_ema
    prev_ts = float(state.last_ts or 0.0)
    if prev_ema is None or prev_ts <= 0.0 or now <= prev_ts:
        ema = float(net_eur)
        vel = 0.0
    else:
        dt = max(1e-3, now - prev_ts)
        alpha = 1.0 - pow(0.5, dt / smooth)  # half-life ≈ smooth_sec
        ema = float(prev_ema) + alpha * (float(net_eur) - float(prev_ema))
        vel = (ema - float(prev_ema)) / dt  # EUR / sec

    armed = peak_net >= float(cfg.fade_min_peak_eur) or peak_gross >= float(
        cfg.fade_min_peak_pct
    )
    giveback = peak_net - float(net_eur)
    eta: float | None = None
    if armed and float(net_eur) > 0.0 and vel < 0.0:
        eta = float(net_eur) / (-vel)

    breach = (
        armed
        and float(net_eur) > 0.0
        and giveback >= float(cfg.fade_min_giveback_eur)
        and eta is not None
        and eta <= eta_lim
    )
    breach_since = state.breach_since
    decision: ExitDecision | None = None
    if breach:
        if breach_since is None:
            breach_since = now
        elif now - breach_since >= float(cfg.fade_confirm_sec):
            decision = ExitDecision("fade_fast", float(gross_return), urgent=True)
    else:
        breach_since = None

    new_state = FadeState(
        peak_net_eur=peak_net,
        peak_gross_return=peak_gross,
        net_ema=ema,
        last_ts=now,
        breach_since=breach_since,
    )
    return new_state, decision


def _closed_bars(candles: Sequence[Candle], t_ms: int) -> list[Candle]:
    """Bars fully closed at ``t_ms`` (bar ts + BAR_MS <= t)."""
    return [c for c in candles if int(c[0]) + BAR_MS <= t_ms]


def bar_stats(base: str, candles: Sequence[Candle], t_ms: int) -> BaseStats | None:
    """24h statistics from closed 15m bars, aligned so backtest == live."""
    closed = _closed_bars(candles, t_ms)
    if not closed:
        return None
    day_start = t_ms - BARS_PER_DAY * BAR_MS
    # Time-based window: Bitvavo omits 15m bars without trades, so thin pairs
    # have gaps. Count-based windows would silently stretch beyond 24h.
    window = [c for c in closed if int(c[0]) >= day_start]
    before = [c for c in closed if int(c[0]) < day_start]
    if not window or not before:
        return None
    last = window[-1]
    ref = before[-1]
    # The last bar must be recent and the reference bar must sit near 24h ago.
    if t_ms - int(last[0]) > 4 * BAR_MS or day_start - int(ref[0]) > 8 * BAR_MS:
        return None
    if len(window) < BARS_PER_DAY // 2:
        return None
    price = float(last[4])
    ref_price = float(ref[4])
    if price <= 0 or ref_price <= 0:
        return None
    high = max(float(c[2]) for c in window)
    volume_eur = sum(float(c[5]) * float(c[4]) for c in window)
    return BaseStats(
        base=base,
        price=price,
        ret_24h=price / ref_price - 1.0,
        from_high=price / high - 1.0 if high > 0 else 0.0,
        volume_eur=volume_eur,
    )


def universe_stats(
    candles_by_base: Mapping[str, Sequence[Candle]], t_ms: int, cfg: DeskConfig
) -> dict[str, BaseStats]:
    out: dict[str, BaseStats] = {}
    for base in cfg.universe:
        rows = candles_by_base.get(base)
        if not rows:
            continue
        stats = bar_stats(base, rows, t_ms)
        if stats is not None:
            out[base] = stats
    return out


def restrict_by_volume(stats: Mapping[str, BaseStats], cfg: DeskConfig) -> dict[str, BaseStats]:
    """Apply ``universe_top_by_volume``; identity when it is 0."""
    k = int(cfg.universe_top_by_volume or 0)
    if k <= 0 or len(stats) <= k:
        return dict(stats)
    ranked = sorted(stats.values(), key=lambda s: s.volume_eur, reverse=True)[:k]
    return {s.base: s for s in ranked}


def classify_regime(
    btc: BaseStats | None,
    alts: Mapping[str, BaseStats],
    cfg: DeskConfig,
    *,
    alphai: AlphaIView | None = None,
) -> RegimeDecision:
    reasons: list[str] = []
    btc_ret = btc.ret_24h if btc is not None else None
    breadth = sum(1 for s in alts.values() if s.ret_24h > 0) / len(alts) if alts else 0.0
    if btc_ret is None:
        reasons.append("btc_stats_missing")
    elif btc_ret <= cfg.btc_min_ret:
        reasons.append("btc_weak")
    if breadth < cfg.min_breadth:
        reasons.append("breadth_weak")
    if alphai is not None and alphai.macro_caution and cfg.macro_caution_mode == "block":
        reasons.append("alphai_macro_block")
    soft_reasons = {"btc_weak", "breadth_weak"}
    hard = [r for r in reasons if r not in soft_reasons]
    if (
        cfg.soft_regime_on_weak_tape
        and reasons
        and not hard
        and all(r in soft_reasons for r in reasons)
    ):
        double_weak = "btc_weak" in reasons and "breadth_weak" in reasons
        if bool(cfg.weak_tape_idle_on_double) and double_weak:
            # Both soft factors failed: idle — do not AlphaI-force into dead tape.
            idle_reasons = tuple([*reasons, "weak_tape_idle"])
            return RegimeDecision(
                ok=False,
                btc_ret=btc_ret,
                breadth=breadth,
                reasons=idle_reasons,
                soft=False,
            )
        if (
            bool(cfg.soft_regime_idle_on_macro_caution)
            and alphai is not None
            and alphai.macro_caution
            and cfg.macro_caution_mode == "reduce"
        ):
            idle_reasons = tuple([*reasons, "soft_macro_idle"])
            return RegimeDecision(
                ok=False,
                btc_ret=btc_ret,
                breadth=breadth,
                reasons=idle_reasons,
                soft=False,
            )
        # Single soft factor only: stay open, AlphaI-size down in select_entries.
        return RegimeDecision(
            ok=True, btc_ret=btc_ret, breadth=breadth, reasons=tuple(reasons), soft=True
        )
    return RegimeDecision(
        ok=not reasons, btc_ret=btc_ret, breadth=breadth, reasons=tuple(reasons), soft=False
    )


def regime_label(regime: RegimeDecision, cfg: DeskConfig) -> str:
    """Coin-agnostic tape bucket for PnL attribution: strong|firm|soft|weak."""
    if not regime.ok:
        return "weak"
    if bool(getattr(regime, "soft", False)):
        return "soft"
    if float(regime.breadth) >= float(cfg.strong_breadth):
        return "strong"
    return "firm"


def summarize_regime_pnl(exits: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate closed-trade net PnL by entry regime_label (strong/firm/soft/weak)."""
    buckets: dict[str, dict[str, float | int]] = {}
    for row in exits:
        ctx = row.get("entry_ctx") if isinstance(row.get("entry_ctx"), Mapping) else {}
        label = str((ctx or {}).get("regime_label") or "").strip().lower()
        if label not in {"strong", "firm", "soft", "weak"}:
            reason = str(row.get("entry_reason") or row.get("reason") or "")
            if "soft_regime" in reason:
                label = "soft"
            elif "breadth_strong" in reason:
                label = "strong"
            else:
                label = "firm"
        bucket = buckets.setdefault(
            label, {"n": 0, "wins": 0, "net_eur": 0.0, "sum_hold_h": 0.0}
        )
        net = float(row.get("net_eur") or 0.0)
        bucket["n"] = int(bucket["n"]) + 1
        bucket["net_eur"] = float(bucket["net_eur"]) + net
        if net > 0:
            bucket["wins"] = int(bucket["wins"]) + 1
        hold = row.get("hold_h")
        if hold is not None:
            bucket["sum_hold_h"] = float(bucket["sum_hold_h"]) + float(hold)
    out: dict[str, Any] = {}
    for label, b in sorted(buckets.items()):
        n = int(b["n"])
        net = float(b["net_eur"])
        wins = int(b["wins"])
        hold_sum = float(b["sum_hold_h"])
        out[label] = {
            "n": n,
            "wins": wins,
            "win_rate": round(wins / n, 4) if n else None,
            "net_eur": round(net, 2),
            "avg_net_eur": round(net / n, 2) if n else None,
            "avg_hold_h": round(hold_sum / n, 2) if n and hold_sum else None,
        }
    return out


def rank_candidates(
    alts: Mapping[str, BaseStats],
    btc_ret: float,
    cfg: DeskConfig,
    *,
    alphai: AlphaIView | None = None,
) -> list[Candidate]:
    view = alphai or AlphaIView()
    out: list[Candidate] = []
    fee_floor = cfg.fee_rt * max(0.0, float(cfg.entry_fee_buffer_mult))
    base_need = max(float(cfg.min_excess), fee_floor)
    for base, s in alts.items():
        if base in view.avoid:
            continue
        excess = s.ret_24h - btc_ret
        if s.from_high < -cfg.max_from_high:
            continue
        if s.volume_eur < cfg.min_volume_eur:
            continue
        # Reject late chase: already up hard and still glued near the 24h high.
        max_chase = float(cfg.max_chase_ret_24h)
        if max_chase > 0.0 and s.ret_24h >= max_chase:
            if s.from_high > -max(0.0, float(cfg.chase_near_high)):
                continue
        pick = base in view.picks
        need = base_need
        if view.macro_caution and cfg.macro_caution_mode == "reduce":
            need = max(need, cfg.fee_rt * float(cfg.macro_caution_fee_buffer_mult))
        if excess < need:
            continue
        # RS primary; AlphaI pick gets a real boost; mild abs-momentum complement
        # (volume already passed). Coin-agnostic — no per-base special cases.
        score = excess + (cfg.alphai_rank_boost if pick else 0.0)
        score += cfg.momentum_rank_weight * max(0.0, s.ret_24h)
        out.append(
            Candidate(
                base=base,
                excess=excess,
                ret_24h=s.ret_24h,
                from_high=s.from_high,
                volume_eur=s.volume_eur,
                score=score,
                alphai_pick=pick,
            )
        )
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def select_entries(
    cands: Sequence[Candidate],
    regime: RegimeDecision,
    cfg: DeskConfig,
    *,
    held_bases: Iterable[str],
    blocked_bases: Iterable[str] = (),
    alphai: AlphaIView | None = None,
    now_ms: int | None = None,
    outcome_store: Any | None = None,
) -> list[Entry]:
    if not regime.ok:
        return []
    held = {b.upper() for b in held_bases}
    blocked = {b.upper() for b in blocked_bases}
    clusters_held = {cfg.clusters.get(b) for b in held}
    slots = max(0, cfg.max_positions - len(held))
    top_n = cfg.top_n_broad if regime.breadth >= cfg.broad_breadth else cfg.top_n
    view = alphai or AlphaIView()
    macro_mult = (
        cfg.macro_caution_clip_mult
        if (view.macro_caution and cfg.macro_caution_mode == "reduce")
        else 1.0
    )
    soft_need = cfg.fee_rt * max(0.0, float(cfg.soft_regime_fee_buffer_mult))
    n_cands = len(cands)
    soft = bool(getattr(regime, "soft", False))
    label = regime_label(regime, cfg)
    out: list[Entry] = []
    for c in cands:
        if len(out) >= min(slots, top_n):
            break
        if c.base in held or c.base in blocked:
            continue
        if (
            view.macro_caution
            and cfg.macro_caution_mode == "reduce"
            and cfg.macro_caution_requires_alphai_pick
            and not c.alphai_pick
        ):
            continue
        cluster = cfg.clusters.get(c.base)
        if cluster is not None and cluster in clusters_held:
            continue
        if soft and not c.alphai_pick:
            continue
        if soft and c.excess < soft_need:
            continue
        breadth_mult, breadth_tag = breadth_clip_mult(regime.breadth, cfg)
        # Gate oversized strong-tape clips behind quality so broad-but-fake
        # rallies do not auto-size up mediocre RS names.
        if (
            breadth_tag == "breadth_strong"
            and bool(cfg.strong_clip_requires_quality)
            and not c.alphai_pick
            and float(c.excess) < float(cfg.strong_clip_min_excess)
        ):
            breadth_mult, breadth_tag = 1.0, "breadth_strong_gated"
        alphai_mult, alphai_tags = alphai_entry_clip_mult(
            c.base, view, cfg, now_ms=now_ms
        )
        entry_ctx = build_entry_ctx(
            excess=float(c.excess),
            ret_24h=float(c.ret_24h),
            from_high=float(c.from_high),
            n_cands=n_cands,
            breadth=float(regime.breadth),
            btc_ret=regime.btc_ret,
            soft=soft,
            alphai_pick=bool(c.alphai_pick),
        )
        entry_ctx["regime_label"] = label
        outcome_mult, outcome_tags = 1.0, ()
        if (
            bool(getattr(cfg, "outcome_size_enabled", True))
            and outcome_store is not None
            and hasattr(outcome_store, "size_mult")
        ):
            outcome_mult, outcome_tags = outcome_store.size_mult(entry_ctx)
        clip = cfg.clip_eur * alphai_mult * macro_mult * breadth_mult * float(outcome_mult)
        if soft:
            clip *= cfg.soft_regime_clip_mult
        reasons = [f"excess={c.excess:+.4f}", f"from_high={c.from_high:+.4f}"]
        if c.alphai_pick:
            reasons.append("alphai_pick")
        reasons.extend(alphai_tags)
        reasons.extend(outcome_tags)
        if soft:
            reasons.append("soft_regime")
        reasons.append(f"regime={label}")
        if macro_mult != 1.0:
            reasons.append("macro_reduce")
        if breadth_tag:
            reasons.append(breadth_tag)
        out.append(
            Entry(
                base=c.base,
                clip_eur=round(clip, 2),
                score=c.score,
                reasons=tuple(reasons),
                entry_ctx=dict(entry_ctx),
            )
        )
        if cluster is not None:
            clusters_held.add(cluster)
    return out


def breadth_clip_mult(breadth: float, cfg: DeskConfig) -> tuple[float, str]:
    """Clip multiplier and reason tag for the tape strength at decision time."""
    if breadth >= cfg.strong_breadth and cfg.strong_clip_mult != 1.0:
        return cfg.strong_clip_mult, "breadth_strong"
    if breadth < cfg.broad_breadth and cfg.weak_clip_mult != 1.0:
        return cfg.weak_clip_mult, "breadth_weak"
    return 1.0, ""


def max_clip_mult(cfg: DeskConfig) -> float:
    """Largest multiplier ``select_entries`` can apply to ``clip_eur``."""
    outcome_hi = 1.15 if bool(getattr(cfg, "outcome_size_enabled", True)) else 1.0
    return max(1.0, cfg.alphai_clip_mult) * max(1.0, cfg.strong_clip_mult) * outcome_hi


def is_entry_weekday(t_ms: int, cfg: DeskConfig) -> bool:
    """False on Saturday/Sunday UTC when weekend entries are disabled."""
    if not cfg.skip_weekend_entries:
        return True
    # Unix epoch (day 0) was a Thursday, so weekday = (day + 3) % 7 with Mon=0.
    return ((t_ms // 86_400_000 + 3) % 7) < 5


def is_scheduled_hour(hour_start_ms: int, cfg: DeskConfig) -> bool:
    """True when the hour starting at ``hour_start_ms`` is a decision slot."""
    hour = (hour_start_ms // 3_600_000) % 24
    return hour in cfg.decision_hours_utc and is_entry_weekday(hour_start_ms, cfg)


def is_decision_time(t_ms: int, cfg: DeskConfig) -> bool:
    if cfg.decision_every_bar:
        return t_ms % BAR_MS == 0
    interval = float(cfg.decision_interval_sec or 0.0)
    if interval > 0.0:
        slot_ms = max(1, int(interval * 1000))
        return t_ms % slot_ms == 0 and is_entry_weekday(t_ms, cfg)
    return t_ms % 3_600_000 == 0 and is_scheduled_hour(t_ms, cfg)


def evaluate_exit(
    pos: Position,
    bar: Candle,
    cfg: DeskConfig,
    *,
    alphai: AlphaIView | None = None,
) -> ExitDecision | None:
    """Evaluate a closed 15m bar. Mutates ``pos.peak``; returns an exit or None.

    Order matters: the hard stop is checked on the close before the trail so a
    gap through both levels is booked as the stop (urgent taker exit).
    """
    high = float(bar[2])
    low = float(bar[3])
    close = float(bar[4])
    open_ = float(bar[1])
    bar_end = int(bar[0]) + BAR_MS
    tightened = cfg.alphai_avoid_tightens_trail and alphai is not None and pos.base in alphai.avoid

    def _trail_for(peak: float) -> tuple[float, float]:
        trail = cfg.trail_pct
        peak_gain = peak / pos.entry_price - 1.0 if pos.entry_price > 0 else 0.0
        if cfg.trail_tight_after > 0 and peak_gain >= cfg.trail_tight_after:
            trail = min(trail, cfg.trail_tight_pct)
        base_trail = trail
        if tightened:
            trail = min(trail, cfg.trail_tight_pct)
        return base_trail, trail

    def _stop_pct_for(peak: float) -> tuple[float, str]:
        """Effective stop distance and reason tag (hard_stop vs early_stop)."""
        stop = cfg.hard_stop_pct
        reason = "hard_stop"
        if cfg.early_stop_pct > 0.0 and cfg.early_stop_until_peak > 0.0 and pos.entry_price > 0:
            peak_gain = peak / pos.entry_price - 1.0
            if peak_gain < cfg.early_stop_until_peak:
                stop = cfg.early_stop_pct
                reason = "early_stop"
        return stop, reason

    if cfg.exit_on_touch:
        # Intrabar semantics: stops are tested against the low with the peak
        # known *before* this bar (the order of high and low inside a bar is
        # unknown). A gap through the level fills at the open.
        stop_pct, stop_reason = _stop_pct_for(pos.peak)
        stop_px = pos.entry_price * (1.0 - stop_pct)
        if low <= stop_px:
            px = min(stop_px, open_)
            return ExitDecision(stop_reason, pos.gross_return(px), urgent=True, price=px)
        if pos.peak > 0:
            base_trail, trail = _trail_for(pos.peak)
            trail_px = pos.peak * (1.0 - trail)
            if low <= trail_px:
                px = min(trail_px, open_)
                reason = "trail" if low <= pos.peak * (1.0 - base_trail) else "trail_alphai"
                return ExitDecision(reason, pos.gross_return(px), urgent=False, price=px)
        if pos.peak < high:
            pos.peak = high
        gross = pos.gross_return(close)
    else:
        # Peak for stop staging uses the pre-bar peak (same as exit_on_touch).
        stop_pct, stop_reason = _stop_pct_for(pos.peak)
        if pos.peak < high:
            pos.peak = high
        gross = pos.gross_return(close)
        if close <= pos.entry_price * (1.0 - stop_pct):
            return ExitDecision(stop_reason, gross, urgent=True)
        base_trail, trail = _trail_for(pos.peak)
        if pos.peak > 0 and close <= pos.peak * (1.0 - trail):
            reason = "trail" if close <= pos.peak * (1.0 - base_trail) else "trail_alphai"
            return ExitDecision(reason, gross, urgent=False)
    age_ms = bar_end - pos.opened_ms
    green_h = float(cfg.green_deadline_hours)
    if green_h > 0.0 and age_ms >= green_h * 3600_000:
        peak_gain = pos.peak / pos.entry_price - 1.0 if pos.entry_price > 0 else 0.0
        if peak_gain < float(cfg.green_min_peak):
            return ExitDecision("no_green", gross, urgent=False)
    midflat_h = float(cfg.midflat_hours)
    if midflat_h > 0.0 and age_ms >= midflat_h * 3600_000 and gross <= cfg.fee_rt:
        return ExitDecision("midflat", gross, urgent=False)
    if age_ms >= cfg.time_exit_hours * 3600_000 and gross <= cfg.fee_rt:
        return ExitDecision("time_exit", gross, urgent=False)
    return None


def net_pnl_eur(
    pos: Position, exit_price: float, exit_fee_eur: float | None, cfg: DeskConfig
) -> float:
    gross_eur = pos.quantity * (exit_price - pos.entry_price)
    if exit_fee_eur is None:
        # Backtest convention: split the round-trip fee evenly.
        exit_fee_eur = pos.notional_eur * cfg.fee_rt / 2.0
        entry_fee = pos.notional_eur * cfg.fee_rt / 2.0
    else:
        entry_fee = pos.entry_fee_eur
    return gross_eur - entry_fee - exit_fee_eur


@dataclass
class RiskLedger:
    """Realized-loss limits. Pure bookkeeping; callers feed it closed trades."""

    day_loss_limit_eur: float
    week_loss_limit_eur: float
    pause_hours: float
    day_key: str = ""
    day_realized_eur: float = 0.0
    week_key: str = ""
    week_realized_eur: float = 0.0
    paused_until_ms: int = 0
    entries_today: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def _day_key(t_ms: int) -> str:
        return str(t_ms // 86_400_000)

    @staticmethod
    def _week_key(t_ms: int) -> str:
        # Unix epoch was a Thursday; shift so weeks start on Monday 00:00 UTC.
        return str((t_ms // 86_400_000 + 3) // 7)

    def roll(self, t_ms: int) -> None:
        dk = self._day_key(t_ms)
        if dk != self.day_key:
            self.day_key = dk
            self.day_realized_eur = 0.0
            self.entries_today = {}
        wk = self._week_key(t_ms)
        if wk != self.week_key:
            self.week_key = wk
            self.week_realized_eur = 0.0

    def note_entry(self, base: str, t_ms: int) -> None:
        self.roll(t_ms)
        self.entries_today[base] = self.entries_today.get(base, 0) + 1

    def note_close(self, net_eur: float, t_ms: int) -> None:
        self.roll(t_ms)
        self.day_realized_eur += net_eur
        self.week_realized_eur += net_eur
        if self.week_realized_eur <= -self.week_loss_limit_eur:
            self.paused_until_ms = max(
                self.paused_until_ms, t_ms + int(self.pause_hours * 3600_000)
            )

    def entries_allowed(self, t_ms: int) -> tuple[bool, str]:
        self.roll(t_ms)
        if t_ms < self.paused_until_ms:
            return False, "week_loss_pause"
        if self.day_realized_eur <= -self.day_loss_limit_eur:
            return False, "day_loss_limit"
        return True, "ok"

    def blocked_bases(self, t_ms: int, max_per_day: int) -> set[str]:
        self.roll(t_ms)
        return {b for b, n in self.entries_today.items() if n >= max_per_day}

    def to_dict(self) -> dict[str, Any]:
        return {
            "day_key": self.day_key,
            "day_realized_eur": round(self.day_realized_eur, 4),
            "week_key": self.week_key,
            "week_realized_eur": round(self.week_realized_eur, 4),
            "paused_until_ms": self.paused_until_ms,
            "entries_today": dict(self.entries_today),
        }

    @classmethod
    def from_dict(cls, cfg: DeskConfig, data: Mapping[str, Any] | None) -> RiskLedger:
        led = cls(
            day_loss_limit_eur=cfg.day_loss_limit_eur,
            week_loss_limit_eur=cfg.week_loss_limit_eur,
            pause_hours=cfg.pause_hours_after_week_limit,
        )
        if data:
            led.day_key = str(data.get("day_key") or "")
            led.day_realized_eur = float(data.get("day_realized_eur") or 0.0)
            led.week_key = str(data.get("week_key") or "")
            led.week_realized_eur = float(data.get("week_realized_eur") or 0.0)
            led.paused_until_ms = int(data.get("paused_until_ms") or 0)
            led.entries_today = {
                str(k): int(v) for k, v in (data.get("entries_today") or {}).items()
            }
        return led

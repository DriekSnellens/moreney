"""BTC-core + weekly RS clip — independent book beside the mix.

20% BTC while close > SMA50; 80% in one liquid alt if 20d-style skip-1
excess vs BTC > 4% (lookback 10). Weekly rebalance. 10% trailing stop on
the alt sleeve only. No per-coin hardcodes.
Paper until ``momentum_btc_rs_clip_allow_live`` arms venue fills.

Existing lots are not resized until ``rebalance_due`` so a live restart
does not flatten or dump the book.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE

FEE_RT = 0.003
SLIP = 0.001

# Full liquid Bitvavo EUR pool (ignition_expand_candles), not the 16-name desk.
EXPAND_LIQUID_UNIVERSE: tuple[str, ...] = (
    "AAVE",
    "ADA",
    "ALGO",
    "ALICE",
    "ARB",
    "ARK",
    "ATOM",
    "AVAX",
    "BCH",
    "BNB",
    "CAP",
    "COTI",
    "CRV",
    "CT",
    "CVX",
    "DATAIP",
    "DOGE",
    "DOT",
    "EIGEN",
    "ENA",
    "ENJ",
    "ETH",
    "FARTCOIN",
    "FET",
    "GLMR",
    "GRASS",
    "GTC",
    "HBAR",
    "HYPE",
    "ICP",
    "INJ",
    "JASMY",
    "JUP",
    "KAS",
    "LINK",
    "LPT",
    "LSK",
    "LTC",
    "MAGIC",
    "MANA",
    "MEGA",
    "MON",
    "MOVR",
    "NEAR",
    "NOM",
    "NPC",
    "ONDO",
    "OP",
    "PENGU",
    "PEPE",
    "PHA",
    "PLUME",
    "PUMP",
    "QNT",
    "RAY",
    "RENDER",
    "SAND",
    "SCR",
    "SEI",
    "SHIB",
    "SKY",
    "SOL",
    "STX",
    "SUI",
    "SUPER",
    "SWEAT",
    "SYN",
    "TAO",
    "TIA",
    "TRX",
    "UNI",
    "USELESS",
    "VET",
    "VIRTUAL",
    "VVV",
    "WIF",
    "WLD",
    "XDP",
    "XLM",
    "XPL",
    "XRP",
    "ZRO",
)


@dataclass(frozen=True)
class ClipConfig:
    book_eur: float = 20_000.0
    btc_frac: float = 0.20
    alt_frac: float = 0.80
    excess_floor: float = 0.04
    lookback_days: int = 10
    skip_days: int = 1
    rebalance_days: int = 7
    # If set (0=Mon … 6=Sun), weekly clock only fires on that weekday
    # after ``rebalance_days`` have elapsed. None / <0 = any day.
    rebalance_weekday: int | None = None
    sma_n: int = 50
    min_qvol_eur: float = 80_000.0
    min_notional_eur: float = 50.0
    fee_rt: float = FEE_RT
    slip: float = SLIP
    alt_trail_pct: float = 0.10
    resize_band: float = 0.10
    # Alt must also close above its own SMA. Off on the armed 20/80 clip.
    require_alt_sma: bool = False
    # With no qualifying alt, do not open a BTC sleeve. Paired with btc_frac 0.
    cash_when_no_alt: bool = False
    # Moonshot preimage gates (coin-agnostic). 0 / False = off.
    min_r3_pct: float = 0.0
    require_trend: bool = False
    # Cap sizing base at book_eur so a small sleeve stays fixed-size.
    size_to_book: bool = False
    # weekly_rs | top_day | coil_day | brk20_day | top_rs
    entry_mode: str = "weekly_rs"
    # Exit alt after N calendar days (0 = trail/rotate only).
    time_max_days: int = 0
    # Hard stop from entry (0 = off).
    hard_stop_pct: float = 0.0
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    decision_hours_utc: tuple[int, ...] = (0,)
    tick_sec: float = 30.0
    ohlc_days: int = 120
    # >0: scan for entries through the day (MoonShot first-breakout). 0 = hour clock.
    entry_scan_sec: float = 0.0
    # 0 = off. MoonShot refuses a cross that is already this far above yesterday's close.
    max_entry_day_ret: float = 0.0
    # 0 = off. MoonShot only buys while price is still this close above the 20d high.
    max_break_extension: float = 0.0


def residual_full_config(cfg: ClipConfig) -> ClipConfig:
    """PnL pack: 100% one residual alt, own SMA50, cash when none qualify.

    Same 10d skip-1 week clock, SMA50 flatten and 10% alt trail as the clip.
    Excess floor 3.5% sits in the flat 3.2–3.6% band from the wet replay.
    """
    return replace(
        cfg,
        btc_frac=0.0,
        alt_frac=1.0,
        excess_floor=0.035,
        require_alt_sma=True,
        cash_when_no_alt=True,
    )


def moonshot_spike_config(cfg: ClipConfig | None = None) -> ClipConfig:
    """Deprecated alias — use ``daily_green_config`` (active top_day sleeve)."""
    return daily_green_config(cfg)


def daily_green_config(cfg: ClipConfig | None = None) -> ClipConfig:
    """Fixed €1.7k daily-active sleeve (walk-forward dual IS+OOS winner).

    Full liquid universe (~80 names). Risk-on: 20d breakout + day thrust
    (brk20_day), trail 12%, hard-stop 5%, time-stop 5d, size capped at book.
    BTC SMA50 filter on. Optimized for green weeks − DD, not spike-fit alone.
    """
    base = cfg or ClipConfig()
    uni = tuple(base.universe) if base.universe else EXPAND_LIQUID_UNIVERSE
    if uni == DEFAULT_UNIVERSE:
        uni = EXPAND_LIQUID_UNIVERSE
    qvol = float(base.min_qvol_eur or 0.0)
    if qvol <= 0:
        qvol = 50_000.0
    elif qvol > 50_000.0:
        qvol = 50_000.0
    return replace(
        base,
        book_eur=float(base.book_eur) if float(base.book_eur) > 0 else 1_700.0,
        btc_frac=0.0,
        alt_frac=1.0,
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        rebalance_days=1,
        sma_n=50,
        min_qvol_eur=qvol,
        require_alt_sma=False,
        cash_when_no_alt=True,
        min_r3_pct=0.0,
        require_trend=False,
        size_to_book=True,
        alt_trail_pct=0.12,
        entry_mode="brk20_day",
        time_max_days=5,
        hard_stop_pct=0.05,
        universe=uni,
    )


@dataclass
class ClipPosition:
    base: str
    entry_price: float
    notional_eur: float
    qty: float
    opened_ms: int
    role: str = "btc"
    holding_id: str = ""
    venue: str = "paper"
    entry_reason: str = ""
    peak_px: float = 0.0

    def __post_init__(self) -> None:
        if not self.holding_id:
            self.holding_id = f"clip-{self.base}-{uuid.uuid4().hex[:8]}"

    def is_paper(self) -> bool:
        return str(self.venue or "paper").lower() in {"paper", "", "synthetic"}

    def gross_return(self, mark: float) -> float:
        if self.entry_price <= 0 or mark <= 0:
            return 0.0
        return mark / self.entry_price - 1.0

    def unrealized_net(self, mark: float, fee_rt: float) -> float:
        ret = self.gross_return(mark)
        return self.notional_eur * ret - self.notional_eur * (fee_rt / 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ClipPosition:
        return cls(
            base=str(raw["base"]),
            entry_price=float(raw["entry_price"]),
            notional_eur=float(raw["notional_eur"]),
            qty=float(raw.get("qty") or 0.0),
            opened_ms=int(raw["opened_ms"]),
            role=str(raw.get("role") or "btc"),
            holding_id=str(raw.get("holding_id") or ""),
            venue=str(raw.get("venue") or "paper"),
            entry_reason=str(raw.get("entry_reason") or ""),
            peak_px=float(raw["peak_px"]) if raw.get("peak_px") not in (None, "") else 0.0,
        )


def fill_px(close: float, side: str, *, slip: float = SLIP) -> float:
    if close <= 0:
        return 0.0
    if side == "buy":
        return close * (1.0 + slip)
    return close * (1.0 - slip)


def completed_ohlc(
    rows: Sequence[Sequence[float]], *, now: datetime | None = None
) -> list[list[float]]:
    """Drop the in-progress UTC day so signals use closed 1d bars only."""
    out = [list(r) for r in rows]
    if not out:
        return out
    now = now or datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    last_d = datetime.fromtimestamp(int(out[-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    if last_d == today:
        return out[:-1]
    return out


def sma(closes: Sequence[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(float(x) for x in closes[-n:]) / n


def quote_vol(rows: Sequence[Sequence[float]], n: int = 20) -> float:
    if len(rows) < 1:
        return 0.0
    use = rows[-n:] if len(rows) >= n else rows
    vs = [float(r[5]) * float(r[4]) for r in use if float(r[4]) > 0]
    return sum(vs) / len(vs) if vs else 0.0


def rs_excess(
    alt: Sequence[float],
    btc: Sequence[float],
    *,
    lb: int,
    skip: int,
) -> float | None:
    need = lb + skip + 1
    if len(alt) < need or len(btc) < need:
        return None
    e, s = -1 - skip, -1 - skip - lb
    if alt[s] <= 0 or btc[s] <= 0:
        return None
    return (alt[e] / alt[s] - 1.0) - (btc[e] / btc[s] - 1.0)


def closes_of(rows: Sequence[Sequence[float]]) -> list[float]:
    return [float(r[4]) for r in rows if float(r[4]) > 0]


def default_config() -> ClipConfig:
    return ClipConfig()


def load_alphai_breakout_sets(path: str | None = None) -> tuple[frozenset[str], frozenset[str]]:
    """AlphaI picks and avoid-list for the live breakout scan."""
    from pathlib import Path

    from bot.live.momentum_desk import AlphaIView

    if path is None:
        from bot.core.config import get_settings

        path = str(getattr(get_settings(), "alphai_daily_recommendations_path", "") or "")
    if not path:
        return frozenset(), frozenset()
    file = Path(path)
    if not file.exists():
        return frozenset(), frozenset()
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return frozenset(), frozenset()
    if not isinstance(payload, dict):
        return frozenset(), frozenset()
    view = AlphaIView.from_recommendations(payload)
    return frozenset(view.picks), frozenset(view.avoid)


def fresh_alphai_breakout_sets(
    *,
    path: str | None = None,
    focus_bases: Collection[str] | None = None,
    now: datetime | None = None,
    client: Any | None = None,
    interval_minutes: int | None = None,
    allow_network: bool = True,
) -> tuple[frozenset[str], frozenset[str]]:
    """Picks and avoid from the current AlphaI session.

    A scan refreshes the file when its session bucket has rolled. A list that
    is still stale after that attempt is ignored, so yesterday's picks cannot
    steer the breakout.
    """
    from bot.integrations.alphai.daily_recommendations import (
        load_daily_recommendations,
        maybe_refresh_daily,
        needs_session_refresh,
    )

    instant = now or datetime.now(UTC)
    settings = None
    if path is None or interval_minutes is None or (allow_network and client is None):
        from bot.core.config import get_settings

        settings = get_settings()
    if path is None and settings is not None:
        path = str(getattr(settings, "alphai_daily_recommendations_path", "") or "")
    if not path:
        return frozenset(), frozenset()
    minutes = interval_minutes
    if minutes is None and settings is not None:
        minutes = int(getattr(settings, "alphai_recommendations_interval_minutes", 15) or 15)
    minutes = int(minutes or 15)
    hour = 12
    if settings is not None:
        hour = int(getattr(settings, "alphai_daily_recommendations_hour", 12) or 12)

    def _current(report: dict[str, Any] | None) -> tuple[frozenset[str], frozenset[str]] | None:
        if not report or needs_session_refresh(
            report,
            now=instant,
            interval_minutes=minutes,
            update_hour_local=hour,
        ):
            return None
        from bot.live.momentum_desk import AlphaIView

        view = AlphaIView.from_recommendations(report)
        return frozenset(view.picks), frozenset(view.avoid)

    cached = load_daily_recommendations(path)
    fresh = _current(cached)
    if fresh is not None:
        return fresh
    if not allow_network:
        return frozenset(), frozenset()

    if client is None and settings is not None:
        client = _alphai_client_from_settings(settings)
    if client is None:
        return frozenset(), frozenset()

    from bot.integrations.alphai.regime import _parse_csv_bases
    from bot.integrations.alphai.symbols import LIQUID_EUR_BASES

    focus = {str(b).upper() for b in (focus_bases or ()) if b}
    focus |= set(LIQUID_EUR_BASES)
    if settings is not None:
        focus |= _parse_csv_bases(getattr(settings, "live_micro_focus_bases", "") or "", fallback=set())
    report = maybe_refresh_daily(
        client,
        path,
        focus_bases=focus or set(LIQUID_EUR_BASES),
        enabled=True,
        min_relevance=int(
            getattr(settings, "alphai_daily_recommendations_min_relevance", 6) or 6
        )
        if settings is not None
        else 6,
        top_n=int(getattr(settings, "alphai_daily_recommendations_top_n", 8) or 8)
        if settings is not None
        else 8,
        update_hour_local=hour,
        interval_minutes=minutes,
        interval_hours=int(getattr(settings, "alphai_recommendations_interval_hours", 1) or 1)
        if settings is not None
        else 1,
        now=instant,
    )
    fresh = _current(report if isinstance(report, dict) else None)
    if fresh is not None:
        return fresh
    return frozenset(), frozenset()


def _alphai_client_from_settings(settings: Any) -> Any | None:
    import os

    if not bool(getattr(settings, "alphai_enabled", False)):
        return None
    from bot.integrations.alphai.client import AlphaIClient

    key = getattr(settings, "alphai_api_key", None)
    secret = ""
    if key is not None and hasattr(key, "get_secret_value"):
        secret = str(key.get_secret_value() or "")
    elif key:
        secret = str(key)
    if not secret:
        secret = os.environ.get("ALPHAI_API_KEY", "")
    if not secret:
        return None
    return AlphaIClient(secret)


def evaluate_clip(
    ohlc_by_base: Mapping[str, Sequence[Sequence[float]]],
    cfg: ClipConfig,
    *,
    held: Mapping[str, str],
    cash_eur: float,
    deployed_eur: float,
    now_ms: int,
    last_rebalance_ms: int,
    now: datetime | None = None,
    sleeve_eur: Mapping[str, float] | None = None,
    live_marks: Mapping[str, float] | None = None,
    alphai_picks: Collection[str] | None = None,
    alphai_avoid: Collection[str] | None = None,
) -> dict[str, Any]:
    """Decide clip longs. ``held`` maps base → role (btc|alt)."""
    now = now or datetime.now(UTC)
    btc_rows = completed_ohlc(ohlc_by_base.get("BTC") or [], now=now)
    btc_c = closes_of(btc_rows)
    s50 = sma(btc_c, cfg.sma_n)
    last = float(btc_c[-1]) if btc_c else 0.0
    if s50 is None:
        return {
            "ok": False,
            "risk_block": "sma_unavailable",
            "risk_on": False,
            "sma50": None,
            "btc": last,
            "exits": [],
            "entries": [],
            "want_btc": False,
            "want_alt": None,
            "caption": "BTC SMA50 nog niet klaar — bags blijven staan.",
            "ranked": [],
            "rebalance_due": False,
            "trims": [],
        }
    risk_on = last > s50
    raw_equity = max(0.0, float(cash_eur) + float(deployed_eur))
    equity = (
        min(raw_equity, float(cfg.book_eur))
        if bool(cfg.size_to_book) and float(cfg.book_eur) > 0
        else raw_equity
    )
    held_btc = next((b for b, role in held.items() if role == "btc"), None)
    held_alt = next((b for b, role in held.items() if role == "alt"), None)
    exits: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    if not risk_on:
        for base, role in held.items():
            exits.append({"base": base, "reason": "btc_below_sma50", "role": role})
        return {
            "ok": True,
            "risk_block": "",
            "risk_on": False,
            "sma50": s50,
            "btc": last,
            "exits": exits,
            "entries": [],
            "want_btc": False,
            "want_alt": None,
            "caption": "BTC onder SMA50 — clip in cash.",
            "ranked": [],
            "rebalance_due": False,
            "gap_pct": round(last / s50 - 1.0, 4),
            "trims": [],
        }

    reb_ms = int(cfg.rebalance_days) * 86_400_000
    # A fresh book (no clock yet) may enter. After a trail or weekly check the
    # clock blocks the next buy, including when the book is already flat.
    age_due = last_rebalance_ms <= 0 or (now_ms - last_rebalance_ms) >= reb_ms
    wd = cfg.rebalance_weekday
    if age_due and wd is not None and int(wd) >= 0:
        rebalance_due = now.weekday() == int(wd)
    else:
        rebalance_due = age_due

    mode = str(cfg.entry_mode or "weekly_rs").lower()
    day_modes = {"top_day", "coil_day", "brk20_day", "brk20_now"}
    alphai_pick_set = {str(x).upper() for x in (alphai_picks or ())}
    alphai_avoid_set = {str(x).upper() for x in (alphai_avoid or ())}
    marks = {
        str(k).upper(): float(v)
        for k, v in dict(live_marks or {}).items()
        if float(v or 0.0) > 0
    }
    ranked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for base in cfg.universe:
        rows = completed_ohlc(ohlc_by_base.get(base) or [], now=now)
        cl = closes_of(rows)
        xs = rs_excess(cl, btc_c, lb=cfg.lookback_days, skip=cfg.skip_days)
        qv = quote_vol(rows)
        if mode not in day_modes and xs is None:
            skipped.append({"base": base, "reason": "short_history"})
            continue
        if mode in day_modes and len(cl) < 2:
            skipped.append({"base": base, "reason": "short_history"})
            continue
        if qv < cfg.min_qvol_eur:
            skipped.append(
                {
                    "base": base,
                    "reason": "thin_volume",
                    "qvol": round(qv, 0),
                    "excess": round(xs, 4) if xs is not None else None,
                }
            )
            continue
        if cfg.require_alt_sma:
            s_alt = sma(cl, cfg.sma_n)
            last_alt = float(cl[-1]) if cl else 0.0
            if s_alt is None or last_alt <= s_alt:
                skipped.append(
                    {
                        "base": base,
                        "reason": "below_sma",
                        "excess": round(xs, 4) if xs is not None else None,
                    }
                )
                continue
        if float(cfg.min_r3_pct or 0.0) > 0:
            if len(cl) < 4 or cl[-4] <= 0:
                skipped.append(
                    {
                        "base": base,
                        "reason": "short_r3",
                        "excess": round(xs, 4) if xs is not None else None,
                    }
                )
                continue
            r3 = cl[-1] / cl[-4] - 1.0
            if r3 < float(cfg.min_r3_pct):
                skipped.append(
                    {
                        "base": base,
                        "reason": "weak_r3",
                        "r3": round(r3, 4),
                        "excess": round(xs, 4) if xs is not None else None,
                    }
                )
                continue
        else:
            r3 = None
        if cfg.require_trend:
            s20 = sma(cl, 20)
            s50_alt = sma(cl, cfg.sma_n)
            last_alt = float(cl[-1]) if cl else 0.0
            if s20 is None or s50_alt is None or not (last_alt > s20 > s50_alt):
                skipped.append(
                    {
                        "base": base,
                        "reason": "no_trend",
                        "excess": round(xs, 4) if xs is not None else None,
                    }
                )
                continue
        day_ret = 0.0
        if len(cl) >= 2 and cl[-2] > 0:
            day_ret = cl[-1] / cl[-2] - 1.0
        highs = [float(r[2]) for r in rows if len(r) >= 3 and float(r[2]) > 0]
        lows = [float(r[3]) for r in rows if len(r) >= 4 and float(r[3]) > 0]
        coil = False
        if len(highs) >= 20 and len(lows) >= 20:
            hi5, lo5 = max(highs[-5:]), min(lows[-5:])
            hi20, lo20 = max(highs[-20:]), min(lows[-20:])
            span5 = hi5 / lo5 - 1.0 if lo5 > 0 else 0.0
            span20 = hi20 / lo20 - 1.0 if lo20 > 0 else 0.0
            coil = span20 > 1e-9 and span5 / span20 < 0.5
        brk20 = False
        if len(highs) >= 21 and cl:
            prior_hi20 = max(highs[-21:-1])
            brk20 = float(cl[-1]) >= prior_hi20
        if mode == "coil_day":
            if not coil or day_ret < float(cfg.excess_floor):
                skipped.append(
                    {
                        "base": base,
                        "reason": "no_coil" if not coil else "weak_day",
                        "day_ret": round(day_ret, 4),
                    }
                )
                continue
        if mode == "brk20_day":
            if not brk20 or day_ret < float(cfg.excess_floor):
                skipped.append(
                    {
                        "base": base,
                        "reason": "no_breakout" if not brk20 else "weak_day",
                        "day_ret": round(day_ret, 4),
                    }
                )
                continue
        if mode == "brk20_now":
            # First breakout: yesterday's close is still under the 20d high,
            # and the live price is crossing it now. Yesterday's breakout is
            # already in the price, so it does not qualify.
            mark = marks.get(base, 0.0)
            prev_close = float(cl[-1]) if cl else 0.0
            if mark <= 0 or prev_close <= 0 or len(highs) < 20:
                skipped.append({"base": base, "reason": "short_history"})
                continue
            prior_hi = max(highs[-20:])
            live_ret = mark / prev_close - 1.0
            if prev_close >= prior_hi:
                skipped.append(
                    {
                        "base": base,
                        "reason": "already_broken",
                        "day_ret": round(live_ret, 4),
                    }
                )
                continue
            if mark < prior_hi or live_ret < float(cfg.excess_floor):
                skipped.append(
                    {
                        "base": base,
                        "reason": "no_breakout" if mark < prior_hi else "weak_day",
                        "day_ret": round(live_ret, 4),
                    }
                )
                continue
            day_cap = float(getattr(cfg, "max_entry_day_ret", 0.0) or 0.0)
            ext_cap = float(getattr(cfg, "max_break_extension", 0.0) or 0.0)
            extension = mark / prior_hi - 1.0 if prior_hi > 0 else 0.0
            if (day_cap > 0 and live_ret > day_cap) or (ext_cap > 0 and extension > ext_cap):
                skipped.append(
                    {
                        "base": base,
                        "reason": "too_extended",
                        "day_ret": round(live_ret, 4),
                    }
                )
                continue
            if base in alphai_avoid_set:
                skipped.append(
                    {
                        "base": base,
                        "reason": "alphai_avoid",
                        "day_ret": round(live_ret, 4),
                    }
                )
                continue
            ranked.append(
                {
                    "base": base,
                    "excess": float(xs) if xs is not None else 0.0,
                    "day_ret": live_ret,
                    "qvol": round(qv, 0),
                    "coil": False,
                    "brk20": True,
                    "alphai_pick": base in alphai_pick_set,
                }
            )
            continue
        row = {
            "base": base,
            "excess": float(xs) if xs is not None else 0.0,
            "day_ret": day_ret,
            "qvol": round(qv, 0),
            "coil": coil,
            "brk20": brk20,
        }
        if r3 is not None:
            row["r3"] = round(float(r3), 4)
        ranked.append(row)
    if mode == "brk20_now":
        ranked.sort(
            key=lambda r: (
                float(r.get("day_ret") or 0.0),
                1.0 if r.get("alphai_pick") else 0.0,
            ),
            reverse=True,
        )
    elif mode in day_modes:
        ranked.sort(key=lambda r: float(r.get("day_ret") or 0.0), reverse=True)
    else:
        ranked.sort(key=lambda r: float(r["excess"]), reverse=True)
    want_alt: str | None = None
    if rebalance_due and ranked:
        top = ranked[0]
        if mode in day_modes:
            want_alt = str(top["base"])
        elif float(top["excess"]) > cfg.excess_floor:
            want_alt = str(top["base"])
    elif not rebalance_due:
        want_alt = held_alt

    if held_alt and held_alt != want_alt:
        exits.append(
            {"base": held_alt, "reason": "rs_rotate" if want_alt else "rs_drop", "role": "alt"}
        )

    cash_left = float(cash_eur)
    # btc_frac 0 is the full residual book: drop the BTC sleeve on the weekly check.
    if rebalance_due and held_btc and float(cfg.btc_frac) <= 0:
        exits.append({"base": held_btc, "reason": "btc_sleeve_off", "role": "btc"})
        if want_alt and held_alt == want_alt:
            alt_n = float((sleeve_eur or {}).get("alt") or 0.0)
            buy_alt = equity * float(cfg.alt_frac) - alt_n
            if buy_alt >= cfg.min_notional_eur:
                top = ranked[0] if ranked and ranked[0]["base"] == want_alt else {"excess": 0.0}
                entries.append(
                    {
                        "base": want_alt,
                        "notional_eur": round(buy_alt, 2),
                        "role": "alt",
                        "reasons": [
                            f"excess={float(top.get('excess') or 0):.3f}",
                            f"frac={cfg.alt_frac:.2f}",
                            "btc_sleeve_off",
                        ],
                    }
                )
    elif (
        rebalance_due
        and held_btc
        and cfg.cash_when_no_alt
        and not want_alt
    ):
        exits.append({"base": held_btc, "reason": "cash_no_alt", "role": "btc"})
    # Conservative: assume exits free cash after they fill; entries size from equity.
    skip_btc_buy = cfg.cash_when_no_alt and not want_alt
    if not held_btc and float(cfg.btc_frac) > 0 and not skip_btc_buy:
        n_btc = min(cash_left * 0.98, equity * float(cfg.btc_frac))
        if n_btc >= cfg.min_notional_eur:
            entries.append(
                {
                    "base": "BTC",
                    "notional_eur": round(n_btc, 2),
                    "role": "btc",
                    "reasons": [f"sma{cfg.sma_n}_up", f"frac={cfg.btc_frac:.2f}"],
                }
            )
    if want_alt and want_alt != held_alt:
        n_alt = equity * float(cfg.alt_frac)
        if n_alt >= cfg.min_notional_eur:
            top = ranked[0] if ranked and ranked[0]["base"] == want_alt else {"excess": 0.0}
            entries.append(
                {
                    "base": want_alt,
                    "notional_eur": round(n_alt, 2),
                    "role": "alt",
                    "reasons": [
                        (
                            f"day={float(top.get('day_ret') or 0):.3f}"
                            if mode in day_modes
                            else f"excess={float(top.get('excess') or 0):.3f}"
                        ),
                        f"frac={cfg.alt_frac:.2f}",
                        mode if mode != "weekly_rs" else "weekly_rs",
                    ],
                }
            )

    trims: list[dict[str, Any]] = []
    sleeves = {str(k): float(v) for k, v in dict(sleeve_eur or {}).items()}
    same_names = (
        rebalance_due
        and risk_on
        and held_btc
        and want_alt
        and held_alt == want_alt
        and not any(e.get("base") == "BTC" for e in exits)
    )
    if same_names and equity > 0:
        btc_n = float(sleeves.get("btc") or 0.0)
        alt_n = float(sleeves.get("alt") or 0.0)
        if abs(btc_n / equity - float(cfg.btc_frac)) > float(cfg.resize_band):
            target_btc = equity * float(cfg.btc_frac)
            sell_btc = btc_n - target_btc
            if sell_btc >= cfg.min_notional_eur:
                trims.append(
                    {
                        "base": "BTC",
                        "role": "btc",
                        "sell_notional_eur": round(sell_btc, 2),
                        "reason": "size_to_frac",
                    }
                )
            target_alt = equity * float(cfg.alt_frac)
            buy_alt = target_alt - alt_n
            if buy_alt >= cfg.min_notional_eur and want_alt:
                entries.append(
                    {
                        "base": want_alt,
                        "notional_eur": round(buy_alt, 2),
                        "role": "alt",
                        "reasons": [
                            f"excess={float((ranked[0] if ranked else {}).get('excess') or 0):.3f}",
                            f"frac={cfg.alt_frac:.2f}",
                            "size_to_frac",
                        ],
                    }
                )

    alt_txt = want_alt or "geen alt"
    if mode in day_modes or float(cfg.min_r3_pct or 0.0) > 0:
        detail = ""
        if want_alt and ranked:
            top = ranked[0]
            if mode in day_modes:
                detail = f" (day {float(top.get('day_ret') or 0):+.1%})"
            else:
                detail = f" (xs {float(top['excess']):+.1%}"
                if top.get("r3") is not None:
                    detail += f", r3 {float(top['r3']):+.1%}"
                detail += ")"
        if mode == "coil_day":
            gate = f"coil_day+day≥{cfg.excess_floor:.0%}"
        elif mode == "brk20_now":
            gate = "eerste 20d-breakout"
            if want_alt and ranked and ranked[0].get("alphai_pick"):
                gate += " · AlphaI"
        elif mode == "brk20_day":
            gate = f"brk20_day+day≥{cfg.excess_floor:.0%}"
        elif mode == "top_day":
            gate = "top_day"
        else:
            gate = (
                f"r3≥{cfg.min_r3_pct:.0%}+xs≥{cfg.excess_floor:.0%}"
                + ("+trend" if cfg.require_trend else "")
            )
        caption = (
            f"Daily sleeve €{cfg.book_eur:,.0f}: {alt_txt}{detail}"
            f", {gate}"
            + (f", trail {cfg.alt_trail_pct:.0%}" if float(cfg.alt_trail_pct or 0) > 0 else "")
            + (f", hs {cfg.hard_stop_pct:.0%}" if float(cfg.hard_stop_pct or 0) > 0 else "")
            + (f", time≤{cfg.time_max_days}d" if int(cfg.time_max_days or 0) > 0 else "")
            + ". Vast boek — elke risk-on dag actief."
        )
    else:
        caption = (
            f"Clip: {int(cfg.btc_frac * 100)}% BTC boven SMA{cfg.sma_n}, "
            f"{int(cfg.alt_frac * 100)}% {alt_txt}"
            + (f" (excess {ranked[0]['excess']:+.1%})" if want_alt and ranked else "")
            + (f", alt-trail {cfg.alt_trail_pct:.0%}" if float(cfg.alt_trail_pct or 0) > 0 else "")
            + f", {cfg.lookback_days}d RS. Telt niet mee in live mix-equity."
        )
    return {
        "ok": True,
        "risk_block": "",
        "risk_on": True,
        "sma50": s50,
        "btc": last,
        "exits": exits,
        "entries": entries,
        "want_btc": True,
        "want_alt": want_alt,
        "caption": caption,
        "ranked": ranked[:8],
        "skipped": skipped[:12],
        "rebalance_due": rebalance_due,
        "gap_pct": round(last / s50 - 1.0, 4),
        "trims": trims,
    }

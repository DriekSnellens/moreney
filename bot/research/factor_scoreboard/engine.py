"""Pre-registered factor grid on the live residual pack.

Every overlay is a generic gate or size scale evaluated on the signal date
with data through that close. Baseline is the armed ``residual_full`` pack
(near-high 0.25, trail 10%, lock +12%/+6%). No per-coin branches.

Families:
  sentiment   Fear & Greed gates and a soft scale
  positioning Binance perp funding z-score / absolute level, BTC funding
  regime      BTC off-peak de-risk, cross-sectional dispersion, breadth
  sizing      realized-vol targeting
  trend       own time-series return gate (TSMOM), own lookback return
  clock       shorter re-entry after a trail/lock sell
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from bot.live.momentum_btc_rs_clip import closes_of
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import rows_through
from bot.research.clip_exit_lab.policies import ExitPolicy

Gate = Callable[[str, str], bool]
Scale = Callable[[str, str], float]
Ohlc = Mapping[str, Sequence[Sequence[float]]]

LIVE_POLICY = ExitPolicy(
    name="trail10_lock12_6", alt_trail_pct=0.10, alt_lock_arm_pct=0.12, alt_lock_floor_pct=0.06
)
LIVE_KNOBS: dict[str, Any] = {
    "btc_frac": 0.0,
    "excess_floor": 0.035,
    "flatten": "all",
    "sma_n": 50,
    "rebalance_days": 7,
    "lookback_days": 10,
    "skip_days": 1,
    "require_alt_sma": True,
    "cash_when_no_alt": True,
    "near_high_weight": 0.25,
    "policy": LIVE_POLICY,
}


def prev_day(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


# ---------- features (all through ``date``) ----------
def realized_vol(ohlc: Ohlc, base: str, date: str, n: int = 20) -> float | None:
    cl = closes_of(rows_through(ohlc.get(base) or [], date))
    if len(cl) < n + 1:
        return None
    rets = [math.log(cl[i] / cl[i - 1]) for i in range(len(cl) - n, len(cl)) if cl[i - 1] > 0]
    if len(rets) < n - 2:
        return None
    return statistics.pstdev(rets) * math.sqrt(365.0)


def btc_off_peak(ohlc: Ohlc, date: str, n: int = 100) -> float:
    cl = closes_of(rows_through(ohlc.get("BTC") or [], date))
    if len(cl) < 10:
        return 0.0
    peak = max(cl[-n:])
    return 1.0 - cl[-1] / peak if peak > 0 else 0.0


def ret_n(ohlc: Ohlc, base: str, date: str, n: int) -> float | None:
    cl = closes_of(rows_through(ohlc.get(base) or [], date))
    if len(cl) <= n or cl[-1 - n] <= 0:
        return None
    return cl[-1] / cl[-1 - n] - 1.0


def dispersion(ohlc: Ohlc, date: str, universe: Sequence[str], lb: int = 10) -> float | None:
    rets = []
    for base in universe:
        r = ret_n(ohlc, base, date, lb)
        if r is not None:
            rets.append(r)
    if len(rets) < 6:
        return None
    return statistics.pstdev(rets)


def breadth(ohlc: Ohlc, date: str, universe: Sequence[str], n: int = 20) -> float | None:
    up = seen = 0
    for base in universe:
        r = ret_n(ohlc, base, date, n)
        if r is None:
            continue
        seen += 1
        up += r > 0
    return up / seen if seen else None


class DispersionPercentile:
    """Causal percentile of today's dispersion against the prior window."""

    def __init__(
        self,
        ohlc: Ohlc,
        dates: Sequence[str],
        universe: Sequence[str],
        window: int = 180,
        min_hist: int = 60,
    ):
        self.hist: dict[str, float] = {}
        for d in dates:
            v = dispersion(ohlc, d, universe)
            if v is not None:
                self.hist[d] = v
        self.days = sorted(self.hist)
        self.window = window
        self.min_hist = min_hist

    def __call__(self, date: str) -> float | None:
        if date not in self.hist:
            return None
        idx = self.days.index(date)
        past = [self.hist[x] for x in self.days[max(0, idx - self.window) : idx]]
        if len(past) < self.min_hist:
            return None
        v = self.hist[date]
        return sum(1 for p in past if p <= v) / len(past)


def funding_z(series: Mapping[str, float], date: str, n: int = 3, win: int = 60) -> float | None:
    days = sorted(d for d in series if d <= date)
    if len(days) < win + n:
        return None
    recent = [series[d] for d in days[-n:]]
    hist = [series[d] for d in days[-(win + n) : -n]]
    sd = statistics.pstdev(hist)
    if sd <= 0:
        return None
    return (statistics.mean(recent) - statistics.mean(hist)) / sd


# ---------- overlay builders ----------
def gate_fng_min(fng: Mapping[str, int], th: int) -> Gate:
    return lambda date, base: fng.get(date, fng.get(prev_day(date), 50)) >= th


def scale_fng_soft(fng: Mapping[str, int], lo: int, hi: int, floor: float = 0.5) -> Scale:
    def f(date: str, base: str) -> float:
        v = fng.get(date, fng.get(prev_day(date), 50))
        if v >= hi:
            return 1.0
        if v <= lo:
            return floor
        return floor + (1.0 - floor) * (v - lo) / (hi - lo)

    return f


def scale_vol_target(ohlc: Ohlc, target: float) -> Scale:
    def f(date: str, base: str) -> float:
        rv = realized_vol(ohlc, base, date)
        return 1.0 if not rv else min(1.0, target / rv)

    return f


def scale_drawdown(ohlc: Ohlc, th: float, mult: float) -> Scale:
    return lambda date, base: mult if btc_off_peak(ohlc, date) > th else 1.0


def gate_drawdown(ohlc: Ohlc, th: float) -> Gate:
    return lambda date, base: btc_off_peak(ohlc, date) <= th


def gate_dispersion(pct_fn: Callable[[str], float | None], pct: float) -> Gate:
    def f(date: str, base: str) -> bool:
        p = pct_fn(date)
        return p is None or p < pct

    return f


def scale_dispersion(pct_fn: Callable[[str], float | None], pct: float, mult: float) -> Scale:
    def f(date: str, base: str) -> float:
        p = pct_fn(date)
        return mult if (p is not None and p >= pct) else 1.0

    return f


def gate_tsmom(ohlc: Ohlc, n: int, th: float = 0.0) -> Gate:
    def f(date: str, base: str) -> bool:
        r = ret_n(ohlc, base, date, n)
        return r is None or r > th

    return f


def gate_funding_z(
    fund: Mapping[str, Mapping[str, float]], zmax: float, base_key: str | None = None
) -> Gate:
    def f(date: str, base: str) -> bool:
        series = fund.get(base_key or base) or {}
        z = funding_z(series, date)
        return z is None or z < zmax

    return f


def gate_funding_abs(fund: Mapping[str, Mapping[str, float]], rate_max: float, n: int = 3) -> Gate:
    def f(date: str, base: str) -> bool:
        series = fund.get(base) or {}
        days = sorted(d for d in series if d <= date)[-n:]
        return not days or statistics.mean(series[d] for d in days) < rate_max

    return f


def gate_breadth(ohlc: Ohlc, universe: Sequence[str], frac: float) -> Gate:
    def f(date: str, base: str) -> bool:
        b = breadth(ohlc, date, universe)
        return b is None or b >= frac

    return f


# ---------- grid ----------
def default_variants(
    ohlc: Ohlc,
    *,
    fng: Mapping[str, int] | None,
    fund: Mapping[str, Mapping[str, float]] | None,
    dates: Sequence[str],
    universe: Sequence[str] = DEFAULT_UNIVERSE,
) -> list[tuple[str, str, Gate | None, Scale | None, dict[str, Any]]]:
    """(name, family, gate, scale, extra run kwargs)."""
    disp = DispersionPercentile(ohlc, dates, universe)
    rows: list[tuple[str, str, Gate | None, Scale | None, dict[str, Any]]] = [
        ("baseline", "baseline", None, None, {}),
    ]
    if fng:
        rows += [
            ("fng_gate25", "sentiment", gate_fng_min(fng, 25), None, {}),
            ("fng_gate40", "sentiment", gate_fng_min(fng, 40), None, {}),
            ("fng_gate50", "sentiment", gate_fng_min(fng, 50), None, {}),
            ("fng_soft20_60", "sentiment", None, scale_fng_soft(fng, 20, 60), {}),
        ]
    if fund:
        rows += [
            ("fund_z1.5", "positioning", gate_funding_z(fund, 1.5), None, {}),
            ("fund_z2.0", "positioning", gate_funding_z(fund, 2.0), None, {}),
            ("fund_abs0.03", "positioning", gate_funding_abs(fund, 0.0003), None, {}),
            ("btcfund_z1.5", "positioning", gate_funding_z(fund, 1.5, base_key="BTC"), None, {}),
        ]
    rows += [
        ("vol_t60", "sizing", None, scale_vol_target(ohlc, 0.60), {}),
        ("vol_t80", "sizing", None, scale_vol_target(ohlc, 0.80), {}),
        ("vol_t100", "sizing", None, scale_vol_target(ohlc, 1.00), {}),
        ("vol_t120", "sizing", None, scale_vol_target(ohlc, 1.20), {}),
        ("dd15_half", "regime", None, scale_drawdown(ohlc, 0.15, 0.5), {}),
        ("dd20_half", "regime", None, scale_drawdown(ohlc, 0.20, 0.5), {}),
        ("dd15_gate", "regime", gate_drawdown(ohlc, 0.15), None, {}),
        ("dd25_gate", "regime", gate_drawdown(ohlc, 0.25), None, {}),
        ("disp_gate90", "regime", gate_dispersion(disp, 0.90), None, {}),
        ("disp_gate80", "regime", gate_dispersion(disp, 0.80), None, {}),
        ("disp_half90", "regime", None, scale_dispersion(disp, 0.90, 0.5), {}),
        ("breadth40", "regime", gate_breadth(ohlc, universe, 0.40), None, {}),
        ("breadth50", "regime", gate_breadth(ohlc, universe, 0.50), None, {}),
    ]
    rows += [
        (f"tsmom{n}", "trend", gate_tsmom(ohlc, n), None, {})
        for n in (15, 20, 25, 30, 35, 40, 45, 50, 60, 90)
    ]
    rows += [(f"cooldown{k}", "clock", None, None, {"exit_cooldown_days": k}) for k in (1, 2, 3, 5)]
    rows += [("btc_fallback", "clock", None, None, {"cash_when_no_alt": False})]
    return rows


def run_variant(
    ohlc: Ohlc,
    *,
    name: str,
    gate: Gate | None,
    scale: Scale | None,
    extra: Mapping[str, Any],
    windows: Sequence[tuple[str, str, str]],
    book_eur: float = 20_000.0,
) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name}
    knobs = {**LIVE_KNOBS, **dict(extra)}
    for tag, start, end in windows:
        row = run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            entry_gate=gate,
            alt_size_scale=scale,
            strategy=name,
            **knobs,
        )
        out[tag] = {
            "pnl_eur": round(float(row["pnl_eur"]), 0),
            "max_dd_pct": round(float(row["max_dd_pct"]), 4),
            "n_trades": int(row["n_trades"]),
            "year_pnl": row.get("year_pnl"),
        }
    return out


def run_grid(
    ohlc: Ohlc,
    *,
    fng: Mapping[str, int] | None,
    fund: Mapping[str, Mapping[str, float]] | None,
    windows: Sequence[tuple[str, str, str]],
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    book_eur: float = 20_000.0,
    log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    from bot.research.clip_exit_lab.engine import bar_date

    dates = [bar_date(r) for r in (ohlc.get("BTC") or [])]
    rows = []
    for name, family, gate, scale, extra in default_variants(
        ohlc, fng=fng, fund=fund, dates=dates, universe=universe
    ):
        rec = run_variant(
            ohlc, name=name, gate=gate, scale=scale, extra=extra, windows=windows, book_eur=book_eur
        )
        rec["family"] = family
        rows.append(rec)
        if log:
            parts = "  ".join(
                f"{tag} {rec[tag]['pnl_eur']:+9.0f} dd {rec[tag]['max_dd_pct'] * 100:4.1f}"
                for tag, _, _ in windows
            )
            log(f"{name:<14} {family:<11} {parts}")
    return rows

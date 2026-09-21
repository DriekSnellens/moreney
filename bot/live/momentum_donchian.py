"""Daily Donchian long sleeve (loop mix). Coin-agnostic.

Entry: BTC > SMA50 and the *completed* UTC daily high breaks the prior N-day high.
Exit: completed-bar low breaks the prior exit_n-day low, or Friday-flatten
after that Friday's UTC close (same as the loop sim — not Friday 00:00).
Clips are 40% of sleeve equity (cash + deployed), capped by cash.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE

# Candle: ts, o, h, l, c, v
Candle = Sequence[float]


@dataclass(frozen=True)
class DonchianConfig:
    name: str
    title: str
    channel: int = 10
    exit_n: int = 5
    friday_flatten: bool = False
    btc_sma: int = 50
    max_pos: int = 2
    weight: float = 0.4
    fee_rt: float = 0.003
    min_notional_eur: float = 50.0
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    tick_sec: float = 30.0
    # Once per day, just after the UTC daily bar closes (matches sim close fills).
    decision_hours_utc: tuple[int, ...] = (0,)


@dataclass
class DonchianPosition:
    base: str
    entry_price: float
    notional_eur: float
    opened_ms: int
    holding_id: str = ""
    entry_reason: str = ""
    sleeve: str = ""
    venue: str = "paper"
    quantity: float = 0.0

    def __post_init__(self) -> None:
        if not self.holding_id:
            self.holding_id = f"dc-{self.sleeve or 'x'}-{self.base}-{uuid.uuid4().hex[:8]}"
        if self.quantity <= 0 and self.entry_price > 0:
            self.quantity = self.notional_eur / self.entry_price

    def is_paper(self) -> bool:
        return str(self.venue or "paper").lower() in {"paper", "", "synthetic"}

    def long_return(self, mark: float) -> float:
        if self.entry_price <= 0 or mark <= 0:
            return 0.0
        return mark / self.entry_price - 1.0

    def unrealized_net(self, mark: float, fee_rt: float) -> float:
        ret = self.long_return(mark)
        return self.notional_eur * ret - self.notional_eur * (fee_rt / 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DonchianPosition:
        return cls(
            base=str(raw["base"]),
            entry_price=float(raw["entry_price"]),
            notional_eur=float(raw["notional_eur"]),
            opened_ms=int(raw["opened_ms"]),
            holding_id=str(raw.get("holding_id") or ""),
            entry_reason=str(raw.get("entry_reason") or ""),
            sleeve=str(raw.get("sleeve") or ""),
            venue=str(raw.get("venue") or "paper"),
            quantity=float(raw.get("quantity") or 0.0),
        )


def _sma(closes: Sequence[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(float(x) for x in closes[-period:]) / period


def _mom(closes: Sequence[float], lb: int) -> float:
    if len(closes) < lb + 1 or closes[-1 - lb] <= 0:
        return 0.0
    return float(closes[-1]) / float(closes[-1 - lb]) - 1.0


def btc_long_ok(btc_closes: Sequence[float], cfg: DonchianConfig) -> tuple[bool, dict[str, Any]]:
    sma = _sma(btc_closes, cfg.btc_sma)
    last = float(btc_closes[-1]) if btc_closes else 0.0
    ok = sma is not None and last > sma
    return ok, {"btc": last, "sma": sma, "btc_ok": ok, "sma_n": cfg.btc_sma}


def utc_day_start_ms(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(day.timestamp() * 1000)


def _ts_ms(ts: int) -> int:
    """Bitvavo daily opens are ms; tolerate accidental second-scale stamps."""
    t = int(ts)
    return t * 1000 if 0 < t < 10_000_000_000 else t


def drop_incomplete_daily(
    ohlc_by_base: Mapping[str, Sequence[Candle]],
    *,
    now: datetime | None = None,
) -> dict[str, list[Candle]]:
    """Keep bars whose open is before today's UTC 00:00 (completed 1d candles)."""
    cutoff = utc_day_start_ms(now)
    out: dict[str, list[Candle]] = {}
    for base, rows in ohlc_by_base.items():
        kept: list[Candle] = []
        for row in rows:
            try:
                ts = _ts_ms(int(row[0]))
            except (TypeError, ValueError, IndexError):
                continue
            if ts < cutoff:
                kept.append(row)
        out[str(base)] = kept
    return out


def completed_bar_weekday(
    ohlc_by_base: Mapping[str, Sequence[Candle]],
    *,
    now: datetime | None = None,
) -> int | None:
    """UTC weekday of the latest completed daily bar, or None."""
    filtered = drop_incomplete_daily(ohlc_by_base, now=now)
    last_ts = -1
    for rows in filtered.values():
        if not rows:
            continue
        try:
            last_ts = max(last_ts, _ts_ms(int(rows[-1][0])))
        except (TypeError, ValueError, IndexError):
            continue
    if last_ts < 0:
        return None
    return datetime.fromtimestamp(last_ts / 1000, UTC).weekday()


def weekend_flatten(
    *,
    now: datetime | None = None,
    signal_weekday: int | None = None,
) -> bool:
    """Fri/Sat/Sun of the *completed* daily bar (sim: flatten at Friday close).

    Wall-clock Friday is still Friday's session — do not flatten then.
    If the signal bar is unknown, only Sat/Sun wall-clock is a flatten
    backup. Weekdays with no bars must not look like a trading day (that
    was the Monday live-kick: buy, then dump when Sunday's bar arrived).
    """
    if signal_weekday is not None:
        return int(signal_weekday) >= 4
    return friday_close_reached(now)


def _blocked(
    *,
    btc_meta: Mapping[str, Any],
    signal_wd: int | None,
    risk_block: str,
    reasons: list[str] | None = None,
    exits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    why = list(reasons or [risk_block])
    return {
        "ok": False,
        "btc": dict(btc_meta),
        "exits": list(exits or []),
        "entries": [],
        "rejected": [],
        "reasons": why,
        "risk_block": risk_block,
        "signal_weekday": signal_wd,
    }


def friday_close_reached(now: datetime | None = None) -> bool:
    """Intraday safety net: Friday's UTC daily bar has closed (Sat or Sun)."""
    return (now or datetime.now(UTC)).weekday() >= 5


def evaluate_donchian(
    ohlc_by_base: Mapping[str, Sequence[Candle]],
    btc_closes: Sequence[float],
    cfg: DonchianConfig,
    *,
    held: set[str],
    cash_eur: float,
    deployed_eur: float = 0.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure daily decision on *completed* UTC bars. Last in-progress day is ignored."""
    now = now or datetime.now(UTC)
    ohlc = drop_incomplete_daily(ohlc_by_base, now=now)
    ohlc_btc = ohlc.get("BTC") or []
    # Prefer the long close series for SMA; a short BTC OHLC fetch must not
    # wipe SMA to null and look like btc_below_sma.
    if len(ohlc_btc) >= int(cfg.btc_sma):
        btc_closes = [float(r[4]) for r in ohlc_btc]
    signal_wd = completed_bar_weekday(ohlc, now=now)
    btc_ok, btc_meta = btc_long_ok(btc_closes, cfg)
    exits: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reasons: list[str] = []

    if signal_wd is None:
        if cfg.friday_flatten and friday_close_reached(now):
            reasons.append("friday_flatten")
            for base in held:
                exits.append({"base": base, "reason": "friday_flatten"})
            return _blocked(
                btc_meta=btc_meta,
                signal_wd=signal_wd,
                risk_block="friday_flatten",
                reasons=reasons,
                exits=exits,
            )
        return _blocked(
            btc_meta=btc_meta,
            signal_wd=signal_wd,
            risk_block="data_not_ready",
        )

    if cfg.friday_flatten and weekend_flatten(now=now, signal_weekday=signal_wd):
        reasons.append("friday_flatten")
        for base in held:
            exits.append({"base": base, "reason": "friday_flatten"})
        return _blocked(
            btc_meta=btc_meta,
            signal_wd=signal_wd,
            risk_block="friday_flatten",
            reasons=reasons,
            exits=exits,
        )

    for base in held:
        rows = ohlc.get(base) or []
        if len(rows) < cfg.exit_n + 1:
            continue
        prior_lows = [float(r[3]) for r in rows[-cfg.exit_n - 1 : -1]]
        today_low = float(rows[-1][3])
        if prior_lows and today_low <= min(prior_lows):
            exits.append({"base": base, "reason": "channel_low"})

    held_after = set(held) - {e["base"] for e in exits}
    if btc_meta.get("sma") is None:
        reasons.append("sma_unavailable")
        return _blocked(
            btc_meta=btc_meta,
            signal_wd=signal_wd,
            risk_block="sma_unavailable",
            reasons=reasons,
            exits=exits,
        )
    if not btc_ok:
        reasons.append("btc_below_sma")
        return _blocked(
            btc_meta=btc_meta,
            signal_wd=signal_wd,
            risk_block="btc_below_sma",
            reasons=reasons,
            exits=exits,
        )

    open_slots = max(0, int(cfg.max_pos) - len(held_after))
    if open_slots <= 0:
        reasons.append("slots_full")
        return {
            "ok": True,
            "btc": btc_meta,
            "exits": exits,
            "entries": entries,
            "rejected": rejected,
            "reasons": reasons,
            "risk_block": "",
            "signal_weekday": signal_wd,
        }

    cands: list[tuple[float, str]] = []
    for base in cfg.universe:
        if base in held_after:
            continue
        rows = ohlc.get(base) or []
        need = cfg.channel + 1
        if len(rows) < need:
            rejected.append({"base": base, "reason": "short_history"})
            continue
        prior_highs = [float(r[2]) for r in rows[-cfg.channel - 1 : -1]]
        today_high = float(rows[-1][2])
        if not prior_highs or today_high <= max(prior_highs):
            rejected.append({"base": base, "reason": "no_breakout"})
            continue
        closes = [float(r[4]) for r in rows]
        cands.append((_mom(closes, cfg.channel), base))
    cands.sort(reverse=True)

    sleeve_eq = max(float(cash_eur) + max(float(deployed_eur), 0.0), 0.0)
    cash_left = float(cash_eur)
    for mom, base in cands:
        if len(entries) >= open_slots:
            break
        notional = min(sleeve_eq * float(cfg.weight), cash_left * 0.95)
        if notional < cfg.min_notional_eur:
            rejected.append({"base": base, "reason": "notional_too_small"})
            continue
        entries.append(
            {
                "base": base,
                "notional_eur": round(notional, 2),
                "mom": round(mom, 4),
                "reasons": [f"breakout_{cfg.channel}", f"mom={mom:.3f}"],
            }
        )
        cash_left -= notional
    return {
        "ok": True,
        "btc": btc_meta,
        "exits": exits,
        "entries": entries,
        "rejected": rejected[:12],
        "reasons": reasons or ["breakout_scan"],
        "risk_block": "",
        "candidates": [{"base": b, "mom": round(m, 4)} for m, b in cands[:8]],
        "signal_weekday": signal_wd,
        "sleeve_eq_eur": round(sleeve_eq, 2),
    }


def loop_sleeve_configs() -> tuple[DonchianConfig, ...]:
    return (
        DonchianConfig(
            name="donch_fri10",
            title="Donchian 10/5 Friday-flat",
            channel=10,
            exit_n=5,
            friday_flatten=True,
        ),
        DonchianConfig(
            name="donch10",
            title="Donchian 10/5",
            channel=10,
            exit_n=5,
            friday_flatten=False,
        ),
        DonchianConfig(
            name="donch_fri",
            title="Donchian 20/10 Friday-flat",
            channel=20,
            exit_n=10,
            friday_flatten=True,
        ),
    )

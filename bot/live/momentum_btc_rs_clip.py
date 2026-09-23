"""BTC-core + 25% weekly RS clip — independent book beside the mix.

75% BTC while close > SMA50; at most 25% in one liquid alt if 20d skip-1
excess vs BTC ≥ 8%. Weekly rebalance. No per-coin hardcodes.
Paper until ``momentum_btc_rs_clip_allow_live`` arms venue fills.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE

FEE_RT = 0.003
SLIP = 0.001


@dataclass(frozen=True)
class ClipConfig:
    book_eur: float = 20_000.0
    btc_frac: float = 0.75
    alt_frac: float = 0.25
    excess_floor: float = 0.08
    lookback_days: int = 20
    skip_days: int = 1
    rebalance_days: int = 7
    sma_n: int = 50
    min_qvol_eur: float = 80_000.0
    min_notional_eur: float = 50.0
    fee_rt: float = FEE_RT
    slip: float = SLIP
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    decision_hours_utc: tuple[int, ...] = (0,)
    tick_sec: float = 30.0
    ohlc_days: int = 120


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
        }
    risk_on = last > s50
    equity = max(0.0, float(cash_eur) + float(deployed_eur))
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
        }

    reb_ms = int(cfg.rebalance_days) * 86_400_000
    rebalance_due = last_rebalance_ms <= 0 or (now_ms - last_rebalance_ms) >= reb_ms or not held

    ranked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for base in cfg.universe:
        rows = completed_ohlc(ohlc_by_base.get(base) or [], now=now)
        cl = closes_of(rows)
        xs = rs_excess(cl, btc_c, lb=cfg.lookback_days, skip=cfg.skip_days)
        qv = quote_vol(rows)
        if xs is None:
            skipped.append({"base": base, "reason": "short_history"})
            continue
        if qv < cfg.min_qvol_eur:
            skipped.append(
                {
                    "base": base,
                    "reason": "thin_volume",
                    "qvol": round(qv, 0),
                    "excess": round(xs, 4),
                }
            )
            continue
        ranked.append({"base": base, "excess": xs, "qvol": round(qv, 0)})
    ranked.sort(key=lambda r: float(r["excess"]), reverse=True)
    want_alt: str | None = None
    if rebalance_due and ranked and float(ranked[0]["excess"]) >= cfg.excess_floor:
        want_alt = str(ranked[0]["base"])
    elif not rebalance_due:
        want_alt = held_alt

    if held_alt and held_alt != want_alt:
        exits.append(
            {"base": held_alt, "reason": "rs_rotate" if want_alt else "rs_drop", "role": "alt"}
        )

    cash_left = float(cash_eur)
    # Conservative: assume exits free cash after they fill; entries size from equity.
    if not held_btc:
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
                        f"excess={float(top.get('excess') or 0):.3f}",
                        f"frac={cfg.alt_frac:.2f}",
                        "weekly_rs",
                    ],
                }
            )

    alt_txt = want_alt or "geen alt"
    caption = (
        f"Clip: {int(cfg.btc_frac * 100)}% BTC boven SMA{cfg.sma_n}, "
        f"{int(cfg.alt_frac * 100)}% {alt_txt}"
        + (f" (excess {ranked[0]['excess']:+.1%})" if want_alt and ranked else "")
        + ". Telt niet mee in live mix-equity."
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
    }

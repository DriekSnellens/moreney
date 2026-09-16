"""Tape-confirmed entry candidates — coin-agnostic relative-strength leaders.

When AlphaI publishes no actionable picks (headline-quiet day) the desk must
not idle through an alt-rotation tape. This module ranks the liquid universe
purely from live tape: 24h return vs BTC (excess), distance from the 24h high
(no knife-catching / no chasing a blow-off), EUR volume (liquidity floor) and
sector breadth (share of universe beating BTC). Never a per-coin rule.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_BITVAVO_24H_URL = "https://api.bitvavo.com/v2/ticker/24h"


@dataclass(frozen=True, slots=True)
class TapeRow:
    base: str
    ret_pct: float
    from_high_pct: float
    range_pct: float
    volume_eur: float


@dataclass(frozen=True, slots=True)
class TapeLeader:
    base: str
    ret_pct: float
    excess_btc_pp: float
    from_high_pct: float
    volume_eur: float
    score: float


@dataclass(frozen=True, slots=True)
class TapeSnapshot:
    fetched_ts: float
    btc_ret_pct: float | None
    breadth: float
    universe_n: int
    leaders: tuple[TapeLeader, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def leader_bases(self) -> frozenset[str]:
        return frozenset(ld.base for ld in self.leaders)

    def age_sec(self, now: float | None = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.fetched_ts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fetched_ts": round(self.fetched_ts, 1),
            "age_sec": round(self.age_sec(), 1),
            "btc_ret_pct": None if self.btc_ret_pct is None else round(self.btc_ret_pct, 3),
            "breadth": round(self.breadth, 3),
            "universe_n": self.universe_n,
            "leaders": [
                {
                    "base": ld.base,
                    "ret_pct": round(ld.ret_pct, 2),
                    "excess_btc_pp": round(ld.excess_btc_pp, 2),
                    "from_high_pct": round(ld.from_high_pct, 2),
                    "volume_eur": round(ld.volume_eur, 0),
                    "score": round(ld.score, 3),
                }
                for ld in self.leaders
            ],
            "reasons": list(self.reasons),
        }


def parse_bitvavo_24h(
    payload: Iterable[Mapping[str, Any]], bases: Iterable[str]
) -> dict[str, TapeRow]:
    """Bitvavo ``/ticker/24h`` rows → per-base tape rows for ``BASE-EUR`` markets."""
    wanted = {str(b or "").upper() for b in bases if str(b or "").strip()}
    out: dict[str, TapeRow] = {}
    for row in payload:
        market = str(row.get("market") or "")
        if not market.endswith("-EUR"):
            continue
        base = market[:-4].upper()
        if wanted and base not in wanted:
            continue
        try:
            open_px = float(row.get("open") or 0)
            last = float(row.get("last") or 0)
            high = float(row.get("high") or 0)
            low = float(row.get("low") or 0)
            vol = float(row.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if open_px <= 0 or last <= 0 or high <= 0:
            continue
        out[base] = TapeRow(
            base=base,
            ret_pct=(last / open_px - 1.0) * 100.0,
            from_high_pct=(last / high - 1.0) * 100.0,
            range_pct=((high / low - 1.0) * 100.0) if low > 0 else 0.0,
            volume_eur=vol * last,
        )
    return out


def fetch_bitvavo_24h(bases: Iterable[str], *, timeout_sec: float = 8.0) -> dict[str, TapeRow]:
    """One public call for the whole universe (no per-base candle loops)."""
    req = Request(_BITVAVO_24H_URL, headers={"User-Agent": "moreney-tape-confirm"})
    try:
        with urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode())
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("TAPE_24H_FETCH_FAIL err=%s", exc)
        return {}
    if not isinstance(payload, list):
        return {}
    return parse_bitvavo_24h(payload, bases)


def rank_tape_leaders(
    rows: Mapping[str, TapeRow],
    *,
    btc_base: str = "BTC",
    min_excess_pp: float = 2.0,
    min_ret_pct: float = 1.0,
    min_volume_eur: float = 500_000.0,
    max_from_high_pct: float = 3.0,
    min_breadth: float = 0.50,
    top_n: int = 4,
    exclude: Iterable[str] = (),
    now_ts: float | None = None,
) -> TapeSnapshot:
    """Rank tape leaders; empty when breadth says the sector is weak.

    * ``excess`` = base 24h % − BTC 24h % (relative strength, not absolute pump)
    * ``from_high`` guards against buying a name already fading from its high
    * ``breadth`` = share of universe (ex-BTC) beating BTC; low breadth → no
      leaders (a lone pump on a red tape is not a rotation).
    """
    ts = float(now_ts if now_ts is not None else time.time())
    btc_key = str(btc_base or "BTC").upper()
    excl = {str(b or "").upper() for b in exclude}
    btc = rows.get(btc_key)
    btc_ret = btc.ret_pct if btc is not None else None
    reasons: list[str] = []
    universe = [r for b, r in rows.items() if b != btc_key]
    n = len(universe)
    if n == 0:
        return TapeSnapshot(ts, btc_ret, 0.0, 0, (), ("empty_universe",))
    if btc_ret is None:
        reasons.append("no_btc_reference")
        breadth = sum(1 for r in universe if r.ret_pct > 0) / n
    else:
        breadth = sum(1 for r in universe if r.ret_pct > btc_ret) / n
    if breadth < float(min_breadth):
        reasons.append("breadth_weak")
        return TapeSnapshot(ts, btc_ret, breadth, n, (), tuple(reasons))

    leaders: list[TapeLeader] = []
    for r in universe:
        if r.base in excl:
            continue
        excess = r.ret_pct - (btc_ret if btc_ret is not None else 0.0)
        if excess < float(min_excess_pp):
            continue
        if r.ret_pct < float(min_ret_pct):
            continue
        if r.volume_eur < float(min_volume_eur):
            continue
        if r.from_high_pct < -abs(float(max_from_high_pct)):
            continue
        # Score: relative strength, penalised by fade from high; liquidity bonus.
        score = excess + r.from_high_pct * 0.5 + min(2.0, r.volume_eur / 5_000_000.0)
        leaders.append(
            TapeLeader(
                base=r.base,
                ret_pct=r.ret_pct,
                excess_btc_pp=excess,
                from_high_pct=r.from_high_pct,
                volume_eur=r.volume_eur,
                score=score,
            )
        )
    leaders.sort(key=lambda ld: -ld.score)
    if not leaders:
        reasons.append("no_leaders")
    else:
        reasons.append("leaders")
        if breadth >= 0.65:
            reasons.append("breadth_strong")
    top = tuple(leaders[: max(0, int(top_n))])
    return TapeSnapshot(ts, btc_ret, breadth, n, top, tuple(reasons))

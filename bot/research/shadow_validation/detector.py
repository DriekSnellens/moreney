"""Live L1 detector for the frozen cross-venue dislocation parent.

Same rule as CrossVenueDislocationFamily: fire when |mid_a - mid_b| / mid_a
>= dislocation_bps / 10000. No extra gates. No quote_age filter (H-0005 remains
REJECT_AS_INCREMENTAL_FILTER).
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.research.shadow_validation.books import CompactL1
from bot.research.shadow_validation.protocol import DISLOCATION_BPS, ROUTE, VENUE_A, VENUE_B


@dataclass(slots=True, frozen=True)
class ShadowSignal:
    symbol: str
    route: str
    venue_a: str
    venue_b: str
    a_rich: bool
    dislocation: float
    threshold: float
    entry_venue: str
    entry_side: str  # SELL (hit bid) or BUY (lift ask)
    hedge_venue: str
    hedge_side: str
    entry: CompactL1
    hedge: CompactL1


def detect_signal(
    l1_a: CompactL1,
    l1_b: CompactL1,
    *,
    dislocation_bps: float = DISLOCATION_BPS,
) -> ShadowSignal | None:
    if l1_a.mid <= 0.0 or l1_b.mid <= 0.0:
        return None
    dis = (l1_a.mid - l1_b.mid) / l1_a.mid
    thr = float(dislocation_bps) / 10000.0
    if abs(dis) < thr:
        return None
    a_rich = dis > 0.0
    # A rich → sell A (hit bid), buy B (lift ask). A cheap → buy A, sell B.
    if a_rich:
        entry_venue, entry_side = VENUE_A, "SELL"
        hedge_venue, hedge_side = VENUE_B, "BUY"
        entry, hedge = l1_a, l1_b
    else:
        entry_venue, entry_side = VENUE_A, "BUY"
        hedge_venue, hedge_side = VENUE_B, "SELL"
        entry, hedge = l1_a, l1_b
    return ShadowSignal(
        symbol=l1_a.symbol,
        route=ROUTE,
        venue_a=VENUE_A,
        venue_b=VENUE_B,
        a_rich=a_rich,
        dislocation=dis,
        threshold=thr,
        entry_venue=entry_venue,
        entry_side=entry_side,
        hedge_venue=hedge_venue,
        hedge_side=hedge_side,
        entry=entry,
        hedge=hedge,
    )

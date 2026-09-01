"""Conservative research ↔ live opportunity matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from bot.research.live_vs_research_attribution.lifecycle import MatchClass
from bot.research.live_vs_research_attribution.loaders import LiveFillRecord, base_from_symbol

_ZERO = Decimal("0")


@dataclass
class ResearchOpportunity:
    opportunity_id: str
    ts: datetime | None
    symbol: str
    base: str
    buy_venue: str | None
    sell_venue: str | None
    side: str | None
    expected_net: Decimal | None
    gross_edge: Decimal | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MatchedRecord:
    live_fill_id: str | None
    research_id: str | None
    match_class: MatchClass
    live_ts: datetime | None
    research_ts: datetime | None
    symbol: str | None
    base: str | None
    venue: str | None
    side: str | None
    research_expected_net: Decimal | None
    live_notional_eur: Decimal | None
    match_reason: str
    attribution_gap: Decimal | None = None
    realized_net: Decimal | None = None


def _parse_research_ts(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _d(v: object) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def research_from_economic_parity(rows: list[dict[str, Any]]) -> list[ResearchOpportunity]:
    out: list[ResearchOpportunity] = []
    for row in rows:
        sym = str(row.get("symbol", "")).upper()
        route = str(row.get("route", ""))
        parts = route.split("|") if route else []
        out.append(
            ResearchOpportunity(
                opportunity_id=str(row.get("candidate_id", "")),
                ts=_parse_research_ts(row.get("timestamp")),
                symbol=sym,
                base=base_from_symbol(sym),
                buy_venue=parts[0] if len(parts) >= 2 else None,
                sell_venue=parts[1] if len(parts) >= 2 else None,
                side=None,
                expected_net=_d(row.get("research_expected_net")),
                gross_edge=_d(row.get("research_expected_gross")),
                source="economic_parity",
                raw=row,
            )
        )
    return out


def research_from_shadow(rows: list[dict[str, Any]]) -> list[ResearchOpportunity]:
    out: list[ResearchOpportunity] = []
    for row in rows:
        sym = str(row.get("symbol") or row.get("A_SIGNAL", {}).get("symbol") or "")
        b = row.get("B_EXPECTED_ECONOMICS") or {}
        out.append(
            ResearchOpportunity(
                opportunity_id=str(row.get("candidate_id", "")),
                ts=_parse_research_ts(row.get("signal_time_ms")),
                symbol=sym.upper() if sym else "",
                base=base_from_symbol(sym) if sym else "",
                buy_venue=None,
                sell_venue=None,
                side=str((row.get("A_SIGNAL") or {}).get("entry_side") or "") or None,
                expected_net=_d(b.get("expected_net")),
                gross_edge=_d(b.get("expected_gross")),
                source="shadow_validation",
                raw=row,
            )
        )
    return out


_MATCH_PRIORITY = {
    MatchClass.EXACT_MATCH: 0,
    MatchClass.PROBABLE_MATCH: 1,
    MatchClass.POSSIBLE_MATCH: 2,
    MatchClass.NO_MATCH: 3,
}


def match_live_to_research(
    live_fills: list[LiveFillRecord],
    research: list[ResearchOpportunity],
    *,
    exact_window_sec: float = 2.0,
    probable_window_sec: float = 30.0,
    possible_window_sec: float = 300.0,
) -> list[MatchedRecord]:
    """Conservative matcher: prefers NO_MATCH over false positives."""
    results: list[MatchedRecord] = []
    used_research: set[str] = set()

    for fill in live_fills:
        best: MatchedRecord | None = None
        fill_base = base_from_symbol(fill.symbol)

        for opp in research:
            if opp.opportunity_id in used_research:
                continue
            if opp.base and opp.base != fill_base:
                continue
            if opp.ts is None:
                continue
            delta = abs((fill.ts - opp.ts).total_seconds())
            venue_ok = (
                opp.buy_venue is None
                or opp.sell_venue is None
                or fill.venue.lower() in {opp.buy_venue.lower(), opp.sell_venue.lower()}
            )
            if not venue_ok:
                continue

            if delta <= exact_window_sec:
                cls = MatchClass.EXACT_MATCH
                reason = f"symbol+venue+ts≤{exact_window_sec}s"
            elif delta <= probable_window_sec:
                cls = MatchClass.PROBABLE_MATCH
                reason = f"symbol+venue+ts≤{probable_window_sec}s"
            elif delta <= possible_window_sec:
                cls = MatchClass.POSSIBLE_MATCH
                reason = f"symbol+ts≤{possible_window_sec}s"
            else:
                continue

            rec = MatchedRecord(
                live_fill_id=fill.event_id,
                research_id=opp.opportunity_id,
                match_class=cls,
                live_ts=fill.ts,
                research_ts=opp.ts,
                symbol=fill.symbol,
                base=fill_base,
                venue=fill.venue,
                side=fill.side,
                research_expected_net=opp.expected_net,
                live_notional_eur=fill.notional_eur,
                match_reason=reason,
            )
            if best is None or _MATCH_PRIORITY[cls] < _MATCH_PRIORITY[best.match_class]:
                best = rec

        if best is None:
            results.append(
                MatchedRecord(
                    live_fill_id=fill.event_id,
                    research_id=None,
                    match_class=MatchClass.NO_MATCH,
                    live_ts=fill.ts,
                    research_ts=None,
                    symbol=fill.symbol,
                    base=fill_base,
                    venue=fill.venue,
                    side=fill.side,
                    research_expected_net=None,
                    live_notional_eur=fill.notional_eur,
                    match_reason="no research candidate within window",
                )
            )
        else:
            if best.research_id:
                used_research.add(best.research_id)
            results.append(best)

    return results


def match_summary(matches: list[MatchedRecord]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for m in matches:
        counts[m.match_class.value] = counts.get(m.match_class.value, 0) + 1
    return {
        "total_live_fills": len(matches),
        "by_class": counts,
        "exact_or_probable": counts.get(MatchClass.EXACT_MATCH.value, 0)
        + counts.get(MatchClass.PROBABLE_MATCH.value, 0),
        "no_match": counts.get(MatchClass.NO_MATCH.value, 0),
        "note": (
            "Live micro (maker_inventory alt-beta) and research (cross_venue_dislocation) "
            "use different universes; low match rate is expected, not a bug."
        ),
    }

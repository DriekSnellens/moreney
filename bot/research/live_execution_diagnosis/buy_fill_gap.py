"""Analyze buy vs sell fill asymmetry in live micro session audit."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

_ZERO = Decimal("0")


@dataclass
class SideStatusRow:
    venue: str
    side: str
    status: str
    count: int


@dataclass
class BuyFillGapReport:
    micro_results_total: int = 0
    by_venue_side_status: list[SideStatusRow] = field(default_factory=list)
    buy_submitted: int = 0
    buy_filled: int = 0
    buy_pending: int = 0
    buy_cancelled: int = 0
    sell_submitted: int = 0
    sell_filled: int = 0
    sell_pending: int = 0
    sell_cancelled: int = 0
    filled_sell_notional_eur: Decimal = _ZERO
    submitted_buy_notional_eur: Decimal = _ZERO
    filled_symbols: list[tuple[str, int]] = field(default_factory=list)
    order_blocked_top: list[tuple[str, int]] = field(default_factory=list)
    bridge_skips_top: list[tuple[str, int]] = field(default_factory=list)
    bridge_live_fill_count: int | None = None
    bridge_backfill_count: int | None = None
    live_maker: bool | None = None
    root_causes: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _iter_audit(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def analyze_buy_fill_gap(
    audit_path: Path,
    *,
    session_status_path: Path | None = None,
    bridge_state_path: Path | None = None,
) -> BuyFillGapReport:
    report = BuyFillGapReport()
    vss: Counter[tuple[str, str, str]] = Counter()
    blocked: Counter[str] = Counter()
    filled_sym: Counter[str] = Counter()

    for evt in _iter_audit(audit_path):
        t = evt.get("type") or evt.get("event_type")
        if t == "order_blocked":
            p = evt.get("payload") or {}
            reason = str(p.get("reason") or p.get("message") or "unknown")[:120]
            blocked[reason] += 1
        if t != "micro_order_result":
            continue
        report.micro_results_total += 1
        p = evt.get("payload") or {}
        venue = str(p.get("venue") or "unknown")
        side = str(p.get("side") or "unknown")
        status = str(p.get("status") or "unknown")
        vss[(venue, side, status)] += 1
        try:
            notional = Decimal(str(p.get("notional_eur") or 0))
        except Exception:
            notional = _ZERO
        if side == "buy":
            if status == "submitted":
                report.buy_submitted += 1
                report.submitted_buy_notional_eur += notional
            elif status == "filled":
                report.buy_filled += 1
            elif status == "pending":
                report.buy_pending += 1
            elif status == "cancelled":
                report.buy_cancelled += 1
        elif side == "sell":
            if status == "submitted":
                report.sell_submitted += 1
            elif status == "filled":
                report.sell_filled += 1
                report.filled_sell_notional_eur += notional
                filled_sym[p.get("symbol") or "?"] += 1
            elif status == "pending":
                report.sell_pending += 1
            elif status == "cancelled":
                report.sell_cancelled += 1

    report.by_venue_side_status = [
        SideStatusRow(venue=k[0], side=k[1], status=k[2], count=v)
        for k, v in sorted(vss.items(), key=lambda x: -x[1])
    ]
    report.filled_symbols = filled_sym.most_common(15)
    report.order_blocked_top = blocked.most_common(10)

    session = _load_json(session_status_path) if session_status_path else None
    bridge = _load_json(bridge_state_path) if bridge_state_path else None
    bridge_info = (session or {}).get("bridge") or bridge or {}
    report.bridge_live_fill_count = bridge_info.get("live_fill_count")
    report.bridge_backfill_count = bridge_info.get("backfill_mirrored_count")
    report.live_maker = bridge_info.get("live_maker")
    skips = bridge_info.get("skips") or {}
    report.bridge_skips_top = sorted(
        ((str(k), int(v)) for k, v in skips.items()),
        key=lambda x: -x[1],
    )[:12]

    # Root cause synthesis (analysis only)
    if report.buy_filled == 0 and report.sell_filled > 0:
        report.root_causes.append(
            {
                "id": "MAKER_BUY_RESTING",
                "severity": "HIGH",
                "summary": (
                    f"{report.buy_submitted} Bitvavo buys logged as submitted (resting maker), "
                    f"0 buy fills in micro_order_result audit; {report.sell_filled} sells filled."
                ),
            }
        )
    if report.buy_pending > 0:
        report.root_causes.append(
            {
                "id": "OKX_BUY_STUCK_PENDING",
                "severity": "HIGH",
                "summary": (
                    f"{report.buy_pending} OKX buy results stuck in pending — "
                    "OKX path not producing exchange fills in this window."
                ),
            }
        )
    max_open_blocked = sum(
        n
        for reason, n in blocked.items()
        if "max open orders" in reason.lower()
    )
    if max_open_blocked > 100:
        report.root_causes.append(
            {
                "id": "MAX_OPEN_ORDERS",
                "severity": "MEDIUM",
                "summary": (
                    "Hundreds of order_blocked events (max open orders) — "
                    "failed OKX submits and resting buys consume capacity."
                ),
            }
        )
    if report.bridge_backfill_count and report.bridge_live_fill_count:
        if int(report.bridge_backfill_count) >= int(report.bridge_live_fill_count):
            report.root_causes.append(
                {
                    "id": "INVENTORY_FROM_BACKFILL",
                    "severity": "MEDIUM",
                    "summary": (
                        f"Bridge shows {report.bridge_live_fill_count} live fills with "
                        f"{report.bridge_backfill_count} backfill-mirrored — "
                        "sells likely exit pre-session / backfilled inventory, not session buys."
                    ),
                },
            )

    report.notes.extend(
        [
            "micro_order_result logs the initial micro_engine response; resting buy fills "
            "mirrored via manage_resting_orders are not re-audited as filled buys.",
            "With live_maker=true, buys are post-only limits (resting); sells cross when "
            "bid >= break-even or on cut-loss taker path.",
            "Paper portfolio (live_micro_fullbot_state) may show €0 realized because "
            "_mirror_exchange_trade updates bridge FIFO only, not paper _fills.apply.",
        ]
    )
    return report

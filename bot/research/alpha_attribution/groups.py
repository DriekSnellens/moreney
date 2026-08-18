"""Canonical replay economics for parent / retained / excluded groups."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from bot.research.accounting.audit import audit_canonical
from bot.research.accounting.protocol import WATERFALL_TOLERANCE
from bot.research.accounting.waterfall import estimated_fill_count, from_attached_events

_ZERO = Decimal("0")


def group_economics(
    events: Sequence[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
    label: str,
    parent_signals: int | None = None,
    parent_net: Decimal | None = None,
) -> dict[str, Any]:
    n = len(events)
    mean_fwd = (
        sum(float(e.get("forward") or 0.0) for e in events) / n if n else None
    )
    econ = from_attached_events(
        events,
        venue=venue,
        venue_exit=venue_exit,
        mean_forward=mean_fwd,
        audit={"candidates": n, "admitted": n, "rejected": 0},
    )
    acc = audit_canonical(econ)
    parent_n = parent_signals if parent_signals is not None else n
    pnet = parent_net if parent_net is not None else econ.replay_net.value
    share_sig = (n / parent_n) if parent_n else None
    share_net = (econ.replay_net.value / pnet) if pnet else None
    return {
        "GROUP": label,
        "signal_count": econ.signals.value,
        "estimated_fills": econ.fills.value,
        "gross_eur": str(econ.gross.value),
        "fees_eur": str(econ.fees.value),
        "slippage_eur": str(econ.slippage.value),
        "adverse_eur": str(econ.adverse.value),
        "other_costs_eur": str(econ.other_costs.value),
        "replay_net_eur": str(econ.replay_net.value),
        "replay_net_per_signal": (
            None if n == 0 else str(econ.replay_net_per_signal.value)
        ),
        "replay_net_per_fill": (
            None if econ.replay_net_per_fill is None else str(econ.replay_net_per_fill.value)
        ),
        "share_of_parent_signals": None if share_sig is None else float(share_sig),
        "share_of_parent_net": None if share_net is None else str(share_net),
        "world": "EXECUTION_REPLAY",
        "ACCOUNTING_AUDIT": acc["ACCOUNTING_AUDIT"],
        "canonical": econ.report_block(),
        "excluded_economically_positive": (
            econ.replay_net.value > 0 if label == "EXCLUDED_BY_CHILD" else None
        ),
    }


def assert_parent_identity(
    parent: dict[str, Any],
    retained: dict[str, Any],
    excluded: dict[str, Any],
    *,
    unsupported_net: Decimal = _ZERO,
    unsupported_n: int = 0,
) -> list[str]:
    issues: list[str] = []
    p_n = int(parent["signal_count"])
    r_n = int(retained["signal_count"])
    e_n = int(excluded["signal_count"])
    if p_n != r_n + e_n + unsupported_n:
        issues.append(
            f"parent signals {p_n} != retained {r_n} + excluded {e_n} + unsupported {unsupported_n}"
        )
    p_net = Decimal(str(parent["replay_net_eur"]))
    r_net = Decimal(str(retained["replay_net_eur"]))
    e_net = Decimal(str(excluded["replay_net_eur"]))
    if abs(p_net - (r_net + e_net + unsupported_net)) > WATERFALL_TOLERANCE:
        issues.append(
            f"parent net {p_net} != retained {r_net} + excluded {e_net} + unsupported {unsupported_net}"
        )
    return issues


def _replay_value(block: dict[str, Any], key: str) -> Decimal:
    world = (block or {}).get("EXECUTION_REPLAY_WORLD") or {}
    item = world.get(key) or {}
    if isinstance(item, dict) and "value" in item:
        return Decimal(str(item["value"]))
    if item in (None, {}):
        return _ZERO
    return Decimal(str(item))


def _group_from_sums(
    *,
    label: str,
    signals: int,
    fills: int,
    gross: Decimal,
    fees: Decimal,
    slippage: Decimal,
    adverse: Decimal,
    other: Decimal,
    net: Decimal,
    parent_signals: int,
    parent_net: Decimal,
    positive_windows: int,
    negative_windows: int,
) -> dict[str, Any]:
    n = int(signals)
    fill_n = int(fills)
    share_sig = (n / parent_signals) if parent_signals else None
    share_net = (net / parent_net) if parent_net else None
    return {
        "GROUP": label,
        "signal_count": n,
        "estimated_fills": fill_n,
        "gross_eur": str(gross),
        "fees_eur": str(fees),
        "slippage_eur": str(slippage),
        "adverse_eur": str(adverse),
        "other_costs_eur": str(other),
        "replay_net_eur": str(net),
        "replay_net_per_signal": None if n == 0 else str(net / Decimal(n)),
        "replay_net_per_fill": None if fill_n == 0 else str(net / Decimal(fill_n)),
        "share_of_parent_signals": None if share_sig is None else float(share_sig),
        "share_of_parent_net": None if share_net is None else str(share_net),
        "world": "EXECUTION_REPLAY",
        "ACCOUNTING_AUDIT": "PASS",
        "source": "published_paired_windows",
        "positive_windows": positive_windows,
        "negative_windows": negative_windows,
        "excluded_economically_positive": net > 0 if label == "EXCLUDED_BY_CHILD" else None,
    }


def groups_from_stored_paired_windows(windows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Canonical group waterfalls for the published paired universe. Do not rewrite nets."""
    p_sig = c_sig = p_fill = c_fill = 0
    p_gross = p_fees = p_slip = p_adv = p_oth = p_net = _ZERO
    c_gross = c_fees = c_slip = c_adv = c_oth = c_net = _ZERO
    p_pos = p_neg = c_pos = c_neg = e_pos = e_neg = 0
    n_complete = 0
    for w in windows:
        if not w.get("complete", True):
            continue
        n_complete += 1
        parent = w.get("parent") or {}
        child = w.get("child") or {}
        p_sig += int(w.get("parent_signals") or parent.get("SIGNALS") or 0)
        c_sig += int(w.get("child_signals") or child.get("SIGNALS") or 0)
        p_fill += int(w.get("parent_fills") or parent.get("FILLS") or 0)
        c_fill += int(w.get("child_fills") or child.get("FILLS") or 0)
        pg = _replay_value(parent, "replay_gross_eur")
        pf = _replay_value(parent, "replay_fees_eur")
        ps = _replay_value(parent, "replay_slippage_eur")
        pa = _replay_value(parent, "replay_adverse_eur")
        po = _replay_value(parent, "replay_other_costs_eur")
        pn = _replay_value(parent, "replay_net_eur") or Decimal(str(w.get("parent_replay_net") or 0))
        cg = _replay_value(child, "replay_gross_eur")
        cf = _replay_value(child, "replay_fees_eur")
        cs = _replay_value(child, "replay_slippage_eur")
        ca = _replay_value(child, "replay_adverse_eur")
        co = _replay_value(child, "replay_other_costs_eur")
        cn = _replay_value(child, "replay_net_eur") or Decimal(str(w.get("child_replay_net") or 0))
        p_gross += pg
        p_fees += pf
        p_slip += ps
        p_adv += pa
        p_oth += po
        p_net += pn
        c_gross += cg
        c_fees += cf
        c_slip += cs
        c_adv += ca
        c_oth += co
        c_net += cn
        en = pn - cn
        if pn > 0:
            p_pos += 1
        elif pn < 0:
            p_neg += 1
        if cn > 0:
            c_pos += 1
        elif cn < 0:
            c_neg += 1
        if en > 0:
            e_pos += 1
        elif en < 0:
            e_neg += 1
    e_sig = p_sig - c_sig
    e_fill = estimated_fill_count(e_sig)
    parent_g = _group_from_sums(
        label="ALL_PARENT",
        signals=p_sig,
        fills=p_fill,
        gross=p_gross,
        fees=p_fees,
        slippage=p_slip,
        adverse=p_adv,
        other=p_oth,
        net=p_net,
        parent_signals=p_sig,
        parent_net=p_net or Decimal("1"),
        positive_windows=p_pos,
        negative_windows=p_neg,
    )
    retained_g = _group_from_sums(
        label="RETAINED_BY_CHILD",
        signals=c_sig,
        fills=c_fill,
        gross=c_gross,
        fees=c_fees,
        slippage=c_slip,
        adverse=c_adv,
        other=c_oth,
        net=c_net,
        parent_signals=p_sig,
        parent_net=p_net or Decimal("1"),
        positive_windows=c_pos,
        negative_windows=c_neg,
    )
    excluded_g = _group_from_sums(
        label="EXCLUDED_BY_CHILD",
        signals=e_sig,
        fills=e_fill,
        gross=p_gross - c_gross,
        fees=p_fees - c_fees,
        slippage=p_slip - c_slip,
        adverse=p_adv - c_adv,
        other=p_oth - c_oth,
        net=p_net - c_net,
        parent_signals=p_sig,
        parent_net=p_net or Decimal("1"),
        positive_windows=e_pos,
        negative_windows=e_neg,
    )
    parent_g["n_complete_windows"] = n_complete
    return {
        "ALL_PARENT": parent_g,
        "RETAINED_BY_CHILD": retained_g,
        "EXCLUDED_BY_CHILD": excluded_g,
    }

"""Paired-delta plausibility audit. Do not silently rewrite the published delta."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from bot.research.accounting.protocol import WATERFALL_TOLERANCE

_ZERO = Decimal("0")


def _d(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return _ZERO
    if isinstance(value, dict) and "value" in value:
        return Decimal(str(value["value"]))
    return Decimal(str(value))


def audit_paired_windows(
    windows: Sequence[dict[str, Any]],
    *,
    reported_aggregate_delta: Any,
    complete_only: bool = True,
) -> dict[str, Any]:
    """Prove identities. FAIL rather than adjust the published number."""
    issues: list[str] = []
    rows: list[dict[str, Any]] = []
    sum_delta = _ZERO
    sum_parent = _ZERO
    sum_child = _ZERO
    sum_excl = _ZERO
    n_complete = 0
    sign_convention = "paired_delta_eur = child_replay_net_eur - parent_replay_net_eur"

    for w in windows:
        complete = bool(w.get("complete", True))
        parent_n = int(w.get("parent_signals") or w.get("parent_signal_count") or 0)
        child_n = int(w.get("child_signals") or w.get("child_signal_count") or 0)
        parent_f = int(w.get("parent_fills") or w.get("parent_fill_count") or 0)
        child_f = int(w.get("child_fills") or w.get("child_fill_count") or 0)
        parent_net = _d(w.get("parent_replay_net") or w.get("parent_replay_net_eur"))
        child_net = _d(w.get("child_replay_net") or w.get("child_replay_net_eur"))
        delta = _d(w.get("delta") or w.get("paired_delta_eur"))
        shared_net = _d(w.get("shared_signal_net") or w.get("retained_signal_net_eur"))
        excl_net = _d(w.get("excluded_signal_net") or w.get("excluded_signal_net_eur"))
        child_only_n = int(w.get("child_only_signals") or 0)
        parent_only_n = int(w.get("parent_only_signals") or 0)
        shared_n = int(w.get("shared_signals") or child_n)
        wid = str(w.get("window") or w.get("window_id") or "?")

        want_delta = child_net - parent_net
        if abs(delta - want_delta) > WATERFALL_TOLERANCE:
            issues.append(
                f"{wid}: paired_delta {delta} != child-parent {want_delta}"
            )
        parent_from_parts = shared_net + excl_net
        if abs(parent_net - parent_from_parts) > WATERFALL_TOLERANCE:
            issues.append(
                f"{wid}: parent_replay_net {parent_net} != shared+excluded {parent_from_parts}"
            )
        # Pure filter: child_only net is 0, child == retained/shared.
        if child_only_n != 0:
            issues.append(f"{wid}: child_only_signals={child_only_n} (expected 0 for pure filter)")
        if abs(child_net - shared_net) > WATERFALL_TOLERANCE:
            issues.append(
                f"{wid}: child_replay_net {child_net} != shared/retained {shared_net}"
            )
        if shared_n + parent_only_n != parent_n and parent_n:
            # Allow off-by if ids collide; still record.
            if abs((shared_n + parent_only_n) - parent_n) > 0:
                issues.append(
                    f"{wid}: shared({shared_n})+parent_only({parent_only_n}) != parent({parent_n})"
                )

        row = {
            "window_id": wid,
            "complete": complete,
            "parent_signal_count": parent_n,
            "child_signal_count": child_n,
            "parent_fill_count": parent_f,
            "child_fill_count": child_f,
            "parent_replay_net_eur": str(parent_net),
            "child_replay_net_eur": str(child_net),
            "paired_delta_eur": str(delta),
            "retained_signal_net_eur": str(shared_net),
            "excluded_signal_net_eur": str(excl_net),
            "shared_signals": shared_n,
            "parent_only_signals": parent_only_n,
            "child_only_signals": child_only_n,
            "sign_convention": sign_convention,
        }
        rows.append(row)
        if complete or not complete_only:
            n_complete += 1 if complete else 0
            if complete:
                sum_delta += delta
                sum_parent += parent_net
                sum_child += child_net
                sum_excl += excl_net

    reported = _d(reported_aggregate_delta)
    if abs(sum_delta - reported) > WATERFALL_TOLERANCE:
        issues.append(
            f"SUM(window paired deltas)={sum_delta} != reported_aggregate_delta={reported}"
        )
    if abs(sum_parent - (sum_child + sum_excl)) > WATERFALL_TOLERANCE:
        issues.append(
            f"SUM(parent)={sum_parent} != SUM(child)+SUM(excluded)={sum_child + sum_excl}"
        )

    verdict = "FAIL" if issues else "PASS"
    return {
        "PAIRED_DELTA_ACCOUNTING_AUDIT": verdict,
        "sign_convention": sign_convention,
        "n_windows": len(rows),
        "n_complete_windows": n_complete,
        "sum_window_paired_deltas_eur": str(sum_delta),
        "reported_aggregate_delta_eur": str(reported),
        "sum_parent_replay_net_eur": str(sum_parent),
        "sum_child_replay_net_eur": str(sum_child),
        "sum_excluded_signal_net_eur": str(sum_excl),
        "issues": issues,
        "windows": rows,
        "note": (
            "FAIL is terminal for this audit: the published paired delta is not rewritten. "
            "Negative delta means the child underperformed the parent on canonical replay."
        ),
    }

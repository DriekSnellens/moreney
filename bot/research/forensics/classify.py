"""Predeclared concentration class A–F. Does not rescue rejected strategies."""

from __future__ import annotations

from typing import Any

from bot.research.forensics.buckets import (
    DOMINANCE_SHARE,
    LOO_DOMINANCE_SHARE,
    MIN_BLOCKS_WITH_SIGNALS,
    MIN_SIGNALS_FOR_CLASS,
    NULL_EXTREME_ALPHA,
    REGIME_SHARE,
)

CLASSES = (
    "RANDOM_CONCENTRATION",
    "SYMBOL_SPECIFIC",
    "VENUE_SPECIFIC",
    "TIME_SPECIFIC",
    "REGIME_DEPENDENT",
    "INSUFFICIENT_EVIDENCE",
)

ACTIONS = {
    "RANDOM_CONCENTRATION": "REJECT strategy family. No new hypothesis.",
    "SYMBOL_SPECIFIC": "Create a NEW symbol-specific hypothesis. Do not modify the rejected strategy.",
    "VENUE_SPECIFIC": "Create a NEW venue-specific hypothesis. Do not modify the rejected strategy.",
    "TIME_SPECIFIC": "Investigate market conditions. Do NOT turn it into a time filter.",
    "REGIME_DEPENDENT": "Create a NEW regime-gated hypothesis using only pre-trade features.",
    "INSUFFICIENT_EVIDENCE": "Collect more data. Do not claim a mechanism.",
}


def _share(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.0
    v = row.get("share")
    try:
        return abs(float(v or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _loo_flip_dominant(loo: dict[str, Any], *, min_share: float) -> dict[str, Any] | None:
    full = float(loo.get("FULL_RESULT") or 0.0)
    for row in loo.get("rows") or []:
        grp = abs(float(row.get("group_NET") or 0.0))
        share = (grp / abs(full)) if full else 0.0
        if row.get("sign_flip") and share >= min_share:
            return row
        if share >= DOMINANCE_SHARE and row.get("sign_flip"):
            return row
    return None


def classify(
    *,
    n_signals: int,
    blocks_with_signals: int,
    top: dict[str, Any],
    loo: dict[str, Any],
    regimes: dict[str, Any],
    nulls: dict[str, Any],
    tournament_top_route_share: float | None,
) -> dict[str, Any]:
    notes: list[str] = []
    tautology = bool(top.get("route_share_tautology"))
    if tautology:
        notes.append(
            "ROUTE_SHARE_TAUTOLOGY: frozen params select a single venue/pair, "
            "so tournament top_route_share=1 by construction. Not used as VENUE_SPECIFIC."
        )
        notes.append(
            f"tournament_top_route_share={tournament_top_route_share}"
        )

    if n_signals < MIN_SIGNALS_FOR_CLASS or blocks_with_signals < MIN_BLOCKS_WITH_SIGNALS:
        return _out(
            "INSUFFICIENT_EVIDENCE",
            "too few signals or chronological blocks",
            notes,
            structural=False,
        )

    # B. symbol
    top_sym_share = _share(top.get("top_symbol"))
    loo_sym = _loo_flip_dominant(loo.get("symbol") or {}, min_share=LOO_DOMINANCE_SHARE)
    if top_sym_share >= DOMINANCE_SHARE or loo_sym:
        why = (
            f"top symbol {(top.get('top_symbol') or {}).get('group')} "
            f"share={top_sym_share:.3f}"
        )
        if loo_sym:
            why += f"; leave-out {loo_sym.get('left_out')} flips sign"
        return _out("SYMBOL_SPECIFIC", why, notes, structural=True)

    # C. venue — only if multiple routes exist in the event set
    n_routes = int(top.get("n_routes") or 0)
    top_route_share = _share(top.get("top_venue_pair"))
    loo_route = _loo_flip_dominant(loo.get("venue_pair") or {}, min_share=LOO_DOMINANCE_SHARE)
    if n_routes >= 2 and (top_route_share >= DOMINANCE_SHARE or loo_route):
        why = f"top venue pair share={top_route_share:.3f}"
        return _out("VENUE_SPECIFIC", why, notes, structural=True)

    # D. time block
    top_block_share = _share(top.get("top_chrono_block"))
    loo_block = _loo_flip_dominant(loo.get("chrono_block") or {}, min_share=LOO_DOMINANCE_SHARE)
    if top_block_share >= DOMINANCE_SHARE or loo_block:
        why = (
            f"top chrono block {(top.get('top_chrono_block') or {}).get('group')} "
            f"share={top_block_share:.3f}"
        )
        return _out("TIME_SPECIFIC", why, notes, structural=False)

    # E. regime with pre-trade structural difference
    for name, block in (regimes or {}).items():
        if not isinstance(block, dict):
            continue
        share = abs(float(block.get("share_of_total_net") or 0.0))
        if block.get("structural") and share >= REGIME_SHARE:
            feats = block.get("structural_features") or []
            why = (
                f"regime {name} focus={block.get('focus_group')} "
                f"share={share:.3f} features={feats}"
            )
            return _out("REGIME_DEPENDENT", why, notes, structural=True)

    # A vs F via nulls
    p_sym = float((nulls or {}).get("p_permute_signal_top_symbol") or 1.0)
    p_block = float((nulls or {}).get("p_rotate_chrono_top_block") or 1.0)
    if (nulls or {}).get("feasible") and min(p_sym, p_block) >= NULL_EXTREME_ALPHA:
        return _out(
            "RANDOM_CONCENTRATION",
            f"concentration not extreme vs null (p_sym={p_sym:.3f}, p_block={p_block:.3f})",
            notes,
            structural=False,
        )

    return _out(
        "INSUFFICIENT_EVIDENCE",
        "no single group/regime meets dominance with a pre-trade structural contrast",
        notes,
        structural=False,
    )


def _out(cls: str, why: str, notes: list[str], *, structural: bool) -> dict[str, Any]:
    return {
        "CONCENTRATION_CLASS": cls,
        "CONCENTRATION_SOURCE": why,
        "STRUCTURAL_FEATURE_FOUND": "YES" if structural else "NO",
        "RECOMMENDED_ACTION": ACTIONS[cls],
        "notes": notes,
        "parent_remains": "REJECTED",
    }

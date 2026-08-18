"""RESEARCH_OBSERVATION objects. Never auto-create strategies or OOS thresholds."""

from __future__ import annotations

from typing import Any


def observation(
    *,
    title: str,
    finding: str,
    feature: str | None,
    pre_trade: bool,
    economic_contribution: str | None,
    stability: str | None,
    usefulness: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "type": "RESEARCH_OBSERVATION",
        "title": title,
        "finding": finding,
        "feature": feature,
        "pre_trade_available": pre_trade,
        "economic_contribution": economic_contribution,
        "window_stability": stability,
        "candidate_hypothesis_usefulness": usefulness,
        "auto_strategy": False,
        "affects_production": False,
        "modifies_h0005": False,
        "resurrects_h0007": False,
        "oos_threshold_created": False,
        "future_hypothesis_requires": [
            "pre-trade feature",
            "causal availability",
            "sufficient economic contribution",
            "stability evidence",
            "DEV-only threshold definition",
            "fresh unseen OOS data",
        ],
    }
    if extra:
        row.update(extra)
    return row


def ranked_observations(
    *,
    excluded_positive: bool,
    excluded_net: str,
    retained_net: str,
    parent_net: str,
    top_contexts: list[dict[str, Any]],
    feature_diffs: list[dict[str, Any]],
    dependency: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    out.append(
        observation(
            title="Freshness gate drops positive parent replay mass",
            finding=(
                "On the paired universe, excluded (stale) parent signals have "
                f"canonical replay net {excluded_net}. Retained net is {retained_net}. "
                f"Parent net is {parent_net}. "
                "H-0005 underperformed because it filtered economically positive trades, "
                "not because it uniquely captured a better subset in aggregate EUR."
            ),
            feature="quote_age_ms",
            pre_trade=True,
            economic_contribution=excluded_net,
            stability=None,
            usefulness=(
                "HIGH_FORENSIC — explains the paired delta. Not a new threshold. "
                "Do not retune quote_age_ms on this OOS."
            ),
            extra={"excluded_economically_positive": excluded_positive},
        )
    )
    for ctx in top_contexts[:5]:
        out.append(
            observation(
                title=f"Descriptive context {ctx.get('context')}",
                finding=(
                    f"Context {ctx.get('context')} contribution {ctx.get('contribution_share')} "
                    f"replay_net={ctx.get('replay_net_eur')} stability={ctx.get('stability')} "
                    f"DESCRIPTIVE_ONLY."
                ),
                feature="named_context",
                pre_trade=True,
                economic_contribution=str(ctx.get("replay_net_eur")),
                stability=str(ctx.get("stability")),
                usefulness="DESCRIPTIVE — not a strategy. DEV-only definition required before any hypothesis.",
            )
        )
    for diff in feature_diffs[:8]:
        out.append(
            observation(
                title=f"Feature contrast {diff.get('feature')}={diff.get('bucket')}",
                finding=str(diff.get("note") or diff),
                feature=str(diff.get("feature")),
                pre_trade=bool(diff.get("pre_trade_available", True)),
                economic_contribution=str(diff.get("excluded_replay_net_eur")),
                stability=str(diff.get("stability") or ""),
                usefulness=str(diff.get("usefulness") or "FORENSIC"),
            )
        )
    dep = dependency.get("CONTEXT_DEPENDENCY")
    out.append(
        observation(
            title="Leave-one-context-out",
            finding=(
                f"CONTEXT_DEPENDENCY={dep}. Flagged={dependency.get('flagged_contexts')}. "
                "Not an automatic reject or promote."
            ),
            feature=None,
            pre_trade=True,
            economic_contribution=None,
            stability=None,
            usefulness="FORENSIC",
        )
    )
    return out

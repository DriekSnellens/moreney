"""Optional local-LLM advisory on a forensic summary. Never the judge."""

from __future__ import annotations

from typing import Any

from bot.research.llm.prompts import FORENSICS_ADVISORY_SYSTEM
from bot.research.llm.provider import ProviderError
from bot.research.llm.schemas import ForensicsAdvisory


def _summary(analyzed: dict[str, Any]) -> dict[str, Any]:
    slim = {}
    for sid, block in analyzed.items():
        slim[sid] = {
            "class": block.get("CONCENTRATION_CLASS"),
            "source": block.get("CONCENTRATION_SOURCE"),
            "structural": block.get("STRUCTURAL_FEATURE_FOUND"),
            "top": block.get("top_contributors"),
            "blocks": {
                "positive": (block.get("chrono_blocks") or {}).get("positive_blocks"),
                "negative": (block.get("chrono_blocks") or {}).get("negative_blocks"),
                "best": (block.get("chrono_blocks") or {}).get("best_block"),
                "worst": (block.get("chrono_blocks") or {}).get("worst_block"),
            },
            "nulls": block.get("null_checks"),
            "regimes": {
                k: {
                    "focus": v.get("focus_group"),
                    "share": v.get("share_of_total_net"),
                    "structural": v.get("structural"),
                    "features": v.get("structural_features"),
                }
                for k, v in (block.get("regime_explanation") or {}).items()
                if isinstance(v, dict)
            },
            "frozen_params": block.get("frozen_params"),
            "parent_verdict": block.get("parent_verdict"),
        }
    return slim


def maybe_llm_advisory(
    analyzed: dict[str, Any],
    *,
    enabled: bool = True,
    provider=None,
) -> dict[str, Any]:
    if not enabled:
        return {"used": "NO", "status": "DISABLED", "advisory": None}
    if provider is None:
        try:
            from bot.core.config import get_settings
            from bot.research.llm.ollama import build_provider_from_settings

            provider = build_provider_from_settings(get_settings())
        except Exception as exc:  # noqa: BLE001
            return {"used": "NO", "status": f"UNAVAILABLE:{exc}", "advisory": None}
    health = provider.health()
    if not health.available:
        return {
            "used": "NO",
            "status": health.status,
            "detail": health.detail,
            "advisory": None,
        }
    try:
        batch = provider.generate_structured(
            system_prompt=FORENSICS_ADVISORY_SYSTEM,
            context={
                "label": "CONCENTRATION_FORENSICS_SUMMARY",
                "authoritative_classes": {
                    sid: b.get("CONCENTRATION_CLASS") for sid, b in analyzed.items()
                },
                "summary": _summary(analyzed),
                "rules": [
                    "Deterministic classifier is the authority.",
                    "At most two hypotheses.",
                    "Do not retune for PnL.",
                    "Parents remain REJECTED.",
                ],
            },
            schema_model=ForensicsAdvisory,
        )
    except ProviderError as exc:
        return {"used": "NO", "status": f"UNAVAILABLE:{exc}", "advisory": None}
    return {
        "used": "YES",
        "status": health.status,
        "label": "ADVISORY_NON_AUTHORITATIVE",
        "advisory": batch.model_dump(),
    }

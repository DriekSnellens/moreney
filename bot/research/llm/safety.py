"""Safety invariants — LLM cannot touch production/trading paths."""

from __future__ import annotations

from typing import Any

FORBIDDEN_CONTEXT_KEYS_PRE_FREEZE = frozenset(
    {
        "untouched_oos_raw",
        "oos_raw_events",
        "oos_mids",
        "oos_forward_returns",
        "current_oos_values",
    }
)

FORBIDDEN_HYPOTHESIS_FIELDS = frozenset(
    {
        "python",
        "code",
        "shell",
        "sql",
        "import",
        "fee_override",
        "fill_override",
        "execution_enabled",
        "oos_override",
        "risk_override",
    }
)


def assert_no_shell_tools(tools: dict[str, Any] | None) -> None:
    if not tools:
        return
    banned = {"shell", "bash", "exec", "subprocess", "redis_write", "filesystem_write"}
    bad = banned.intersection(set(tools))
    if bad:
        raise RuntimeError(f"forbidden tools exposed to LLM: {sorted(bad)}")


def context_is_oos_blind(context: dict[str, Any]) -> bool:
    """True when context has no untouched OOS raw values (pre-evaluation)."""
    if FORBIDDEN_CONTEXT_KEYS_PRE_FREEZE.intersection(context):
        return False
    # Nested safety
    for key in FORBIDDEN_CONTEXT_KEYS_PRE_FREEZE:
        if key in json_dumps_keys(context):
            return False
    return True


def json_dumps_keys(obj: Any, *, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else str(k)
            keys.add(str(k))
            keys.add(full)
            keys |= json_dumps_keys(v, prefix=full)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            keys |= json_dumps_keys(v, prefix=f"{prefix}[{i}]")
    return keys


def strip_forbidden_hypothesis_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in raw.items() if k not in FORBIDDEN_HYPOTHESIS_FIELDS}


def analysis_cannot_override_verdict(
    analysis: dict[str, Any],
    canonical_verdicts: dict[str, str],
) -> dict[str, str]:
    """Return canonical verdicts unchanged — analysis is non-authoritative."""
    _ = analysis
    return dict(canonical_verdicts)
